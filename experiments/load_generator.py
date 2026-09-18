"""
Генератор нагрузки на шлюз.

Состав трафика намеренно неравномерный: лёгкие запросы преобладают, а
тяжёлые встречаются редко — так устроен типичный веб-трафик, и именно
поэтому маршрутизация нетривиальна. Если бы все запросы были одинаково
частыми и одинаково тяжёлыми, оптимальная стратегия свелась бы к
равномерному распределению.

Модуль используется и для сбора обучающих данных, и для финального
сравнения политик, поэтому поток запросов для всех политик одинаков:
последовательность эндпоинтов детерминирована зерном.
"""
from __future__ import annotations

import asyncio
import random
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx

GATEWAY_URL = "http://127.0.0.1:8300/route"

# Доли запросов. Лёгкие эндпоинты доминируют, тяжёлые редки.
TRAFFIC_MIX = {
    "/api/user/profile": 0.35,
    "/api/feed": 0.20,
    "/api/search": 0.20,
    "/api/auth/verify": 0.10,
    "/api/report/generate": 0.10,
    "/api/image/thumbnail": 0.05,
}

SLO_MS = 500.0  # порог, выше которого запрос считается нарушившим SLO


def build_plan(total: int, seed: int = 20260918) -> list[str]:
    """Детерминированная последовательность запросов по заданным долям."""
    rng = random.Random(seed)
    endpoints = list(TRAFFIC_MIX)
    weights = [TRAFFIC_MIX[e] for e in endpoints]
    return rng.choices(endpoints, weights=weights, k=total)


async def _client_loop(client, plan_slice, results, errors):
    for endpoint in plan_slice:
        start = time.perf_counter()
        try:
            resp = await client.post(GATEWAY_URL, json={"endpoint": endpoint},
                                     timeout=300)
            resp.raise_for_status()
            results.append(((time.perf_counter() - start) * 1000, endpoint))
        except Exception as exc:
            errors.append(repr(exc))


async def run_load(concurrency: int, total: int, seed: int = 20260918) -> dict:
    plan = build_plan(total, seed)
    slices = [plan[i::concurrency] for i in range(concurrency)]

    results: list = []
    errors: list = []
    limits = httpx.Limits(max_connections=concurrency + 20,
                          max_keepalive_connections=concurrency + 20)

    async with httpx.AsyncClient(limits=limits) as client:
        start = time.perf_counter()
        await asyncio.gather(*[
            _client_loop(client, slices[i], results, errors)
            for i in range(concurrency)
        ])
        wall = time.perf_counter() - start

    values = sorted(v for v, _ in results)
    if not values:
        return {"error": "нет успешных запросов", "errors": len(errors)}

    def pct(p: float) -> float:
        return values[min(int(len(values) * p), len(values) - 1)]

    return {
        "concurrency": concurrency,
        "total": total,
        "completed": len(values),
        "errors": len(errors),
        "wall_s": round(wall, 2),
        "rps": round(len(values) / wall, 2),
        "avg_ms": round(statistics.mean(values), 1),
        "p50_ms": round(pct(0.50), 1),
        "p95_ms": round(pct(0.95), 1),
        "p99_ms": round(pct(0.99), 1),
        "slo_violations": sum(1 for v in values if v > SLO_MS),
        "slo_violation_rate": round(
            sum(1 for v in values if v > SLO_MS) / len(values) * 100, 1),
    }


async def main() -> None:
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--concurrency", type=int, default=16)
    parser.add_argument("--total", type=int, default=300)
    parser.add_argument("--seed", type=int, default=20260918)
    args = parser.parse_args()

    res = await run_load(args.concurrency, args.total, args.seed)
    for key, value in res.items():
        print(f"{key:>20}: {value}")


if __name__ == "__main__":
    asyncio.run(main())
