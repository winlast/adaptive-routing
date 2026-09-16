"""
Реалистичное нагрузочное тестирование на СМЕШАННОМ и ПЕРЕМЕЖАЮЩЕМСЯ потоке
запросов (в отличие от старого benchmark_mixed.py, который отправлял
light- и heavy-запросы последовательными фазами через `hey` — это не
воспроизводит эффект блокировки event loop, для которого нужно, чтобы
CPU-bound и I/O-bound запросы конкурировали за один и тот же процесс
ОДНОВРЕМЕННО).

`hey` не подходит: он шлёт один и тот же payload на все запросы. Поэтому
здесь используется собственный асинхронный генератор нагрузки на httpx:
N параллельных "воркеров" непрерывно шлют запросы на один и тот же URL,
случайно выбирая io-bound или cpu-bound payload с заданной пропорцией.

Сравниваются три сценария на одном и том же смешанном потоке:
  - adaptive:      http://127.0.0.1:9000/route   (шлюз с классификатором)
  - flask_only:     http://127.0.0.1:5000/process (весь трафик на Flask)
  - fastapi_only:   http://127.0.0.1:8000/process (весь трафик на FastAPI)
"""
import asyncio
import csv
import random
import statistics
import time
from pathlib import Path

import httpx

SCRIPTS_DIR = Path(__file__).resolve().parent
BASE_DIR = SCRIPTS_DIR.parent
DATA_DIR = BASE_DIR / "data"
OUTPUT_FILE = DATA_DIR / "benchmark_realistic_results.csv"

# io-bound: io_intensity >= 0.15 (см. workload.py) -> оптимален FastAPI
IO_PAYLOAD = {"load": 30, "io_intensity": 0.4, "cpu_usage": 15}
# cpu-bound: io_intensity < 0.15 -> оптимален Flask
CPU_PAYLOAD = {"load": 120, "io_intensity": 0.05, "cpu_usage": 90}

IO_SHARE = 0.7  # доля io-bound запросов в смешанном потоке

CONCURRENCY_LEVELS = [20, 50, 100]
REQUESTS_PER_WORKER = 15

SCENARIOS = {
    "adaptive": "http://127.0.0.1:9000/route",
    "flask_only": "http://127.0.0.1:5000/process",
    "fastapi_only": "http://127.0.0.1:8000/process",
}


def pick_payload() -> dict:
    return IO_PAYLOAD if random.random() < IO_SHARE else CPU_PAYLOAD


async def worker(client: httpx.AsyncClient, url: str, n_requests: int, latencies: list, errors: list):
    for _ in range(n_requests):
        payload = pick_payload()
        start = time.perf_counter()
        try:
            resp = await client.post(url, json=payload, timeout=30.0)
            resp.raise_for_status()
            latencies.append((time.perf_counter() - start) * 1000)
        except Exception as e:
            errors.append(str(e))


async def run_scenario(url: str, concurrency: int, requests_per_worker: int) -> dict:
    limits = httpx.Limits(max_connections=concurrency + 10, max_keepalive_connections=concurrency + 10)
    async with httpx.AsyncClient(limits=limits) as client:
        latencies: list = []
        errors: list = []
        start = time.perf_counter()
        tasks = [
            asyncio.create_task(worker(client, url, requests_per_worker, latencies, errors))
            for _ in range(concurrency)
        ]
        await asyncio.gather(*tasks)
        wall_time = time.perf_counter() - start

    total_requests = concurrency * requests_per_worker
    successes = len(latencies)
    latencies.sort()

    def pct(p):
        if not latencies:
            return None
        idx = min(int(len(latencies) * p), len(latencies) - 1)
        return round(latencies[idx], 2)

    return {
        "total_requests": total_requests,
        "successes": successes,
        "failures": total_requests - successes,
        "throughput_rps": round(successes / wall_time, 2) if wall_time > 0 else 0,
        "avg_latency_ms": round(statistics.mean(latencies), 2) if latencies else None,
        "p95_latency_ms": pct(0.95),
        "p99_latency_ms": pct(0.99),
        "wall_time_s": round(wall_time, 2),
    }


async def main():
    rows = []
    for name, url in SCENARIOS.items():
        print(f"\n=== {name} ({url}) ===")
        for concurrency in CONCURRENCY_LEVELS:
            print(f"  concurrency={concurrency}, "
                  f"requests={concurrency * REQUESTS_PER_WORKER} (io_share={IO_SHARE}) ...")
            result = await run_scenario(url, concurrency, REQUESTS_PER_WORKER)
            result["scenario"] = name
            result["concurrency"] = concurrency
            rows.append(result)
            print(f"    throughput={result['throughput_rps']} rps | "
                  f"avg={result['avg_latency_ms']} ms | "
                  f"p95={result['p95_latency_ms']} ms | "
                  f"p99={result['p99_latency_ms']} ms | "
                  f"failures={result['failures']}")
            await asyncio.sleep(2)

    DATA_DIR.mkdir(exist_ok=True)
    fieldnames = ["scenario", "concurrency", "total_requests", "successes", "failures",
                  "throughput_rps", "avg_latency_ms", "p95_latency_ms", "p99_latency_ms", "wall_time_s"]
    with open(OUTPUT_FILE, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    print(f"\nСохранено: {OUTPUT_FILE}")


if __name__ == "__main__":
    asyncio.run(main())
