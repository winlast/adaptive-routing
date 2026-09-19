"""
Как выигрыш от маршрутизации зависит от уровня нагрузки и от состава
потока.

Два вопроса, на которые предыдущие замеры не отвечали.

Первый: при какой нагрузке маршрутизация даёт выигрыш и насколько он
меняется. Замер при одной конкурентности показывает точку, а не
зависимость, и по нему нельзя судить, растёт эффект с нагрузкой или
убывает. Физически ожидается рост: чем больше запросов обслуживается
одновременно, тем больше их простаивает за чужим вычислением, пока
event loop заблокирован. Проверяется это, а не постулируется.

Второй: остаётся ли выигрыш, если поток однороден. Если все запросы
одного характера, движкам не в чем различаться и распределять между
ними нечего — выигрыша быть не должно. Это контрольный замер: он
проверяет, что эффект объясняется именно разнородностью потока, а не
чем-то посторонним вроде накладных расходов шлюза. Обнаружение выигрыша
на однородном потоке означало бы, что механизм понят неверно.

Для отсчёта в каждом режиме измеряются оба одиночных движка: выигрыш
считается относительно лучшего из них, а не относительно худшего.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import experiments.benchmark_budget as bb
from experiments.benchmark_budget import measure

BASE_DIR = Path(__file__).resolve().parent.parent
RESULTS_PATH = BASE_DIR / "data" / "load_levels.json"

# Политики: два одиночных движка, слепая балансировка и маршрутизация с
# классификатором при двух порогах — строгом и умеренном.
POLICIES = {
    "all_sync": "весь трафик в синхронный",
    "all_async": "весь трафик в асинхронный",
    "least_conn": "least-connections",
    "budget_neural_30": "классификатор, порог 30 мс",
    "budget_neural_60": "классификатор, порог 60 мс",
}
SINGLE = ("all_sync", "all_async")

LEVELS = [int(x) for x in os.environ.get("LEVELS", "16,32,64").split(",")]
REQUESTS = int(os.environ.get("REQUESTS", "400"))
REPEATS = int(os.environ.get("REPEATS", "3"))
TRAFFIC = os.environ.get("TRAFFIC", "mixed")


async def main() -> None:
    bb.REQUESTS = REQUESTS
    bb.REPEATS = REPEATS

    results: dict = {}
    print(f"Состав потока: {TRAFFIC}. {REQUESTS} запросов, "
          f"{REPEATS} повтора на точку.\n")

    for level in LEVELS:
        bb.CONCURRENCY = level
        print(f"{'=' * 88}\nКонкурентность {level}\n{'=' * 88}")
        print(f"{'режим':<32}{'rps':>14}{'средняя, мс':>14}"
              f"{'p95, мс':>10}{'лёгкие >200мс':>15}")
        print("-" * 88)

        level_results = {}
        for policy, title in POLICIES.items():
            res = await measure(policy)
            level_results[policy] = res
            light = res.get("light_over_budget_pct")
            light_text = f"{light:>14.1f}%" if light is not None else f"{'—':>15}"
            print(f"{title:<32}{res['rps']:>8.1f} ±{res['rps_std']:<4.1f}"
                  f"{res['avg']:>14.0f}{res['p95']:>10.0f}{light_text}",
                  flush=True)
        results[str(level)] = level_results

        # Выигрыш считается относительно лучшего одиночного движка —
        # того, который сам по себе справляется лучше. Сравнение с
        # худшим завышало бы эффект.
        best = max(SINGLE, key=lambda p: level_results[p]["rps"])
        base = level_results[best]
        print(f"\nЛучший одиночный движок: {POLICIES[best]} "
              f"({base['rps']} rps, средняя {base['avg']} мс)")
        for policy in POLICIES:
            if policy in SINGLE:
                continue
            cur = level_results[policy]
            gain = (cur["rps"] / base["rps"] - 1) * 100
            speedup = base["avg"] / cur["avg"]
            print(f"  {POLICIES[policy]:<32}пропускная {gain:+6.1f}%   "
                  f"средняя задержка в {speedup:.2f} раза меньше")
        print()

    RESULTS_PATH.write_text(
        json.dumps({"traffic": TRAFFIC, "levels": results}, indent=2,
                   ensure_ascii=False), encoding="utf-8")
    print(f"Сохранено: {RESULTS_PATH}")


if __name__ == "__main__":
    asyncio.run(main())
