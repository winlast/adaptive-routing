#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Трафик к чужому сервису для проверки утверждения о разбросе стоимости.

Маршруты и параметры здесь не наши — это штатный интерфейс PostgREST.
Состав обращений подобран как у обычного сервиса с постраничным выводом:
чаще всего просят небольшие страницы, изредка — крупные выгрузки.

Значения `limit` берутся из логарифмически равномерного распределения со
смещением к малым, тем же, что в наших замерах. Диапазон задан один раз
и под результат не подбирался: от 1 до 20 000 строк из трёхсот тысяч.
"""
from __future__ import annotations

import asyncio
import random
import sys
import time

import httpx

BASE = "http://127.0.0.1:8500"
TOTAL = int(sys.argv[1]) if len(sys.argv) > 1 else 1500
CONCURRENCY = 6

# Доли обращений по маршрутам. Похоже на обычный сервис: список,
# отфильтрованный список, выборка полей, сортировка, одна запись.
MIX = {
    "list": 0.30,
    "by_region": 0.22,
    "projection": 0.18,
    "sorted": 0.15,
    "single": 0.15,
}
REGIONS = ["north", "south", "east", "west"]
STATUSES = ["new", "paid", "shipped", "done", "cancelled"]


def sample_limit(rng: random.Random, lo: int = 1, hi: int = 20000) -> int:
    """Логарифмически равномерно со смещением к малым значениям."""
    u = rng.random() ** 1.7
    return int(round(lo * (hi / lo) ** u))


def build(rng: random.Random) -> str:
    kind = rng.choices(list(MIX), weights=list(MIX.values()), k=1)[0]
    n = sample_limit(rng)
    if kind == "list":
        return f"/orders?limit={n}"
    if kind == "by_region":
        return f"/orders?region=eq.{rng.choice(REGIONS)}&limit={n}"
    if kind == "projection":
        return f"/orders?select=id,region,amount&limit={n}"
    if kind == "sorted":
        return f"/orders?order=amount.desc&limit={n}"
    return f"/orders?id=eq.{rng.randint(1, 300000)}"


async def worker(client: httpx.AsyncClient, urls: list[str], done: list) -> None:
    for url in urls:
        try:
            r = await client.get(BASE + url, timeout=120)
            done.append(r.status_code)
        except Exception:
            done.append(0)


async def main() -> None:
    rng = random.Random(20260920)
    urls = [build(rng) for _ in range(TOTAL)]
    slices = [urls[i::CONCURRENCY] for i in range(CONCURRENCY)]
    done: list[int] = []
    limits = httpx.Limits(max_connections=CONCURRENCY + 4,
                          max_keepalive_connections=CONCURRENCY + 4)
    start = time.perf_counter()
    async with httpx.AsyncClient(limits=limits) as client:
        await asyncio.gather(*[worker(client, s, done) for s in slices])
    wall = time.perf_counter() - start
    ok = sum(1 for c in done if 200 <= c < 300)
    print(f"обращений: {len(done)}, успешных: {ok}, "
          f"время: {wall:.1f} с, {len(done)/wall:.0f} запросов/с")


if __name__ == "__main__":
    asyncio.run(main())
