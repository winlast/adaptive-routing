"""
Строит таблицу стоимости: сколько стоит каждый эндпоинт на каждом воркере.

Замер делается без конкуренции, по одному запросу за раз, поэтому
отражает чистую стоимость выполнения без эффектов очереди. На этой
таблице строятся два baseline'а:

  * статическое экспертное правило — выбирает воркер с минимальной
    стоимостью и больше ничего не учитывает;
  * оракул — добавляет к стоимости штраф за текущую длину очереди.

Таблица измеряется один раз и сохраняется в data/cost_table.json.
"""
import json
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

OUTPUT = Path(__file__).resolve().parent.parent / "data" / "cost_table.json"
REPEATS = 5


def measure(url: str, endpoint: str) -> float:
    start = time.perf_counter()
    resp = requests.post(url, json={"endpoint": endpoint}, timeout=180)
    resp.raise_for_status()
    return (time.perf_counter() - start) * 1000


def main() -> None:
    ensure_database()
    for url in WORKERS.values():
        measure(url, "/api/user/profile")  # прогрев

    table: dict[str, dict[str, float]] = {}
    print(f"{'endpoint':<26}" + "".join(f"{w:>10}" for w in WORKERS) + "   лучший")
    print("-" * 70)

    for endpoint in ENDPOINT_NAMES:
        row = {}
        for worker, url in WORKERS.items():
            samples = [measure(url, endpoint) for _ in range(REPEATS)]
            row[worker] = round(statistics.median(samples), 2)
        table[endpoint] = row
        best = min(row, key=row.get)
        print(f"{endpoint:<26}" + "".join(f"{row[w]:>10.1f}" for w in WORKERS)
              + f"   {best}")

    OUTPUT.write_text(json.dumps(table, indent=2, ensure_ascii=False),
                      encoding="utf-8")
    print(f"\nСохранено: {OUTPUT}")


if __name__ == "__main__":
    main()
