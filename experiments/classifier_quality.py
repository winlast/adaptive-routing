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

# Доли трафика, которые требуется пропустить в асинхронный движок. Для
# каждой оценки подбирается свой порог, дающий именно эту долю. Без
# такого выравнивания сравнение оценок при одном пороге сравнивает не
# точность классификаторов, а разные рабочие режимы: строгая оценка
# пропускает меньше работы, отчего у неё лучше хвост и хуже пропускная
# способность — и это ничего не говорит о том, правильные ли запросы
# она выбрала.
TARGET_SHARES = [0.5, 0.6, 0.7, 0.8]
N_REQUESTS = 40_000
SEED = 20260918


def build_stream(n: int, skew: float | None = None,
                 seed: int = SEED) -> list[tuple[str, int | None]]:
    """
    Разыгрывает поток запросов. `skew` задаёт смещение распределения
    параметра: чем он меньше, тем чаще встречаются крупные значения.
    """
    import core.workload as workload

    original = workload.PARAM_SKEW
    if skew is not None:
        workload.PARAM_SKEW = skew
    try:
        rng = random.Random(seed)
        endpoints = list(TRAFFIC_MIX)
        weights = [TRAFFIC_MIX[e] for e in endpoints]
        stream = []
        for _ in range(n):
            endpoint = rng.choices(endpoints, weights=weights, k=1)[0]
            stream.append((endpoint, sample_param(ENDPOINTS[endpoint], rng)))
        return stream
    finally:
        workload.PARAM_SKEW = original


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

    calibrated = calibrate(stream, true_block, predicted)
    results["calibrated"] = calibrated
    results["shifted"] = shift_test(truth, estimators, calibrated)
    OUTPUT.write_text(json.dumps(results, indent=2, ensure_ascii=False),
                      encoding="utf-8")
    print(f"Сохранено: {OUTPUT}")


def calibrate(stream, true_block, predicted) -> dict:
    """
    Подбирает каждой оценке порог, дающий заданную долю трафика в
    асинхронном движке, и сравнивает оценки при равной доле.

    При равной доле пропущенной работы пропускная способность
    выравнивается, и остаётся единственное различие — правильно ли
    выбраны именно те запросы, которые безопасны для event loop.
    """
    n = len(stream)
    out: dict = {}
    print("\nПри РАВНОЙ доле трафика в асинхронном движке:")
    print(f"{'доля':>6}  {'классификатор':<22}{'порог, мс':>11}"
          f"{'блокировки в event loop':>26}{'самая тяжёлая':>16}")
    print("-" * 84)
    for share in TARGET_SHARES:
        target = int(n * share)
        for name, title in SOURCES.items():
            order = sorted(range(n), key=lambda i: predicted[name][i])
            admitted = order[:target]
            budget = predicted[name][order[target - 1]]
            total_block = sum(true_block[i] for i in admitted)
            worst = max(true_block[i] for i in admitted)
            out.setdefault(f"{share:.1f}", {})[name] = {
                "budget_ms": round(budget, 1),
                "admitted_block_ms_per_1000": round(total_block / n * 1000, 1),
                "worst_admitted_block_ms": round(worst, 1),
            }
            print(f"{share:>6.0%}  {title:<22}{budget:>11.0f}"
                  f"{total_block / n * 1000:>22.0f} мс{worst:>13.0f} мс")
        print()
    return out


def shift_test(truth, estimators, calibrated, share: str = "0.7") -> dict:
    """
    Что происходит с порогом, подобранным под один трафик, когда трафик
    изменился.

    Порог, найденный опытным путём, описывает не запросы, а конкретное
    распределение запросов. Пока оно держится, грубая оценка с
    подкрученным порогом работает не хуже точной. Как только клиенты
    начинают запрашивать более крупные выборки — включился ночной
    пересчёт, подключился новый потребитель, изменилась выдача, — порог
    перестаёт означать то, ради чего его ставили.

    Здесь пороги берутся подобранными на исходном трафике, а
    оцениваются на трафике, смещённом к крупным значениям параметра.
    Оценка, которая верно предсказывает саму величину блокировки, от
    смещения не страдает: её порог по-прежнему означает «не более
    стольких миллисекунд». Оценка с неверной формой зависимости
    страдает тем сильнее, чем дальше ушло распределение.
    """
    shifted = build_stream(N_REQUESTS, skew=0.7, seed=SEED + 1)
    true_block = [truth.blocking(e, p, "async") for e, p in shifted]
    n = len(shifted)

    print("\nПороги подобраны на исходном трафике, трафик сместился "
          "к крупным запросам:")
    print(f"{'классификатор':<22}{'порог, мс':>11}{'доля в async':>14}"
          f"{'блокировки в event loop':>26}{'самая тяжёлая':>16}")
    print("-" * 89)

    out: dict = {}
    for name, title in SOURCES.items():
        budget = calibrated[share][name]["budget_ms"]
        predicted = [estimators[name].blocking(e, p, "async")
                     for e, p in shifted]
        admitted = [i for i in range(n) if predicted[i] <= budget]
        if not admitted:
            continue
        total = sum(true_block[i] for i in admitted)
        worst = max(true_block[i] for i in admitted)
        out[name] = {
            "budget_ms": budget,
            "admitted_pct": round(len(admitted) / n * 100, 1),
            "admitted_block_ms_per_1000": round(total / n * 1000, 1),
            "worst_admitted_block_ms": round(worst, 1),
        }
        print(f"{title:<22}{budget:>11.0f}{len(admitted) / n * 100:>13.1f}%"
              f"{total / n * 1000:>22.0f} мс{worst:>13.0f} мс")
    return out


if __name__ == "__main__":
    main()
