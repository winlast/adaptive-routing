#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Во что обходится само решение о маршрутизации.

Оценка выполняется на каждый запрос, поэтому её собственная стоимость
входит в накладные расходы маршрутизатора и ограничивает применимость.
Измеряется только принятие решения, без проксирования: обращение к
оценке, сравнение движков и выбор.

Результат сохраняется в data/decision_cost.json.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.cost_model import build_estimator
from core.policies import BlockingBudgetPolicy, RequestFeatures

OUT = Path(__file__).resolve().parent.parent / "data" / "decision_cost.json"

SOURCES = {
    "linear_param": "экспертное правило",
    "power_law": "степенной закон",
    "neural": "нейронная сеть",
}


def main() -> None:
    features = RequestFeatures(
        endpoint="/api/search", method="POST", payload_bytes=250, param=180,
        inflight={"sync": 3, "async": 5},
        pending_work={"sync": 120.0, "async": 40.0})

    result = {}
    print(f"{'источник оценки':<22}{'одно решение':>16}{'решений в секунду':>22}")
    print("-" * 60)
    for name, title in SOURCES.items():
        policy = BlockingBudgetPolicy(build_estimator(name), 30.0)
        for _ in range(500):
            policy.choose(features)          # прогрев
        n = 20000 if name != "neural" else 3000
        start = time.perf_counter()
        for _ in range(n):
            policy.choose(features)
        per_us = (time.perf_counter() - start) / n * 1e6
        result[name] = {"title": title, "microseconds": round(per_us, 2),
                        "decisions_per_second": round(1e6 / per_us)}
        print(f"{title:<22}{per_us:>13.1f} мкс{1e6 / per_us:>20,.0f}"
              .replace(",", " "))

    OUT.write_text(json.dumps(result, indent=2, ensure_ascii=False),
                   encoding="utf-8")
    print(f"\nСохранено: {OUT}")


if __name__ == "__main__":
    main()
