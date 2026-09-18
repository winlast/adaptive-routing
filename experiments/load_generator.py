"""
Генератор нагрузки на шлюз.

Поток запросов задаётся двумя распределениями: по маршрутам и по
значению параметра внутри маршрута. Оба неравномерны и смещены к лёгкому
концу — так устроен реальный веб-трафик. Именно из-за этого смещения
средняя стоимость маршрута оказывается плохим описанием отдельного
запроса: она подтягивается к частым дешёвым обращениям, а редкие тяжёлые,
которые и создают очереди, в ней растворяются.

Последовательность запросов детерминирована зерном, поэтому все политики
сравниваются на буквально одном и том же потоке.

Кроме общих показателей отдельно считаются показатели по лёгким
обращениям (`/api/user/profile`). Это не украшение отчёта: смысл
разделения трафика между движками в том, чтобы тяжёлое вычисление не
останавливало обработку лёгких запросов, а в средней задержке по всему
потоку этот эффект теряется.
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

from core.workload import ENDPOINTS, sample_param

GATEWAY_URL = "http://127.0.0.1:8300/route"

# Доли запросов по маршрутам. Лёгкие обращения преобладают.
TRAFFIC_MIX = {
    "/api/user/profile": 0.35,
    "/api/feed": 0.20,
    "/api/search": 0.20,
    "/api/auth/verify": 0.10,
    "/api/report/generate": 0.10,
    "/api/image/thumbnail": 0.05,
}

# Маршрут, по которому отслеживается страдание лёгких запросов.
LIGHT_ENDPOINT = "/api/user/profile"

SLO_MS = 500.0


def build_plan(total: int, seed: int = 20260918) -> list[tuple[str, int | None]]:
    """Детерминированная последовательность пар «маршрут, параметр»."""
    rng = random.Random(seed)
    endpoints = list(TRAFFIC_MIX)
    weights = [TRAFFIC_MIX[e] for e in endpoints]
    plan = []
    for _ in range(total):
        endpoint = rng.choices(endpoints, weights=weights, k=1)[0]
        plan.append((endpoint, sample_param(ENDPOINTS[endpoint], rng)))
    return plan


async def _client_loop(client, plan_slice, results, errors):
    for endpoint, param in plan_slice:
        start = time.perf_counter()
        try:
            resp = await client.post(
                GATEWAY_URL, json={"endpoint": endpoint, "param": param},
                timeout=600)
            resp.raise_for_status()
            results.append(((time.perf_counter() - start) * 1000, endpoint))
        except Exception as exc:
            errors.append(repr(exc))


def _percentile(ordered: list[float], p: float) -> float:
    if not ordered:
        return float("nan")
    return ordered[min(int(len(ordered) * p), len(ordered) - 1)]


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

    light = sorted(v for v, e in results if e == LIGHT_ENDPOINT)

    return {
        "concurrency": concurrency,
        "total": total,
        "completed": len(values),
        "errors": len(errors),
        "wall_s": round(wall, 2),
        "rps": round(len(values) / wall, 2),
        "avg_ms": round(statistics.mean(values), 1),
        "p50_ms": round(_percentile(values, 0.50), 1),
        "p95_ms": round(_percentile(values, 0.95), 1),
        "p99_ms": round(_percentile(values, 0.99), 1),
        # Лёгкие обращения — те, которые страдают от блокировки чужим
        # вычислением. Ради них маршрутизация и делается.
        "light_count": len(light),
        "light_avg_ms": round(statistics.mean(light), 1) if light else None,
        "light_p95_ms": round(_percentile(light, 0.95), 1) if light else None,
        "light_p99_ms": round(_percentile(light, 0.99), 1) if light else None,
        "slo_violation_rate": round(
            sum(1 for v in values if v > SLO_MS) / len(values) * 100, 1),
        # Сырые задержки нужны для бутстрэпа: доверительный интервал
        # строится по объединённой выборке всех повторов, а не по трём
        # усреднённым числам.
        "_latencies": values,
        "_light_latencies": light,
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
        print(f"{key:>22}: {value}")


if __name__ == "__main__":
    asyncio.run(main())
