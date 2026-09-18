"""
Качество классификатора нагрузки, отделённое от шума нагрузочных замеров.

Нагрузочный эксперимент показывает итог, но его разброс между прогонами
достигает десятка процентов, и по нему трудно понять, чем именно один
источник оценки лучше другого. Здесь тот же вопрос задаётся напрямую и
без нагрузки.

Разыгрывается большой поток запросов из того же распределения, что и в
эксперименте. Для каждого запроса известна истинная блокировка event
loop — она берётся из таблицы профилирования, снятой по сетке значений
параметра. Классификатор с данным порогом решает, пускать ли запрос в
асинхронный движок; решение сравнивается с истиной.

Две ошибки имеют разную природу и разную цену.

  * Ошибочный пропуск — запрос сочли безопасным, а он останавливает
    event loop. Цена — задержка всех запросов, находящихся в нём в этот
    момент. Итог измеряется не числом ошибок, а суммарной блокировкой,
    попавшей в движок сверх допустимой.
  * Ошибочный отказ — запрос сочли опасным, хотя он безвреден. Цена —
    неиспользованная ёмкость асинхронного движка.

Главная величина — избыточная блокировка, пропущенная в event loop, в
миллисекундах на тысячу запросов. Она прямо отвечает на вопрос, ради
которого работа делается: во что обходится незнание того, насколько
тяжёл конкретный запрос.
"""
from __future__ import annotations

import json
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.cost_model import MeasuredCost, build_estimator
from core.workload import ENDPOINTS, sample_param
from experiments.load_generator import TRAFFIC_MIX

BASE_DIR = Path(__file__).resolve().parent.parent
OUTPUT = BASE_DIR / "data" / "classifier_quality.json"

SOURCES = {
    "endpoint_mean": "среднее по маршруту",
    "linear_param": "линейная поправка",
    "power_law": "степенной закон",
    "neural": "нейронная сеть",
}
BUDGETS = [15, 30, 60, 120, 250]
N_REQUESTS = 40_000
SEED = 20260918


def build_stream(n: int) -> list[tuple[str, int | None]]:
    rng = random.Random(SEED)
    endpoints = list(TRAFFIC_MIX)
    weights = [TRAFFIC_MIX[e] for e in endpoints]
    stream = []
    for _ in range(n):
        endpoint = rng.choices(endpoints, weights=weights, k=1)[0]
        stream.append((endpoint, sample_param(ENDPOINTS[endpoint], rng)))
    return stream


def main() -> None:
    truth = MeasuredCost()
    stream = build_stream(N_REQUESTS)
    true_block = [truth.blocking(e, p, "async") for e, p in stream]

    estimators = {name: build_estimator(name) for name in SOURCES}
    predicted = {
        name: [est.blocking(e, p, "async") for e, p in stream]
        for name, est in estimators.items()
    }

    results: dict = {}
    print(f"Поток {N_REQUESTS} запросов из рабочего распределения.\n")
    print(f"{'порог':>7}  {'классификатор':<22}"
          f"{'ошибочно пропущено':>20}{'ошибочно отклонено':>20}"
          f"{'избыточная блокировка':>24}")
    print("-" * 96)

    for budget in BUDGETS:
        should_admit = [b <= budget for b in true_block]
        ideal_excess = 0.0
        for name, title in SOURCES.items():
            admit = [p <= budget for p in predicted[name]]
            false_admit = sum(1 for a, s in zip(admit, should_admit)
                              if a and not s)
            false_reject = sum(1 for a, s in zip(admit, should_admit)
                               if not a and s)
            # Блокировка, попавшая в event loop сверх порога: именно она
            # и останавливает обработку остальных запросов.
            excess = sum(
                max(b - budget, 0.0)
                for a, b in zip(admit, true_block) if a
            )
            per_1000 = excess / N_REQUESTS * 1000
            results.setdefault(str(budget), {})[name] = {
                "false_admit_pct": round(false_admit / N_REQUESTS * 100, 2),
                "false_reject_pct": round(false_reject / N_REQUESTS * 100, 2),
                "excess_block_ms_per_1000": round(per_1000, 1),
                "admitted_pct": round(sum(admit) / N_REQUESTS * 100, 1),
            }
            print(f"{budget:>7}  {title:<22}"
                  f"{false_admit / N_REQUESTS * 100:>19.2f}%"
                  f"{false_reject / N_REQUESTS * 100:>19.2f}%"
                  f"{per_1000:>20.0f} мс")
        print()

    OUTPUT.write_text(json.dumps(results, indent=2, ensure_ascii=False),
                      encoding="utf-8")
    print(f"Сохранено: {OUTPUT}")


if __name__ == "__main__":
    main()
