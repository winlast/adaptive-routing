#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Собственная стоимость запроса и её вычислительная часть.

Профилирование в `profile_costs.py` измеряет запрос через сеть, поэтому
в его результат входят накладные расходы обращения. Здесь операция
выполняется напрямую, без HTTP, и отдельно измеряется вычислительная
часть. Именно эти величины характеризуют сам запрос, а не способ
обращения к нему, и именно они приводятся в статьях как разброс
стоимости внутри маршрута.

Результат сохраняется в data/endpoint_cost.json, чтобы каждое число в
тексте можно было проверить по файлу.
"""
from __future__ import annotations

import json
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.workload import ENDPOINTS, ensure_database, run_cpu_part, run_sync

OUT = Path(__file__).resolve().parent.parent / "data" / "endpoint_cost.json"
REPEATS = 3


def main() -> None:
    ensure_database()
    result: dict[str, list] = {}
    print(f"{'маршрут':<24}{'параметр':>9}{'полное, мс':>13}"
          f"{'вычисления, мс':>17}")
    print("-" * 64)
    for name, spec in ENDPOINTS.items():
        values = spec.param_values if spec.param_name else (None,)
        rows = []
        for param in values:
            run_sync(name, param)  # прогрев
            totals, cpus = [], []
            for _ in range(REPEATS):
                t = time.perf_counter()
                run_sync(name, param)
                totals.append((time.perf_counter() - t) * 1000)
                t = time.perf_counter()
                run_cpu_part(spec, param)
                cpus.append((time.perf_counter() - t) * 1000)
            total = statistics.median(totals)
            cpu = statistics.median(cpus)
            rows.append({"param": param, "total_ms": round(total, 1),
                         "cpu_ms": round(cpu, 1)})
            print(f"{name:<24}{str(param):>9}{total:>13.0f}{cpu:>17.0f}")
        result[name] = rows
        if len(rows) > 1:
            lo = min(r["total_ms"] for r in rows)
            hi = max(r["total_ms"] for r in rows)
            print(f"{'':<24}{'разброс':>9}{hi / lo:>12.0f}x")
    OUT.write_text(json.dumps(result, indent=2, ensure_ascii=False),
                   encoding="utf-8")
    print(f"\nСохранено: {OUT}")


if __name__ == "__main__":
    main()
