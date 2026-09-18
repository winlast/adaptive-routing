"""
Что происходит, когда условия меняются после профилирования.

Все оценки стоимости, включая обученную модель, получены один раз на
той системе, какой она была в момент замера. Это их общее слабое место,
и оно не теоретическое: на той же машине появляется соседний процесс,
движку достаётся меньше процессорного времени, и таблица, снятая вчера,
описывает вчерашнюю систему.

Эксперимент воспроизводит это прямо. В середине прогона на синхронном
движке запускается фоновая нагрузка, отбирающая у него процессорное
время. Асинхронного движка это не касается, поэтому меняется
относительный порядок движков — то есть оптимальное решение
действительно смещается, и политике есть к чему приспосабливаться.
Предыдущая версия эксперимента замедляла общую внешнюю зависимость,
отчего дорожали оба движка одинаково и приспосабливаться было не к чему;
это была ошибка постановки.

Сравниваются две политики, отличающиеся только наличием поправки:

  * с неизменными оценками — работает по таблице, снятой до ухудшения;
  * с поправкой по наблюдениям — те же оценки, умноженные на скользящее
    среднее отношения наблюдённой длительности к ожидаемой.

Проверяется утверждение: предвычисленные оценки устаревают, и
способность поправлять их на ходу важнее точности исходной таблицы.
"""
from __future__ import annotations

import asyncio
import json
import os
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx

from experiments.benchmark_policies import start_gateway, stop_gateway
from experiments.load_generator import run_load

BASE_DIR = Path(__file__).resolve().parent.parent
RESULTS_PATH = BASE_DIR / "data" / "drift_comparison.json"

SYNC_NOISE = "http://127.0.0.1:8201/noise"

SOURCE = os.environ.get("DRIFT_SOURCE", "neural")
BUDGET = os.environ.get("DRIFT_BUDGET", "60")
POLICIES = {
    f"budget_{SOURCE}_{BUDGET}": "оценки неизменны",
    f"adabudget_{SOURCE}_{BUDGET}": "оценки поправляются на ходу",
    "least_conn": "least-connections (оценок нет)",
}

CONCURRENCY = int(os.environ.get("CONCURRENCY", "32"))
BEFORE = int(os.environ.get("BEFORE", "250"))
AFTER = int(os.environ.get("AFTER", "450"))
REPEATS = int(os.environ.get("REPEATS", "3"))
NOISE_THREADS = int(os.environ.get("NOISE", "3"))


def set_noise(threads: int) -> None:
    httpx.post(SYNC_NOISE, params={"threads": threads}, timeout=10)


def summarize(values: list[float]) -> dict:
    ordered = sorted(values)

    def pct(p: float) -> float:
        return ordered[min(int(len(ordered) * p), len(ordered) - 1)]

    return {
        "avg": round(statistics.mean(values), 1),
        "p95": round(pct(0.95), 1),
        "slow_pct": round(sum(1 for v in values if v > 200) / len(values) * 100, 1),
    }


async def main() -> None:
    results: dict[str, dict] = {}

    print(f"Конкурентность {CONCURRENCY}. Фаза 1 — обычный режим, "
          f"фаза 2 — на синхронном движке {NOISE_THREADS} фоновых потока.\n")
    print(f"{'политика':<32}{'до: rps':>9}{'до: лёгкие>200':>16}"
          f"{'после: rps':>12}{'после: лёгкие>200':>19}{'падение rps':>13}")
    print("-" * 101)

    for policy, title in POLICIES.items():
        before_rps, after_rps = [], []
        before_light, after_light = [], []
        for repeat in range(REPEATS):
            set_noise(0)
            proc = start_gateway(policy)
            try:
                await run_load(4, 30, seed=1)
                res_before = await run_load(CONCURRENCY, BEFORE,
                                            seed=500 + repeat)
                set_noise(NOISE_THREADS)
                res_after = await run_load(CONCURRENCY, AFTER,
                                           seed=900 + repeat)
            finally:
                set_noise(0)
                stop_gateway(proc)
            before_rps.append(res_before["rps"])
            after_rps.append(res_after["rps"])
            before_light += res_before["_light_latencies"]
            after_light += res_after["_light_latencies"]
            await asyncio.sleep(2)

        b_rps = statistics.mean(before_rps)
        a_rps = statistics.mean(after_rps)
        b, a = summarize(before_light), summarize(after_light)
        drop = (1 - a_rps / b_rps) * 100
        results[policy] = {
            "title": title,
            "before": {"rps": round(b_rps, 2), **b},
            "after": {"rps": round(a_rps, 2), **a},
            "rps_drop_pct": round(drop, 1),
        }
        print(f"{title:<32}{b_rps:>9.1f}{b['slow_pct']:>15.1f}%"
              f"{a_rps:>12.1f}{a['slow_pct']:>18.1f}%{drop:>12.1f}%",
              flush=True)

    RESULTS_PATH.write_text(json.dumps(results, indent=2, ensure_ascii=False),
                            encoding="utf-8")
    print(f"\nСохранено: {RESULTS_PATH}")


if __name__ == "__main__":
    asyncio.run(main())
