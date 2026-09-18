"""
Обучаемые оценки стоимости запроса.

Оцениваемая величина — занятость движка, которую создаст запрос:
собственное время обработки плюс задержка, причинённая остальным
запросам на том же движке. Признаки — только то, что видит прокси до
обработки: маршрут, значение параметра запроса, размер тела и
движок-кандидат. Характер работы (сколько там вычислений, сколько
ожидания ввода-вывода) модели не сообщается, она выводит его сама.

Реализованы две обучаемые оценки существенно разной сложности, и это
сделано намеренно. Заявлять необходимость нейронной сети, не проверив
двухпараметрическую регрессию, нельзя: если степенной закон достаточен,
об этом следует написать прямо.

  * `PowerLawCost` — для каждой пары «маршрут, движок» методом
    наименьших квадратов подбирается степенная зависимость
    `занятость = a * параметр^b`. Показатель `b` не задаётся, а
    выводится из данных: именно этим оценка отличается от экспертного
    правила, где он жёстко принят равным единице.

  * `NeuralCost` — полносвязная сеть на тех же признаках. Способна
    описать зависимости, не сводящиеся к степенному закону, и
    использовать размер тела запроса.

Целевая величина логарифмируется: занятости различаются на два порядка,
и без логарифмирования редкие тяжёлые запросы полностью определяли бы
функцию потерь.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np

from core.workload import ENDPOINTS, ENDPOINT_NAMES

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
SAMPLES_PATH = DATA_DIR / "cost_samples.json"
HOLDOUT_PATH_DEFAULT = DATA_DIR / "cost_samples_holdout.json"
POWER_PATH = DATA_DIR / "power_law.json"
NEURAL_PATH = DATA_DIR / "cost_model.pth"
NEURAL_META = DATA_DIR / "cost_model_meta.json"

MIN_COST_MS = 1.0


def load_samples(path: Path = SAMPLES_PATH) -> list[dict]:
    if not path.exists():
        raise FileNotFoundError(
            f"Нет замеров: {path}. Сначала выполните "
            "experiments/collect_cost_samples.py"
        )
    return json.loads(path.read_text(encoding="utf-8"))


def _log_param(endpoint: str, param: float | None) -> float:
    """Логарифм параметра, нормированный на его базовое значение."""
    spec = ENDPOINTS.get(endpoint)
    if spec is None or not spec.param_name or param is None:
        return 0.0
    return math.log(max(float(param), 1.0) / float(spec.param_base))


# ---------------------------------------------------------------------------
# Степенной закон
# ---------------------------------------------------------------------------


class PowerLawCost:
    """Степенная зависимость занятости от параметра, по паре на маршрут."""

    name = "power_law"

    def __init__(self, coeffs: dict):
        # coeffs[target][endpoint][worker] = [log_a, b], где target —
        # либо полная цена запроса, либо только блокировка движка.
        self.coeffs = coeffs

    @classmethod
    def _fit_field(cls, samples: list[dict], field: str) -> dict:
        grouped: dict[tuple[str, str], list[tuple[float, float]]] = {}
        for row in samples:
            key = (row["endpoint"], row["worker"])
            grouped.setdefault(key, []).append(
                (_log_param(row["endpoint"], row.get("param")),
                 math.log(max(row[field], MIN_COST_MS)))
            )
        coeffs: dict[str, dict[str, list[float]]] = {}
        for (endpoint, worker), points in grouped.items():
            xs = np.array([p[0] for p in points])
            ys = np.array([p[1] for p in points])
            if xs.std() < 1e-9:
                # Параметр не влияет (или не менялся) — остаётся среднее.
                b, log_a = 0.0, float(ys.mean())
            else:
                b, log_a = np.polyfit(xs, ys, 1)
            coeffs.setdefault(endpoint, {})[worker] = [float(log_a), float(b)]
        return coeffs

    @classmethod
    def fit(cls, samples: list[dict]) -> "PowerLawCost":
        return cls({"cost": cls._fit_field(samples, "occupancy_ms"),
                    "blocking": cls._fit_field(samples, "block_ms")})

    def _predict(self, target: str, endpoint: str, param: float | None,
                 worker: str) -> float:
        row = self.coeffs.get(target, {}).get(endpoint, {}).get(worker)
        if row is None:
            return 120.0
        log_a, b = row
        return max(math.exp(log_a + b * _log_param(endpoint, param)),
                   MIN_COST_MS)

    def cost(self, endpoint: str, param: float | None, worker: str) -> float:
        return self._predict("cost", endpoint, param, worker)

    def blocking(self, endpoint: str, param: float | None,
                 worker: str) -> float:
        return self._predict("blocking", endpoint, param, worker)

    def save(self, path: Path = POWER_PATH) -> None:
        DATA_DIR.mkdir(exist_ok=True)
        path.write_text(json.dumps(self.coeffs, indent=2, ensure_ascii=False),
                        encoding="utf-8")

    @classmethod
    def load(cls, path: Path = POWER_PATH) -> "PowerLawCost":
        return cls(json.loads(path.read_text(encoding="utf-8")))


# ---------------------------------------------------------------------------
# Нейронная сеть
# ---------------------------------------------------------------------------


def feature_names(workers: tuple[str, ...]) -> list[str]:
    return (
        [f"маршрут={e}" for e in ENDPOINT_NAMES]
        + ["log(параметр)", "log(параметр)^2", "размер тела"]
        + [f"движок={w}" for w in workers]
    )


def encode(endpoint: str, param: float | None, worker: str,
           workers: tuple[str, ...]) -> list[float]:
    spec = ENDPOINTS.get(endpoint)
    lp = _log_param(endpoint, param)
    row = [0.0] * len(ENDPOINT_NAMES)
    if endpoint in ENDPOINT_NAMES:
        row[ENDPOINT_NAMES.index(endpoint)] = 1.0
    row += [lp, lp * lp, float(spec.payload_bytes) / 1000.0 if spec else 0.0]
    row += [1.0 if w == worker else 0.0 for w in workers]
    return row


class NeuralCost:
    """Полносвязная сеть, предсказывающая логарифм занятости."""

    name = "neural"

    def __init__(self, model, scaler, workers: tuple[str, ...]):
        self.model = model
        self.scaler = scaler
        self.workers = workers
        self.model.eval()

    def _predict(self, endpoint: str, param: float | None,
                 worker: str) -> tuple[float, float]:
        import torch

        row = np.array([encode(endpoint, param, worker, self.workers)],
                       dtype=np.float32)
        scaled = self.scaler.transform(row)
        with torch.no_grad():
            out = self.model(torch.FloatTensor(scaled))[0]
        return (max(math.exp(float(out[0])), MIN_COST_MS),
                max(math.exp(float(out[1])), MIN_COST_MS))

    def cost(self, endpoint: str, param: float | None, worker: str) -> float:
        return self._predict(endpoint, param, worker)[0]

    def blocking(self, endpoint: str, param: float | None,
                 worker: str) -> float:
        return self._predict(endpoint, param, worker)[1]

    def save(self) -> None:
        import torch

        DATA_DIR.mkdir(exist_ok=True)
        torch.save(self.model.state_dict(), NEURAL_PATH)
        import joblib

        joblib.dump(self.scaler, DATA_DIR / "cost_scaler.pkl")
        NEURAL_META.write_text(
            json.dumps({"workers": list(self.workers),
                        "features": feature_names(self.workers)},
                       indent=2, ensure_ascii=False),
            encoding="utf-8")

    @classmethod
    def load(cls) -> "NeuralCost":
        import joblib
        import torch

        meta = json.loads(NEURAL_META.read_text(encoding="utf-8"))
        workers = tuple(meta["workers"])
        model = build_net(len(meta["features"]))
        model.load_state_dict(torch.load(NEURAL_PATH, weights_only=True))
        scaler = joblib.load(DATA_DIR / "cost_scaler.pkl")
        return cls(model, scaler, workers)


def build_net(n_features: int, hidden: int = 48):
    """
    Сеть с двумя выходами: логарифм полной цены запроса и логарифм
    блокировки движка.

    Обе величины предсказываются одной сетью с общими скрытыми слоями,
    потому что они зависят от одних и тех же свойств запроса. Раздельные
    сети обучались бы на тех же признаках дважды без выигрыша.
    """
    import torch.nn as nn

    class Net(nn.Module):
        def __init__(self):
            super().__init__()
            self.net = nn.Sequential(
                nn.Linear(n_features, hidden),
                nn.ReLU(),
                nn.Linear(hidden, hidden // 2),
                nn.ReLU(),
                nn.Linear(hidden // 2, 2),
            )

        def forward(self, x):
            return self.net(x)

    return Net()


# Имя, под которым обученная оценка подключается к политике.
CostPredictor = PowerLawCost
