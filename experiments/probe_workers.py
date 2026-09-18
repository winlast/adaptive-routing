"""
Разведочный замер: матрица «эндпоинт × воркер» на изолированных запросах.

Отвечает на вопрос, без которого вся работа лишена смысла: отличается ли
вообще время обработки одного и того же запроса на разных воркерах, и
меняется ли лучший воркер от эндпоинта к эндпоинту. Замер делается без
конкуренции (по одному запросу за раз), поэтому показывает чистую
стоимость выполнения, без эффектов очередей.
"""
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import requests

from core.workload import ENDPOINT_NAMES, ensure_database

WORKERS = {
    "sync": "http://127.0.0.1:8201/process",
    "async": "http://127.0.0.1:8202/process",
    "process": "http://127.0.0.1:8203/process",
}

REPEATS = 3


def measure(url: str, endpoint: str) -> float:
    start = time.perf_counter()
    resp = requests.post(url, json={"endpoint": endpoint}, timeout=120)
    resp.raise_for_status()
    return (time.perf_counter() - start) * 1000


def main() -> None:
    ensure_database()

    # Прогрев: первый запрос поднимает пул процессов и наполняет кеши.
    for url in WORKERS.values():
        measure(url, "/api/user/profile")

    print(f"{'endpoint':<26} " + " ".join(f"{w:>10}" for w in WORKERS) + "   лучший")
    print("-" * 74)

    for endpoint in ENDPOINT_NAMES:
        row = {}
        for worker, url in WORKERS.items():
            samples = [measure(url, endpoint) for _ in range(REPEATS)]
            row[worker] = statistics.median(samples)
        best = min(row, key=row.get)
        cells = " ".join(f"{row[w]:>10.1f}" for w in WORKERS)
        print(f"{endpoint:<26} {cells}   {best}")


if __name__ == "__main__":
    main()
