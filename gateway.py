"""
Шлюз: принимает запросы, выбирает движок согласно политике, записывает
фактические измерения.

Политика задаётся переменной окружения POLICY и меняется без правки
кода, чтобы все политики сравнивались в абсолютно одинаковых условиях —
на том же шлюзе, том же клиенте и тех же движках.

Шлюзу доступно ровно то, что доступно обычному обратному прокси:
маршрут, значение параметра запроса, размер тела и собственный счётчик
запросов, отправленных каждому движку и ещё не завершённых. Ни характер
работы (вычисления или ожидание ввода-вывода), ни фактическая
длительность запроса заранее не известны.

Каждый обработанный запрос дописывается в CSV: признаки на момент
решения, выбранный движок и измеренная длительность.
"""
from __future__ import annotations

import csv
import os
import queue
import sys
import threading
import time
from pathlib import Path

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

sys.path.insert(0, str(Path(__file__).resolve().parent))

from core.cost_model import ConstantCost, build_estimator
from core.policies import (
    WORKERS,
    AdaptiveOccupancyPolicy,
    AdaptiveBudgetPolicy,
    BlockingBudgetPolicy,
    HealthAdaptiveBudgetPolicy,
    ExploringPolicy,
    FixedWorkerPolicy,
    LeastConnectionsPolicy,
    LeastOccupancyPolicy,
    Policy,
    RandomPolicy,
    RequestFeatures,
    RoundRobinPolicy,
    build_static_rule,
)
from core.queue_state import QueueTracker
from core.workload import ENDPOINTS

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"

WORKER_URLS = {
    "sync": "http://127.0.0.1:8201/process",
    "async": "http://127.0.0.1:8202/process",
    "process": "http://127.0.0.1:8203/process",
}

POLICY_NAME = os.environ.get("POLICY", "least_conn")
LOG_PATH = Path(os.environ.get("GATEWAY_LOG", DATA_DIR / "requests_log.csv"))

LOG_FIELDS = (
    ["timestamp", "policy", "endpoint", "param", "payload_bytes"]
    + [f"inflight_{w}" for w in WORKERS]
    + [f"work_{w}" for w in WORKERS]
    + ["worker", "latency_ms", "error"]
)

# Соответствие имени политики и источника оценки стоимости. Правило
# выбора у всех этих политик одно и то же, различается только оценка —
# ради этого набор и построен.
COST_POLICIES = {
    "work_endpoint": "endpoint_mean",
    "work_linear": "linear_param",
    "work_power": "power_law",
    "work_neural": "neural",
    "work_measured": "measured",
}

app = FastAPI()

tracker: QueueTracker | None = None
http_client: httpx.AsyncClient | None = None
policy: Policy | None = None

log_queue: "queue.Queue" = queue.Queue()


def _log_worker() -> None:
    """Пишет измерения в CSV из отдельного потока, не задерживая запросы."""
    DATA_DIR.mkdir(exist_ok=True)
    # Схема журнала зависит от набора движков. Если существующий файл
    # написан с другим набором колонок, дописывать в него нельзя:
    # значения разъедутся относительно заголовка, и последующий разбор
    # даст молча неверные числа.
    new_file = True
    if LOG_PATH.exists():
        with open(LOG_PATH, newline="", encoding="utf-8") as fh:
            header = fh.readline().strip().split(",")
        new_file = header != LOG_FIELDS
        if new_file:
            LOG_PATH.unlink()
    with open(LOG_PATH, "a", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=LOG_FIELDS)
        if new_file:
            writer.writeheader()
            fh.flush()
        while True:
            entry = log_queue.get()
            if entry is None:
                break
            writer.writerow(entry)
            fh.flush()


