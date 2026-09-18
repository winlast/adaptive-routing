"""
Основной эксперимент: сравнение политик маршрутизации.

Все политики проверяются в идентичных условиях — тот же шлюз, те же
движки, та же последовательность запросов с теми же значениями
параметров, та же конкурентность. Между прогонами шлюз перезапускается,
чтобы состояние очередей не переносилось из предыдущего замера.

Сравнение построено двухступенчато.

Первая ступень отвечает на вопрос самой работы: даёт ли разделение
трафика между двумя движками что-нибудь по сравнению с развёртыванием на
одном. Для этого в сравнении участвуют `all_sync` и `all_async` — режимы,
в которых маршрутизации нет вовсе.

Вторая ступень отвечает на вопрос, ради которого нужна модель: сколько
даёт понимание того, насколько тяжёл конкретный запрос. Здесь все
политики используют одно и то же правило выбора и различаются только
источником оценки стоимости — от её полного отсутствия
(least-connections) до точной измеренной таблицы, недоступной в
работающей системе.

Значимость различий оценивается бутстрэпом по объединённой выборке
задержек: разброс между прогонами на одной машине достигает десятка
процентов, и без доверительных интервалов любые выводы были бы
произвольными.
"""
from __future__ import annotations

import asyncio
import json
import os
import random
import signal
import statistics
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx

from experiments.load_generator import (
    LIGHT_ENDPOINT, SLO_MS, build_plan, run_load,
)

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
RESULTS_PATH = DATA_DIR / "policy_comparison.json"

# Порядок важен: сначала развёртывание без маршрутизации, затем политики
# по возрастанию качества оценки стоимости запроса.
POLICIES = [x for x in os.environ.get("POLICIES", ",".join([
    "all_sync",
    "all_async",
    "round_robin",
    "least_conn",
    "static_rule",
    "work_endpoint",
    "work_linear",
    "work_power",
    "work_neural",
    "work_measured",
])).split(",") if x]

CONCURRENCY_LEVELS = [int(x) for x in
                      os.environ.get("LEVELS", "16,32").split(",")]
REQUESTS = int(os.environ.get("REQUESTS", "400"))
REPEATS = int(os.environ.get("REPEATS", "3"))
SEED = 777
BOOTSTRAP = 2000


def stop_gateway(proc: subprocess.Popen | None = None) -> None:
    """
    Останавливает шлюз по идентификатору процесса.

    Поиск по строке команды (pkill -f) здесь неприменим: под шаблон
    попадает и сам управляющий процесс, в командной строке которого
    встречается то же имя файла.
    """
    if proc is not None and proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=8)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)
    # Ждём освобождения порта, иначе следующий шлюз не поднимется.
    for _ in range(40):
        try:
            httpx.get("http://127.0.0.1:8300/health", timeout=1)
        except Exception:
            return
        time.sleep(0.25)


def start_gateway(policy: str) -> subprocess.Popen:
    env = dict(os.environ)
    env["POLICY"] = policy
    env["GATEWAY_LOG"] = str(DATA_DIR / "benchmark_requests_log.csv")
    proc = subprocess.Popen(
        [sys.executable, "gateway.py"], cwd=str(BASE_DIR), env=env,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    for _ in range(50):
        time.sleep(0.4)
        try:
            resp = httpx.get("http://127.0.0.1:8300/health", timeout=2)
            if resp.status_code == 200 and resp.json().get("policy") == policy:
                return proc
        except Exception:
            continue
    raise RuntimeError(f"Шлюз не поднялся с политикой {policy}")


def percentile(values: list[float], p: float) -> float:
    ordered = sorted(values)
    return ordered[min(int(len(ordered) * p), len(ordered) - 1)]


def bootstrap_ci(a: list[float], b: list[float], stat, n: int = BOOTSTRAP,
                 seed: int = 1) -> tuple[float, float]:
    """
    Доверительный интервал для разности статистик двух выборок.

    Выборки переразыгрываются с возвращением, для каждой пары считается
    разность; берутся 2.5-й и 97.5-й процентили полученного
    распределения. Интервал, не накрывающий ноль, означает, что различие
    не объясняется разбросом прогонов.
    """
    rng = random.Random(seed)
    diffs = []
    for _ in range(n):
        sa = [a[rng.randrange(len(a))] for _ in range(len(a))]
        sb = [b[rng.randrange(len(b))] for _ in range(len(b))]
        diffs.append(stat(sb) - stat(sa))
    diffs.sort()
    return diffs[int(n * 0.025)], diffs[int(n * 0.975)]


async def main() -> None:
    results: dict[str, dict] = {}
    raw: dict[str, dict[str, list[float]]] = {}

    for concurrency in CONCURRENCY_LEVELS:
        print(f"\n{'=' * 92}")
        print(f"Конкурентность {concurrency}, {REQUESTS} запросов, "
              f"{REPEATS} повтора")
        print("=" * 92)
        print(f"{'политика':<16}{'rps':>16}{'p95, мс':>10}{'p99, мс':>10}"
              f"{'лёгкие p95':>13}{'лёгкие p99':>13}{'SLO%':>8}")
        print("-" * 92)

        for policy in POLICIES:
            runs, all_lat, light_lat = [], [], []
            for repeat in range(REPEATS):
                proc = start_gateway(policy)
                try:
                    await run_load(4, 30, seed=1)  # прогрев
                    await asyncio.sleep(1.5)
                    res = await run_load(concurrency, REQUESTS,
                                         seed=SEED + repeat)
                    if "rps" not in res:
                        raise RuntimeError(
                            f"Политика {policy}: ни один запрос не прошёл "
                            f"({res})")
                    all_lat += res.pop("_latencies", [])
                    light_lat += res.pop("_light_latencies", [])
                    runs.append(res)
                finally:
                    stop_gateway(proc)
                await asyncio.sleep(1)
            raw.setdefault(policy, {})[str(concurrency)] = {
                "all": all_lat, "light": light_lat}

            rps = [r["rps"] for r in runs]
            agg = {
                "rps_mean": round(statistics.mean(rps), 2),
                "rps_std": round(statistics.stdev(rps), 2) if len(rps) > 1 else 0.0,
                "rps_runs": rps,
                "p95_mean": round(statistics.mean(r["p95_ms"] for r in runs), 1),
                "p99_mean": round(statistics.mean(r["p99_ms"] for r in runs), 1),
                "light_p95_mean": round(
                    statistics.mean(r["light_p95_ms"] for r in runs), 1),
                "light_p99_mean": round(
                    statistics.mean(r["light_p99_ms"] for r in runs), 1),
                "light_avg_mean": round(
                    statistics.mean(r["light_avg_ms"] for r in runs), 1),
                "slo_mean": round(
                    statistics.mean(r["slo_violation_rate"] for r in runs), 1),
                "errors": sum(r["errors"] for r in runs),
            }
            results.setdefault(policy, {})[str(concurrency)] = agg
            print(f"{policy:<16}{agg['rps_mean']:>10.1f} ±{agg['rps_std']:<5.1f}"
                  f"{agg['p95_mean']:>10.0f}{agg['p99_mean']:>10.0f}"
                  f"{agg['light_p95_mean']:>13.0f}"
                  f"{agg['light_p99_mean']:>13.0f}{agg['slo_mean']:>8.1f}",
                  flush=True)

    RESULTS_PATH.write_text(json.dumps(results, indent=2, ensure_ascii=False),
                            encoding="utf-8")
    print(f"\nРезультаты сохранены: {RESULTS_PATH}")


if __name__ == "__main__":
    asyncio.run(main())
