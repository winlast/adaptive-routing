#!/usr/bin/env python3
"""
Какую нагрузку выдерживает система при заданном пороге отклика.

Сравнивать пропускную способность саму по себе бессмысленно: систему
всегда можно нагрузить сильнее, если согласиться, что пользователи будут
ждать. Осмысленный вопрос другой — сколько запросов в секунду удаётся
обслужить, **не нарушая обещания по времени отклика**. Именно это
определяет, сколько серверов нужно купить.

Замер снимает кривую «нагрузка против p95 лёгких запросов» для обоих
способов маршрутизации и находит, при какой нагрузке каждый из них
упирается в заданный порог.

Оба режима живут в одном и том же nginx, получают один и тот же трафик
и ходят в одни и те же движки. Отличается только выбор узла.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import random
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx

from core.workload import ENDPOINTS, sample_param
from experiments.load_generator import LIGHT_ENDPOINT, TRAFFIC_MIX

LEVELS = [2, 4, 8, 12, 16, 24, 32]


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
    light = sorted(ms for e, ms in results if e == LIGHT_ENDPOINT)
    if not light:
        return None
    return {
        "потоков": concurrency,
        "рпс": round(len(results) / seconds, 1),
        "лёгкие_p95_мс": round(light[int(len(light) * 0.95)], 1),
        "лёгкие_медиана_мс": round(light[len(light) // 2], 1),
    }


def capacity_at(curve, budget_ms):
    """
    Наибольшая нагрузка, при которой p95 ещё укладывается в порог.

    Между замеренными точками применяется линейная интерполяция по
    нагрузке: точная граница между двумя уровнями не измерялась, и
    выдавать её за измеренную нельзя — это оценка.
    """
    ok = [p for p in curve if p and p["лёгкие_p95_мс"] <= budget_ms]
    if not ok:
        return None
    best = max(ok, key=lambda p: p["рпс"])
    worse = [p for p in curve
             if p and p["рпс"] > best["рпс"] and p["лёгкие_p95_мс"] > budget_ms]
    if not worse:
        return {"рпс": best["рпс"], "оценка": False}
    nxt = min(worse, key=lambda p: p["рпс"])
    span = nxt["лёгкие_p95_мс"] - best["лёгкие_p95_мс"]
    frac = (budget_ms - best["лёгкие_p95_мс"]) / span if span > 0 else 0.0
    return {"рпс": round(best["рпс"] + frac * (nxt["рпс"] - best["рпс"]), 1),
            "оценка": True}


async def main():
    out = {}
    for name, path in (("обычная (least_conn)", "/baseline"),
                       ("по оценке стоимости", "/route")):
        curve = []
        for level in LEVELS:
            await asyncio.sleep(4)   # дать движкам разгрузиться
            point = await measure(f"{ARGS.base}{path}", ARGS.seconds,
                                  level, ARGS.seed)
            curve.append(point)
            print(f"{name:<24} {json.dumps(point, ensure_ascii=False)}")
        out[name] = {"кривая": curve,
                     "ёмкость_при_пороге": {
                         str(b): capacity_at(curve, b)
                         for b in ARGS.budgets}}
    Path(ARGS.out).write_text(
        json.dumps({"порог_p95_мс": ARGS.budgets, "секунд_на_точку": ARGS.seconds,
                    "режимы": out}, ensure_ascii=False, indent=2),
        encoding="utf-8")
    print(f"\nЗаписано в {ARGS.out}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8600")
    ap.add_argument("--seconds", type=float, default=20.0)
    ap.add_argument("--seed", type=int, default=20260920)
    ap.add_argument("--budgets", type=float, nargs="+", default=[100.0, 200.0])
    ap.add_argument("--out", default="data/nginx_capacity.json")
    ARGS = ap.parse_args()
    asyncio.run(main())