def build_policy(name: str) -> tuple[Policy, object]:
    """
    Возвращает политику и оценку стоимости, которой взвешивается очередь.

    Очередь взвешивается тем же источником, что использует политика:
    политика с грубой оценкой одинаково грубо и взвешивает очередь, и
    оценивает новый запрос — ровно так, как это произошло бы в реальной
    системе, где другого источника у неё просто нет.
    """
    # budget_<оценка>_<порог в мс>: классификатор допускает запрос в
    # асинхронный движок, только если предсказанная блокировка не
    # превышает порога.
    # split_<оценка для допуска>_<оценка для взвешивания>_<порог>:
    # разводит две роли оценки по разным источникам, чтобы выяснить,
    # какая из них определяет результат.
    if name.startswith("split_"):
        _, admit, weight, budget = name.split("_", 3)
        admit_est = build_estimator(COST_POLICIES[f"work_{admit}"])
        weight_est = build_estimator(COST_POLICIES[f"work_{weight}"])
        policy = BlockingBudgetPolicy(admit_est, float(budget), name=name,
                                      weight_estimator=weight_est)
        return policy, weight_est
    if (name.startswith("budget_") or name.startswith("adabudget_")
            or name.startswith("health_")
            or name.startswith("healthfull_")):
        prefix, source, budget = name.split("_", 2)
        estimator = build_estimator(COST_POLICIES[f"work_{source}"])
        budget_ms = float("inf") if budget == "inf" else float(budget)
        if prefix == "healthfull":
            return (HealthAdaptiveBudgetPolicy(estimator, budget_ms,
                                               name=name, rank_full=True),
                    estimator)
        factory = {"adabudget": AdaptiveBudgetPolicy,
                   "health": HealthAdaptiveBudgetPolicy,
                   "budget": BlockingBudgetPolicy}[prefix]
        return factory(estimator, budget_ms, name=name), estimator
    if name in COST_POLICIES:
        estimator = build_estimator(COST_POLICIES[name])
        return LeastOccupancyPolicy(estimator, name=name), estimator
    if name.startswith("adaptive_"):
        estimator = build_estimator(COST_POLICIES.get(
            name.replace("adaptive_", ""), "endpoint_mean"))
        return AdaptiveOccupancyPolicy(estimator, name=name), estimator
    if name == "static_rule":
        return build_static_rule(), ConstantCost()

    constant = ConstantCost()
    if name.startswith("all_"):
        return FixedWorkerPolicy(name[len("all_"):]), constant
    if name == "random":
        return RandomPolicy(), constant
    if name == "round_robin":
        return RoundRobinPolicy(), constant
    if name == "least_conn":
        return LeastConnectionsPolicy(), constant
    if name == "explore":
        base, estimator = build_policy("work_endpoint")
        return ExploringPolicy(base, epsilon=0.35), estimator
    raise ValueError(f"Неизвестная политика: {name}")


@app.on_event("startup")
async def startup() -> None:
    global http_client, policy, tracker
    policy, estimator = build_policy(POLICY_NAME)
    tracker = QueueTracker(WORKERS, estimator)
    http_client = httpx.AsyncClient(
        limits=httpx.Limits(max_connections=800, max_keepalive_connections=800),
        timeout=300.0,
    )
    threading.Thread(target=_log_worker, daemon=True).start()


@app.on_event("shutdown")
async def shutdown() -> None:
    log_queue.put(None)
    if http_client:
        await http_client.aclose()


@app.post("/route")
async def route(request: Request):
    body = await request.json()
    endpoint = body.get("endpoint")
    param = body.get("param")
    spec = ENDPOINTS.get(endpoint)

    counts, work = tracker.snapshot()
    features = RequestFeatures(
        endpoint=endpoint,
        method="POST",
        payload_bytes=spec.payload_bytes if spec else 0,
        param=param,
        inflight=counts,
        pending_work=work,
        recent_latency=tracker.recent_latency(),
    )

    worker = policy.choose(features)
    token = tracker.add(worker, endpoint, param)

    start = time.perf_counter()
    error = ""
    payload: dict = {}
    try:
        resp = await http_client.post(
            WORKER_URLS[worker], json={"endpoint": endpoint, "param": param})
        resp.raise_for_status()
        payload = resp.json()
    except Exception as exc:
        error = type(exc).__name__
    finally:
        latency_ms = (time.perf_counter() - start) * 1000
        tracker.remove(worker, token)
        tracker.observe_latency(worker, latency_ms)

    entry = {
        "timestamp": time.time(), "policy": POLICY_NAME, "endpoint": endpoint,
        "param": param, "payload_bytes": features.payload_bytes,
        "worker": worker, "latency_ms": round(latency_ms, 3), "error": error,
    }
    for w in WORKERS:
        entry[f"inflight_{w}"] = counts.get(w, 0)
        entry[f"work_{w}"] = round(work.get(w, 0.0), 1)
    log_queue.put(entry)

    policy.observe(features, worker, latency_ms)

    if error:
        return JSONResponse({"status": "error", "error": error,
                             "worker": worker}, status_code=502)

    payload["routed_to"] = worker
    payload["gateway_latency_ms"] = round(latency_ms, 3)
    return JSONResponse(payload)


@app.get("/health")
async def health():
    counts, work = tracker.snapshot()
    payload = {"status": "healthy", "policy": POLICY_NAME,
               "workers": list(WORKERS), "inflight": counts,
               "pending_work_ms": work}
    # Поправочные коэффициенты адаптивной политики. Выставлены наружу
    # ради диагностики: без них нельзя отличить «поправка не помогает»
    # от «поправка не успела сойтись», а это разные выводы.
    correction = getattr(policy, "correction", None)
    if correction:
        payload["correction"] = {f"{e}|{w}": round(v, 2)
                                 for (e, w), v in correction.items()}
    return payload


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8300,
                log_level="warning", loop="uvloop", http="httptools")
