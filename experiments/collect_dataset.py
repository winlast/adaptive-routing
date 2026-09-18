"""
Сбор обучающих данных.

Шлюз работает со случайной политикой: это нужно не потому, что случайный
выбор хорош, а потому, что он обеспечивает покрытие. Если собирать данные
разумной политикой, в логе окажутся только те пары «запрос-воркер»,
которые она и так считает удачными, и модель никогда не увидит, чем
плохи остальные варианты. Случайная политика даёт наблюдения по всем
комбинациям.

Конкурентность варьируется от прогона к прогону, чтобы в данных были
представлены разные состояния системы — от почти свободной до
перегруженной. Без этого модель не сможет научиться учитывать очереди.
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from experiments.load_generator import run_load

# Разная конкурентность — разные состояния очередей в обучающих данных.
SCHEDULE = [
    (4, 200),
    (8, 250),
    (16, 350),
    (24, 350),
    (32, 350),
]


async def main() -> None:
    total_done = 0
    for i, (concurrency, total) in enumerate(SCHEDULE, 1):
        print(f"[{i}/{len(SCHEDULE)}] конкурентность {concurrency}, "
              f"{total} запросов...", flush=True)
        res = await run_load(concurrency, total, seed=1000 + i)
        total_done += res.get("completed", 0)
        print(f"     rps {res.get('rps')}, avg {res.get('avg_ms')} мс, "
              f"ошибок {res.get('errors')}", flush=True)
        await asyncio.sleep(3)
    print(f"\nВсего собрано запросов: {total_done}")


if __name__ == "__main__":
    asyncio.run(main())
