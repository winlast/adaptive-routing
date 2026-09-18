"""
Основной эксперимент: цена ошибки в оценке нагрузки запроса.

Проверяется не «какая политика лучше», а вопрос, ради которого работа и
делается: что даёт умение оценить, насколько тяжёл конкретный запрос.

Все политики устроены одинаково. Каждая допускает запрос в асинхронный
движок только если предсказанная блокировка event loop не превышает
порога, а среди допустимых движков выбирает менее занятый. Различаются
политики единственным — источником оценки блокировки:

    среднее по маршруту   одно число на маршрут, разделить запросы
                          внутри маршрута невозможно;
    линейная поправка     экспертное правило «пропорционально параметру»;
    степенной закон       показатель степени выведен из замеров;
    нейронная сеть        произвольная зависимость от признаков запроса;
    точная таблица        профилирование по всей сетке, верхняя граница.

Порог пробегает набор значений. Каждому порогу отвечает своя точка на
плоскости «пропускная способность — хвост задержек лёгких запросов»: чем
строже порог, тем меньше работы попадает в асинхронный движок, тем
чище его event loop и тем хуже используется ёмкость системы. Набор точек
образует достижимую границу, и сравниваются именно границы, а не
отдельные числа: это избавляет сравнение от произвола в выборе рабочей
точки.

Для отсчёта в замер включены два режима без маршрутизации (весь трафик в
один движок) и least-connections — балансировщик, не различающий запросы.
"""
from __future__ import annotations

import asyncio
import json
import os
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from experiments.benchmark_policies import start_gateway, stop_gateway
from experiments.load_generator import run_load

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
RESULTS_PATH = DATA_DIR / "budget_frontier.json"

SOURCES = {
    "endpoint": "среднее по маршруту",
    "linear": "линейная поправка",
    "power": "степенной закон",
    "neural": "нейронная сеть",
    "measured": "точная таблица",
}
BUDGETS = [int(x) for x in
           os.environ.get("BUDGETS", "15,30,60,120,250").split(",")]
REFERENCE = ["all_sync", "all_async", "least_conn"]

CONCURRENCY = int(os.environ.get("CONCURRENCY", "32"))
REQUESTS = int(os.environ.get("REQUESTS", "400"))
REPEATS = int(os.environ.get("REPEATS", "3"))
SEED = 4100

# Порог, по которому считается доля пострадавших лёгких обращений.
# Лёгкое обращение выполняется за 15-30 мс, поэтому ответ дольше 200 мс
# означает, что запрос простоял за чужой работой, а не выполнялся.
LIGHT_BUDGET_MS = 200.0


async def measure(policy: str) -> dict:
    runs, light, everything = [], [], []
    for repeat in range(REPEATS):
        proc = start_gateway(policy)
        try:
            await run_load(4, 30, seed=1)
            await asyncio.sleep(1.2)
            res = await run_load(CONCURRENCY, REQUESTS, seed=SEED + repeat)
            if "rps" not in res:
                raise RuntimeError(f"{policy}: ни один запрос не прошёл")
            everything += res.pop("_latencies", [])
            light += res.pop("_light_latencies", [])
            runs.append(res)
        finally:
            stop_gateway(proc)
        await asyncio.sleep(1)

    light.sort()
    everything.sort()

    def pct(values, p):
        return values[min(int(len(values) * p), len(values) - 1)]

    # Доля лёгких обращений, ответ на которые занял больше порога.
    # Устойчивее процентиля: оценивается по всей объединённой выборке,
    # тогда как 99-й процентиль на четырёх сотнях наблюдений опирается
    # на единицы значений и скачет от прогона к прогону.
    light_slow = sum(1 for v in light if v > LIGHT_BUDGET_MS) / len(light) * 100

    return {
        "light_over_budget_pct": round(light_slow, 1),
        "rps": round(statistics.mean(r["rps"] for r in runs), 2),
        "rps_std": round(statistics.stdev(r["rps"] for r in runs), 2)
        if len(runs) > 1 else 0.0,
        "p95": round(pct(everything, 0.95), 1),
        "p99": round(pct(everything, 0.99), 1),
        "light_p95": round(pct(light, 0.95), 1),
        "light_p99": round(pct(light, 0.99), 1),
        "light_median": round(pct(light, 0.50), 1),
        "slo": round(statistics.mean(r["slo_violation_rate"] for r in runs), 1),
        "n_light": len(light),
    }


async def main() -> None:
    results: dict[str, dict] = {}

    print(f"Конкурентность {CONCURRENCY}, {REQUESTS} запросов, "
          f"{REPEATS} повтора на точку\n")
    print(f"{'режим':<34}{'rps':>14}{'p99, мс':>10}{'лёгкие p95':>13}"
          f"{'лёгкие >200мс':>15}{'SLO%':>8}")
    print("-" * 94)

    for policy in REFERENCE:
        res = await measure(policy)
        results[policy] = res
        print(f"{policy:<34}{res['rps']:>8.1f} ±{res['rps_std']:<4.1f}"
              f"{res['p99']:>10.0f}{res['light_p95']:>13.0f}"
              f"{res['light_over_budget_pct']:>14.1f}%{res['slo']:>8.1f}",
              flush=True)

    for source, title in SOURCES.items():
        print()
        for budget in BUDGETS:
            policy = f"budget_{source}_{budget}"
            res = await measure(policy)
            results[policy] = res
            label = f"{title}, порог {budget} мс"
            print(f"{label:<34}{res['rps']:>8.1f} ±{res['rps_std']:<4.1f}"
                  f"{res['p99']:>10.0f}{res['light_p95']:>13.0f}"
                  f"{res['light_over_budget_pct']:>14.1f}%{res['slo']:>8.1f}",
                  flush=True)

    RESULTS_PATH.write_text(json.dumps(results, indent=2, ensure_ascii=False),
                            encoding="utf-8")
    print(f"\nСохранено: {RESULTS_PATH}")


if __name__ == "__main__":
    asyncio.run(main())
