#!/usr/bin/env python3
"""
Замер, который можно повторить вручную одной командой curl.

Нужен он для показа: четыре обращения к одному и тому же адресу с
разным временем убеждают сильнее любого графика, потому что зритель
может тут же повторить их сам. Числа сохраняются в файл, чтобы на
слайде и на карточке стояло измеренное, а не запомненное.

Каждое обращение выполняется несколько раз, берётся медиана: разовый
замер на прогретой и непрогретой базе различается заметно.
"""

from __future__ import annotations

import json
import statistics
import time
import urllib.request
from pathlib import Path

BASE = "http://127.0.0.1:8500/orders?"
ПОВТОРОВ = 7

ЗАПРОСЫ = [
    ("limit=5", "пять строк"),
    ("id=eq.500", "одна строка по ключу"),
    ("limit=20000", "двадцать тысяч строк"),
    ("order=amount.desc&limit=20000", "двадцать тысяч строк с сортировкой"),
]


def измерить(query: str) -> float:
    времена = []
    for _ in range(ПОВТОРОВ):
        начало = time.perf_counter()
        with urllib.request.urlopen(BASE + query, timeout=60) as r:
            r.read()
        времена.append((time.perf_counter() - начало) * 1000)
    return statistics.median(времена)


def main() -> None:
    строки = []
    for query, подпись in ЗАПРОСЫ:
        мс = измерить(query)
        строки.append({"запрос": "/orders?" + query, "подпись": подпись,
                       "медиана_мс": round(мс, 1)})
        print(f"{query:<34} {мс:8.1f} мс")

    быстрый = min(s["медиана_мс"] for s in строки)
    медленный = max(s["медиана_мс"] for s in строки)
    итог = {
        "источник": "PostgREST 12 + PostgreSQL 16, 300 000 строк",
        "примечание": "чужое ПО; повторяется вручную через curl",
        "повторов_на_запрос": ПОВТОРОВ,
        "замеры": строки,
        "разница_раз": round(медленный / быстрый),
    }
    путь = Path(__file__).resolve().parent.parent / "data" / "handmade.json"
    путь.write_text(json.dumps(итог, ensure_ascii=False, indent=2),
                    encoding="utf-8")
    print(f"\nРазница в {итог['разница_раз']} раз. Записано в {путь}")


if __name__ == "__main__":
    main()
