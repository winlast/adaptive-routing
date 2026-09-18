"""
Шлюз: принимает запросы, выбирает воркера согласно политике, записывает
фактические измерения.

Политика задаётся переменной окружения POLICY и меняется без правки кода,
чтобы все политики сравнивались в абсолютно одинаковых условиях — на том
же шлюзе, том же клиенте и тех же воркерах.

Шлюз ведёт счётчики in-flight: сколько запросов отправлено каждому
воркеру и ещё не завершено. Это единственный источник информации о
загрузке, доступный обычному прокси без кооперации с воркерами, и именно
он передаётся политике как признак состояния системы.

Каждый обработанный запрос дописывается в CSV: признаки на момент
решения, выбранный воркер и фактическая длительность. Из этих записей
затем обучается модель, поэтому важно, что цель (латентность) измерена, а
не вычислена по формуле.
"""
from __future__ import annotations

import csv
import json
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

from core.policies import (
    WORKERS,
    HybridPolicy,
    AdaptiveWorkPolicy,
    LeastExpectedWorkPolicy,
    OnlineModelPolicy,
    LeastConnectionsPolicy,
    ModelPolicy,
    OraclePolicy,
    Policy,
    RandomPolicy,
    RequestFeatures,
    RoundRobinPolicy,
    build_static_rule_from_costs,
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

POLICY_NAME = os.environ.get("POLICY", "random")
LOG_PATH = Path(os.environ.get("GATEWAY_LOG", DATA_DIR / "requests_log.csv"))
COST_TABLE_PATH = DATA_DIR / "cost_table.json"

LOG_FIELDS = [
    "timestamp", "policy", "endpoint", "method", "payload_bytes",
    "inflight_sync", "inflight_async", "inflight_process",
    "work_sync", "work_async", "work_process",
    "worker", "latency_ms", "error",
]

app = FastAPI()

tracker: QueueTracker | None = None
http_client: httpx.AsyncClient | None = None
policy: Policy | None = None

log_queue: "queue.Queue" = queue.Queue()


def _log_worker() -> None:
    """Пишет измерения в CSV из отдельного потока, не задерживая запросы."""
    DATA_DIR.mkdir(exist_ok=True)
    new_file = not LOG_PATH.exists()
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


def load_cost_table() -> dict[str, dict[str, float]]:
    if COST_TABLE_PATH.exists():
        return json.loads(COST_TABLE_PATH.read_text(encoding="utf-8"))
    return {}


def build_policy(name: str) -> Policy:
    if name == "random":
        return RandomPolicy()
    if name == "round_robin":
        return RoundRobinPolicy()
    if name == "least_conn":
        return LeastConnectionsPolicy()
    if name == "least_work":
        return LeastExpectedWorkPolicy(load_cost_table())
    if name == "adaptive_work":
        return AdaptiveWorkPolicy(load_cost_table())
    if name == "online_model":
        from core.predictor import LatencyPredictor

        return OnlineModelPolicy(LatencyPredictor.load())
    if name == "static_rule":
        return build_static_rule_from_costs(load_cost_table())
    if name == "oracle":
        return OraclePolicy(load_cost_table())
    if name == "model":
        from core.predictor import LatencyPredictor

        return ModelPolicy(LatencyPredictor.load(), marginal=True)
    if name == "model_greedy":
        from core.predictor import LatencyPredictor

        return ModelPolicy(LatencyPredictor.load(), marginal=False)
    if name.startswith("hybrid"):
        from core.predictor import LatencyPredictor

        slack = int(name.split("_")[1]) if "_" in name else 2
        return HybridPolicy(LatencyPredictor.load(), slack=slack)
    raise ValueError(f"Неизвестная политика: {name}")


@app.on_event("startup")
async def startup() -> None:
    global http_client, policy, tracker
    tracker = QueueTracker(WORKERS, load_cost_table())
    policy = build_policy(POLICY_NAME)
    http_client = httpx.AsyncClient(
        limits=httpx.Limits(max_connections=600, max_keepalive_connections=600),
        timeout=180.0,
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
    spec = ENDPOINTS.get(endpoint)

    snapshot, work_snapshot = tracker.snapshot()

    features = RequestFeatures(
        endpoint=endpoint,
        method="POST",
        payload_bytes=spec.payload_bytes if spec else 0,
        inflight=snapshot,
        pending_work=work_snapshot,
    )

    worker = policy.choose(features)
    token = tracker.add(worker, endpoint)

    start = time.perf_counter()
    error = ""
    payload = {}
    try:
        resp = await http_client.post(WORKER_URLS[worker], json={"endpoint": endpoint})
        resp.raise_for_status()
        payload = resp.json()
    except Exception as exc:
        error = type(exc).__name__
    finally:
        latency_ms = (time.perf_counter() - start) * 1000
        tracker.remove(worker, token)

    log_queue.put({
        "timestamp": time.time(),
        "policy": POLICY_NAME,
        "endpoint": endpoint,
        "method": "POST",
        "payload_bytes": features.payload_bytes,
        "inflight_sync": snapshot.get("sync", 0),
        "inflight_async": snapshot.get("async", 0),
        "inflight_process": snapshot.get("process", 0),
        "work_sync": round(work_snapshot.get("sync", 0.0), 1),
        "work_async": round(work_snapshot.get("async", 0.0), 1),
        "work_process": round(work_snapshot.get("process", 0.0), 1),
        "worker": worker,
        "latency_ms": round(latency_ms, 3),
        "error": error,
    })

    policy.observe(features, worker, latency_ms)

    if error:
        return JSONResponse({"status": "error", "error": error, "worker": worker},
                            status_code=502)

    payload["routed_to"] = worker
    payload["gateway_latency_ms"] = round(latency_ms, 3)
    return JSONResponse(payload)


@app.get("/health")
async def health():
    counts, work = tracker.snapshot()
    return {"status": "healthy", "policy": POLICY_NAME,
            "inflight": counts, "pending_work_ms": work}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        app, host="127.0.0.1", port=8300,
        log_level="warning", loop="uvloop", http="httptools",
    )
