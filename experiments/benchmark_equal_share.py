"""
Сравнение классификаторов при равной доле трафика в асинхронном движке.

Сравнивать классификаторы при одном и том же пороге некорректно: порог
означает для разных оценок разное. Оценка, систематически завышающая
стоимость, при том же пороге пропустит в асинхронный движок меньше
работы, отчего у неё окажется лучше хвост и хуже пропускная способность.
Это различие рабочих режимов, а не точности.

Здесь каждому классификатору подобран свой порог, дающий одну и ту же
долю трафика в асинхронном движке. Подбор сделан заранее, на потоке из
сорока тысяч запросов того же распределения (`classifier_quality.py`).
При равной доле пропущенной работы пропускная способность выравнивается
по построению, и остаётся единственное различие — правильно ли выбраны
именно те запросы, которые безопасны для event loop.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from experiments.benchmark_budget import SOURCES, measure

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
CALIBRATION = DATA_DIR / "classifier_quality.json"
RESULTS_PATH = DATA_DIR / "equal_share.json"

NAME_BY_SOURCE = {
    "endpoint": "endpoint_mean",
    "linear": "linear_param",
    "power": "power_law",
    "neural": "neural",
}
SHARES = [s for s in os.environ.get("SHARES", "0.6,0.7").split(",")]


async def main() -> None:
    calibration = json.loads(CALIBRATION.read_text(encoding="utf-8"))
    results: dict = {}

    print("Пороги подобраны так, чтобы доля трафика в асинхронном движке "
          "была одинаковой.\n")
    print(f"{'доля':>6}  {'классификатор':<24}{'порог':>8}{'rps':>14}"
          f"{'лёгкие p95':>13}{'лёгкие >200мс':>15}{'SLO%':>8}")
    print("-" * 90)

    for share in SHARES:
        row = calibration["calibrated"].get(share)
        if row is None:
            print(f"Нет калибровки для доли {share}")
            continue
        for source, title in SOURCES.items():
            key = NAME_BY_SOURCE.get(source)
            if key is None or key not in row:
                continue
            budget = row[key]["budget_ms"]
            policy = f"budget_{source}_{budget}"
            res = await measure(policy)
            res["budget_ms"] = budget
            res["expected_block_ms_per_1000"] = row[key][
                "admitted_block_ms_per_1000"]
            res["worst_admitted_block_ms"] = row[key][
                "worst_admitted_block_ms"]
            results.setdefault(share, {})[source] = res
            print(f"{float(share):>6.0%}  {title:<24}{budget:>8.0f}"
                  f"{res['rps']:>8.1f} ±{res['rps_std']:<4.1f}"
                  f"{res['light_p95']:>13.0f}"
                  f"{res['light_over_budget_pct']:>14.1f}%{res['slo']:>8.1f}",
                  flush=True)
        print()

    RESULTS_PATH.write_text(json.dumps(results, indent=2, ensure_ascii=False),
                            encoding="utf-8")
    print(f"Сохранено: {RESULTS_PATH}")


if __name__ == "__main__":
    asyncio.run(main())
