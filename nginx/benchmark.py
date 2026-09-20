#!/usr/bin/env python3
"""
Сравнение обычной балансировки и маршрутизации по оценке стоимости —
обе внутри одного и того же nginx.

Сравнение устроено так, чтобы различался ровно один фактор. Трафик
одинаковый (тот же набор обращений и то же распределение параметров),
движки одни и те же, посредник один и тот же процесс nginx. Отличается
только способ выбора узла: least_conn против политики бюджета
блокировки в модуле на Lua.

Считается доля лёгких обращений, которые оказались медленнее порога.
Именно это чувствует пользователь: его быстрый запрос стоит в очереди
за чужой тяжёлой работой.
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
        results.append((endpoint, (time.time() - started) * 1000))


async def measure(url, seconds, concurrency, seed):
    results: list[tuple[str, float]] = []
    limits = httpx.Limits(max_connections=concurrency * 2,
                          max_keepalive_connections=concurrency * 2)
    async with httpx.AsyncClient(timeout=60.0, limits=limits) as client:
        stop_at = time.time() + seconds
        await asyncio.gather(*[
            one_stream(client, url, random.Random(seed + i), stop_at, results)
            for i in range(concurrency)])
    return results


def summarise(results):
    light = sorted(ms for e, ms in results if e == LIGHT_ENDPOINT)
    if not light:
        return None
    slow = sum(1 for ms in light if ms > SLOW_MS) / len(light) * 100
    return {
        "обращений": len(results),
        "лёгких": len(light),
        "пропускная_способность_рпс": round(len(results) / ARGS.seconds, 1),
        "лёгкие_медиана_мс": round(statistics.median(light), 1),
        "лёгкие_p95_мс": round(light[int(len(light) * 0.95)], 1),
        f"доля_медленнее_{SLOW_MS:.0f}_мс_%": round(slow, 1),
    }


async def main():
    out = {}
    for name, path in (("обычная (least_conn)", "/baseline"),
                       ("по оценке стоимости", "/route")):
        # Пауза между замерами: движки должны разгрузиться, иначе хвост
        # предыдущего прогона попадёт в следующий.
        await asyncio.sleep(3)
        results = await measure(f"{ARGS.base}{path}", ARGS.seconds,
                                ARGS.concurrency, ARGS.seed)
        out[name] = summarise(results)
        print(f"{name:<24} {json.dumps(out[name], ensure_ascii=False)}")
    Path(ARGS.out).write_text(
        json.dumps({"порог_мс": SLOW_MS, "секунд": ARGS.seconds,
                    "потоков": ARGS.concurrency, "режимы": out},
                   ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nЗаписано в {ARGS.out}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8600")
    ap.add_argument("--seconds", type=float, default=30.0)
    ap.add_argument("--concurrency", type=int, default=24)
    ap.add_argument("--seed", type=int, default=20260920)
    ap.add_argument("--out", default="data/nginx_module.json")
    ARGS = ap.parse_args()
    asyncio.run(main())
