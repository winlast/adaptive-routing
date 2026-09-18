"""
Разведочный замер под конкурентной нагрузкой.

Предыдущий замер (probe_workers.py) показал, что на изолированном запросе
воркеры почти неразличимы. Ожидаемый источник различий — динамика под
нагрузкой: блокировка event loop, конкуренция за GIL, очередь к пулу
процессов. Этот скрипт подаёт на каждый воркер один и тот же смешанный
поток запросов и сравнивает, как они справляются.
"""
import asyncio
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx

from core.workload import ENDPOINT_NAMES

WORKERS = {
    "sync": "http://127.0.0.1:8201/process",
    "async": "http://127.0.0.1:8202/process",
    "process": "http://127.0.0.1:8203/process",
}

CONCURRENCY = 16
REQUESTS_PER_CLIENT = 8


async def client_loop(client, url, endpoints, latencies, errors):
    for endpoint in endpoints:
        start = time.perf_counter()
        try:
            resp = await client.post(url, json={"endpoint": endpoint}, timeout=180)
            resp.raise_for_status()
            latencies.append(((time.perf_counter() - start) * 1000, endpoint))
        except Exception as exc:
            errors.append(repr(exc))


async def run_worker(name: str, url: str) -> dict:
    # Один и тот же детерминированный поток для всех воркеров.
    plan = [
        [ENDPOINT_NAMES[(c * REQUESTS_PER_CLIENT + i) % len(ENDPOINT_NAMES)]
         for i in range(REQUESTS_PER_CLIENT)]
        for c in range(CONCURRENCY)
    ]

    latencies: list = []
    errors: list = []
    limits = httpx.Limits(max_connections=CONCURRENCY + 10,
                          max_keepalive_connections=CONCURRENCY + 10)
    async with httpx.AsyncClient(limits=limits) as client:
        start = time.perf_counter()
        await asyncio.gather(*[
            client_loop(client, url, plan[c], latencies, errors)
            for c in range(CONCURRENCY)
        ])
        wall = time.perf_counter() - start

    values = sorted(v for v, _ in latencies)
    total = CONCURRENCY * REQUESTS_PER_CLIENT
    return {
        "worker": name,
        "rps": round(len(values) / wall, 2) if wall else 0,
        "avg": round(statistics.mean(values), 1) if values else None,
        "p95": round(values[int(len(values) * 0.95)], 1) if values else None,
        "errors": len(errors),
        "done": f"{len(values)}/{total}",
        "per_endpoint": {
            ep: round(statistics.median([v for v, e in latencies if e == ep]), 1)
            for ep in ENDPOINT_NAMES
            if any(e == ep for _, e in latencies)
        },
    }


async def main() -> None:
    print(f"Конкурентность {CONCURRENCY}, по {REQUESTS_PER_CLIENT} запросов "
          f"на клиента, смешанный поток из {len(ENDPOINT_NAMES)} эндпоинтов\n")
    results = []
    for name, url in WORKERS.items():
        res = await run_worker(name, url)
        results.append(res)
        print(f"{name:>8}: {res['rps']:>6} rps | avg {res['avg']:>7} мс | "
              f"p95 {res['p95']:>7} мс | ошибок {res['errors']} | {res['done']}")
        await asyncio.sleep(2)

    # Таблица стоимостей под нагрузкой. Именно она даёт честный, сильный
    # статический baseline: эксперт профилирует систему в рабочем режиме,
    # а не по одиночным запросам, где воркеры почти неразличимы.
    import json
    cost_table = {
        ep: {r["worker"]: r["per_endpoint"].get(ep) for r in results
             if r["per_endpoint"].get(ep) is not None}
        for ep in ENDPOINT_NAMES
    }
    out = Path(__file__).resolve().parent.parent / "data" / "cost_table.json"
    out.write_text(json.dumps(cost_table, indent=2, ensure_ascii=False),
                   encoding="utf-8")
    print(f"\nТаблица стоимостей под нагрузкой сохранена: {out}")

    print("\nМедиана по эндпоинтам (мс):")
    header = f"{'endpoint':<26}" + "".join(f"{r['worker']:>10}" for r in results)
    print(header)
    print("-" * len(header))
    for ep in ENDPOINT_NAMES:
        cells = "".join(f"{r['per_endpoint'].get(ep, float('nan')):>10.1f}"
                        for r in results)
        best = min(results, key=lambda r: r["per_endpoint"].get(ep, 1e9))["worker"]
        print(f"{ep:<26}{cells}   лучший: {best}")


if __name__ == "__main__":
    asyncio.run(main())
