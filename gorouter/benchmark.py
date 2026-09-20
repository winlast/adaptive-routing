#!/usr/bin/env python3
"""
Сквозная проверка маршрутизатора на Go.

Смысл замера не в том, чтобы Go оказался быстрее Python — это
неинтересно и заранее известно. Смысл в двух вещах.

Первая: та же политика, написанная в третий раз и на другом языке,
должна давать то же поведение. Если не даст — значит результат держался
на частности реализации, а не на самом подходе.

Вторая: маршрутизатор не должен становиться узким местом. Отдельно
измеряется, сколько времени уходит на само решение, — маршрутизатор
сообщает это в /status.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import random
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx

from core.workload import ENDPOINTS, sample_param
from experiments.load_generator import LIGHT_ENDPOINT, TRAFFIC_MIX

SLOW_MS = 200.0


async def one_stream(client, url, rng, stop_at, results):
    endpoints = list(TRAFFIC_MIX)
    weights = [TRAFFIC_MIX[e] for e in endpoints]
    while time.time() < stop_at:
        endpoint = rng.choices(endpoints, weights=weights, k=1)[0]
        body = {"endpoint": endpoint,
                "param": sample_param(ENDPOINTS[endpoint], rng)}
        started = time.time()
        try:
            r = await client.post(url, json=body)
            r.read()
        except Exception:
            continue
        results.append((endpoint, (time.time() - started) * 1000,
                        r.headers.get("X-Worker", "?")))


async def main():
    results = []
    limits = httpx.Limits(max_connections=ARGS.concurrency * 2,
                          max_keepalive_connections=ARGS.concurrency * 2)
    async with httpx.AsyncClient(timeout=60.0, limits=limits) as client:
        stop_at = time.time() + ARGS.seconds
        await asyncio.gather(*[
            one_stream(client, f"{ARGS.base}/route",
                       random.Random(ARGS.seed + i), stop_at, results)
            for i in range(ARGS.concurrency)])
        status = (await client.get(f"{ARGS.base}/status")).json()

    light = sorted(ms for e, ms, _ in results if e == LIGHT_ENDPOINT)
    по_движкам = {}
    for _, _, w in results:
        по_движкам[w] = по_движкам.get(w, 0) + 1

    out = {
        "обращений": len(results),
        "пропускная_способность_рпс": round(len(results) / ARGS.seconds, 1),
        "лёгкие_медиана_мс": round(statistics.median(light), 1) if light else None,
        "лёгкие_p95_мс": round(light[int(len(light) * 0.95)], 1) if light else None,
        f"доля_медленнее_{SLOW_MS:.0f}_мс_%":
            round(sum(1 for ms in light if ms > SLOW_MS) / len(light) * 100, 1)
            if light else None,
        "распределение_по_движкам": по_движкам,
        "стоимость_решения_нс": status["avg_decision_ns"],
        "решений_принято": status["decisions"],
    }
    print(json.dumps(out, ensure_ascii=False, indent=2))
    Path(ARGS.out).write_text(json.dumps(out, ensure_ascii=False, indent=2),
                              encoding="utf-8")
    print(f"\nЗаписано в {ARGS.out}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8700")
    ap.add_argument("--seconds", type=float, default=40.0)
    ap.add_argument("--concurrency", type=int, default=24)
    ap.add_argument("--seed", type=int, default=20260920)
    ap.add_argument("--out", default="data/gorouter.json")
    ARGS = ap.parse_args()
    asyncio.run(main())
