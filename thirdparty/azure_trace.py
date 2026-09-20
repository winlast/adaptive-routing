#!/usr/bin/env python3
"""
Проверка главной посылки проекта на производственной трассе Microsoft.

Всё остальное в проекте измерено на стендах — нашем собственном и чужом,
но всё равно поднятом нами. Здесь данные не наши целиком: это трасса
Azure Functions за две недели 2019 года, опубликованная Microsoft
Research вместе со статьёй на USENIX ATC'20. В ней десятки тысяч
настоящих функций настоящих клиентов Azure.

Посылка проекта: стоимость обращения не определяется тем, куда оно
адресовано. В трассе каждая функция — это, по сути, один обработчик,
аналог маршрута. Если посылка верна, внутри одной функции времена
выполнения должны сильно расходиться.

Что трасса НЕ позволяет проверить: параметры запросов в ней не
сохранены, поэтому предсказать стоимость по ним здесь невозможно.
Проверяется только посылка, а не наша оценка.

Данные: https://github.com/Azure/AzurePublicDataset
"""

from __future__ import annotations

import csv
import json
import statistics
import sys
from pathlib import Path

# Функции с единичными вызовами дают неустойчивые перцентили, а при
# медиане меньше миллисекунды отношение p99/p50 раздувается делением на
# погрешность округления. И то и другое отсекается, чтобы вывод не
# держался на шуме.
MIN_CALLS = 100
MIN_P50_MS = 1.0


def load(path: Path) -> list[dict[str, float]]:
    rows = []
    with path.open(encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            try:
                count = float(row["Count"])
                p50 = float(row["percentile_Average_50"])
                p99 = float(row["percentile_Average_99"])
                p100 = float(row["percentile_Average_100"])
                avg = float(row["Average"])
            except (ValueError, KeyError):
                continue
            if count < MIN_CALLS or p50 < MIN_P50_MS:
                continue
            rows.append({"count": count, "p50": p50, "p99": p99,
                         "max": p100, "avg": avg,
                         "func": row["HashFunction"]})
    return rows


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        print("Использование: azure_trace.py <function_durations_*.csv> ...")
        return 1

    rows: list[dict[str, float]] = []
    for arg in sys.argv[1:]:
        rows += load(Path(arg))
    if not rows:
        print("Подходящих функций не найдено.")
        return 1

    spreads = sorted(r["p99"] / r["p50"] for r in rows)
    tails = sorted(r["max"] / r["p50"] for r in rows)
    n = len(rows)
    calls = sum(r["count"] for r in rows)

    def share(seq: list[float], limit: float) -> float:
        return sum(1 for v in seq if v >= limit) / len(seq) * 100

    result = {
        "источник": "Azure Functions Trace 2019, Microsoft Research",
        "что_проверяется": "расходится ли стоимость внутри одного обработчика",
        "что_не_проверяется": "предсказание по параметрам — их в трассе нет",
        "отбор": f"функции с не менее чем {MIN_CALLS} вызовами "
                 f"и медианой от {MIN_P50_MS:.0f} мс",
        "функций": n,
        "вызовов": int(calls),
        "разброс_p99_к_p50": {
            "медиана": round(statistics.median(spreads), 1),
            "четверть_сверху": round(spreads[int(n * 0.75)], 1),
            "десятая_сверху": round(spreads[int(n * 0.90)], 1),
            "доля_функций_с_разбросом_от_10_раз_%": round(share(spreads, 10), 1),
            "доля_функций_с_разбросом_от_100_раз_%": round(share(spreads, 100), 1),
        },
        "разброс_макс_к_p50": {
            "медиана": round(statistics.median(tails), 1),
            "доля_функций_с_разбросом_от_100_раз_%": round(share(tails, 100), 1),
        },
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    out = Path(__file__).resolve().parent.parent / "data" / "azure_trace.json"
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2),
                   encoding="utf-8")
    print(f"\nЗаписано в {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
