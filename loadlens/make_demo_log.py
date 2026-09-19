#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Собирает демонстрационный журнал обращений из настоящих замеров стенда.

Журнал не выдуман: длительности взяты из `data/requests_log.csv`, куда
шлюз пишет фактически измеренное время каждого обращения. Здесь они
лишь переводятся в обычный формат журнала веб-сервера, чтобы показать,
что инструмент работает с тем, что есть у любой команды, а не с
собственным форматом.
"""
from __future__ import annotations

import csv
import random
import sys
from datetime import datetime, timedelta
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
SRC = BASE / "data" / "requests_log.csv"
OUT = Path(__file__).resolve().parent / "demo_access.log"

PARAM_NAME = {
    "/api/user/profile": "limit",
    "/api/feed": "limit",
    "/api/search": "limit",
    "/api/report/generate": "days",
    "/api/image/thumbnail": "size",
    "/api/auth/verify": None,
}


def main() -> int:
    if not SRC.exists():
        print(f"Нет исходных замеров: {SRC}", file=sys.stderr)
        print("Сначала прогоните нагрузку на стенде (см. README).",
              file=sys.stderr)
        return 1

    rows = [r for r in csv.DictReader(SRC.open(encoding="utf-8"))
            if r.get("endpoint") and r.get("latency_ms")]
    if not rows:
        print("В журнале шлюза нет записей.", file=sys.stderr)
        return 1

    rng = random.Random(20260919)
    start = datetime(2026, 9, 19, 12, 0, 0)
    lines = []
    for i, r in enumerate(rows):
        endpoint = r["endpoint"]
        param = r.get("param")
        name = PARAM_NAME.get(endpoint)
        url = endpoint
        if name and param not in (None, "", "None"):
            url = f"{endpoint}?{name}={int(float(param))}"
        latency = float(r["latency_ms"]) / 1000.0
        ts = start + timedelta(milliseconds=i * 12 + rng.randint(0, 6))
        stamp = ts.strftime("%d/%b/%Y:%H:%M:%S +0300")
        ip = f"10.0.{rng.randint(0, 3)}.{rng.randint(2, 250)}"
        lines.append(
            f'{ip} - - [{stamp}] "POST {url} HTTP/1.1" 200 '
            f'{rng.randint(300, 2400)} "-" "python-httpx/0.27" {latency:.3f}')

    OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Записано обращений: {len(lines)}")
    print(f"Файл: {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
