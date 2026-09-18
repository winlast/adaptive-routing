"""
Настройка и проверка оценок стоимости запроса.

Все четыре оценки настраиваются на одной и той же обучающей выборке и
проверяются на одной и той же отложенной, поэтому различие в точности
объясняется только видом зависимости, а не количеством данных:

  среднее по маршруту   — одно число на маршрут;
  линейная поправка     — показатель степени принят равным единице;
  степенной закон       — показатель подбирается по данным;
  нейронная сеть        — произвольная зависимость от тех же признаков.

Точность измеряется медианной относительной ошибкой. Средняя
относительная ошибка здесь непригодна: занятости различаются на два
порядка, и несколько тяжёлых запросов определяли бы всю оценку.

Отдельно печатается разбивка по маршрутам. Она существенна для выводов:
у маршрута с фиксированной стоимостью среднее по маршруту обязано быть
точным, и если это подтверждается — значит методика измеряет то, что
задумано, а не артефакт.
"""
from __future__ import annotations

import json
import os
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np

from core.cost_model import EndpointMeanCost, LinearParamCost
from core.predictor import (
    HOLDOUT_PATH_DEFAULT, NeuralCost, PowerLawCost, build_net, encode,
    feature_names, load_samples,
)
from core.workload import ENDPOINTS

WORKERS = tuple(os.environ.get("WORKERS", "sync,async").split(","))
EPOCHS = 400


def train_neural(samples: list[dict]) -> NeuralCost:
    import torch
    from sklearn.preprocessing import StandardScaler

    x = np.array([encode(r["endpoint"], r.get("param"), r["worker"], WORKERS)
                  for r in samples], dtype=np.float32)
    y = np.log(np.maximum(np.array(
        [[r["occupancy_ms"], r["block_ms"]] for r in samples],
        dtype=np.float32), 1.0))
    scaler = StandardScaler().fit(x)
    xs = torch.FloatTensor(scaler.transform(x))
    ys = torch.FloatTensor(y)

    model = build_net(x.shape[1])
    optimizer = torch.optim.Adam(model.parameters(), lr=0.01, weight_decay=1e-5)
    loss_fn = torch.nn.MSELoss()
    loss = None
    for _ in range(EPOCHS):
        optimizer.zero_grad()
        loss = loss_fn(model(xs), ys)
        loss.backward()
        optimizer.step()
    print(f"  сеть: итоговая ошибка на обучении {float(loss.detach()):.4f} "
          f"(в логарифмах)")
    return NeuralCost(model, scaler, WORKERS)


def median_ape(estimator, rows: list[dict], target: str = "cost") -> float:
    field = "occupancy_ms" if target == "cost" else "block_ms"
    predict = estimator.cost if target == "cost" else estimator.blocking
    errors = [
        abs(predict(r["endpoint"], r.get("param"), r["worker"]) - r[field])
        / max(r[field], 1.0) * 100
        for r in rows
    ]
    return statistics.median(errors) if errors else float("nan")


def main() -> None:
    train = load_samples()
    holdout = load_samples(HOLDOUT_PATH_DEFAULT)
    print(f"Обучающих замеров {len(train)}, отложенных {len(holdout)}\n")

    estimators = {
        "среднее по маршруту": EndpointMeanCost(train),
        "линейная поправка": LinearParamCost(train),
        "степенной закон": PowerLawCost.fit(train),
        "нейронная сеть": train_neural(train),
    }
    estimators["степенной закон"].save()
    estimators["нейронная сеть"].save()

    print("\nМедианная относительная ошибка на отложенной выборке:")
    print(f"{'оценка':<24}{'цена запроса':>16}{'блокировка':>14}")
    print("-" * 54)
    for name, est in estimators.items():
        print(f"{name:<24}{median_ape(est, holdout):>15.1f}%"
              f"{median_ape(est, holdout, 'blocking'):>13.1f}%")

    print(f"\nРазбивка по маршрутам (ошибка, %):")
    header = "".join(f"{n[:13]:>15}" for n in estimators)
    print(f"{'маршрут':<24}{header}")
    print("-" * (24 + 15 * len(estimators)))
    for endpoint in ENDPOINTS:
        rows = [r for r in holdout if r["endpoint"] == endpoint]
        if not rows:
            continue
        line = f"{endpoint:<24}"
        for est in estimators.values():
            line += f"{median_ape(est, rows):>14.1f}%"
        print(line)

    save_accuracy(estimators, holdout)

    print("\nПодобранные показатели степени зависимости цены от параметра")
    print("(степенной закон против истинных):")
    power = estimators["степенной закон"]
    print(f"{'маршрут':<24}{'истинный':>10}" +
          "".join(f"{w:>10}" for w in WORKERS))
    print("-" * (34 + 10 * len(WORKERS)))
    for endpoint, spec in ENDPOINTS.items():
        row = power.coeffs["cost"].get(endpoint, {})
        line = f"{endpoint:<24}{spec.exponent:>10.2f}"
        for w in WORKERS:
            line += f"{row.get(w, [0, 0])[1]:>10.2f}"
        print(line)


def save_accuracy(estimators, holdout) -> None:
    """Сохраняет точности для построения графика."""
    payload = {
        "overall": {name: round(median_ape(est, holdout), 1)
                    for name, est in estimators.items()},
        "overall_blocking": {name: round(median_ape(est, holdout, "blocking"), 1)
                             for name, est in estimators.items()},
        "by_endpoint": {},
    }
    for endpoint in ENDPOINTS:
        rows = [r for r in holdout if r["endpoint"] == endpoint]
        if rows:
            payload["by_endpoint"][endpoint] = {
                name: round(median_ape(est, rows), 1)
                for name, est in estimators.items()
            }
    path = Path(__file__).resolve().parent.parent / "data" / "estimator_accuracy.json"
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False),
                    encoding="utf-8")
    print(f"\nТочности сохранены: {path}")


if __name__ == "__main__":
    main()
