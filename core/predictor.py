"""
Предсказание латентности запроса на каждом воркере.

Задача сформулирована как регрессия на фактически измеренных
длительностях, а не как классификация «типа запроса». Разница
принципиальная: метку типа мы раньше придумывали сами, и модель лишь
воспроизводила наше же правило, тогда как латентность — объективная
величина, измеренная секундомером, и её зависимость от состояния системы
модели приходится выучивать по-настоящему.

Признаки — только то, что доступно шлюзу до обработки запроса:
эндпоинт, размер тела, длины очередей всех воркеров и сам кандидат-воркер.
Фактический характер работы (сколько там CPU, сколько ожидания) модели не
сообщается — она должна вывести его сама.

Цель обучения — логарифм латентности: распределение времён имеет тяжёлый
правый хвост, и без логарифмирования редкие долгие запросы доминируют в
функции потерь. Для выбора воркера монотонное преобразование безразлично.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import joblib
import numpy as np
import torch
import torch.nn as nn

from core.policies import WORKERS, RequestFeatures
from core.workload import ENDPOINT_NAMES

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
MODEL_PATH = DATA_DIR / "latency_model.pth"
SCALER_PATH = DATA_DIR / "latency_scaler.pkl"
META_PATH = DATA_DIR / "latency_meta.json"

FEATURE_NAMES = (
    [f"endpoint={e}" for e in ENDPOINT_NAMES]
    + ["payload_bytes"]
    + [f"inflight_{w}" for w in WORKERS]
    + [f"pending_work_{w}" for w in WORKERS]
    + [f"recent_latency_{w}" for w in WORKERS]
    + [f"worker={w}" for w in WORKERS]
)
N_FEATURES = len(FEATURE_NAMES)


def encode(endpoint: str, payload_bytes: int, inflight: dict[str, int],
           worker: str, pending_work: dict[str, float] | None = None,
           recent_latency: dict[str, float] | None = None) -> list[float]:
    """Превращает состояние в вектор признаков."""
    pending_work = pending_work or {}
    recent_latency = recent_latency or {}
    row = [0.0] * N_FEATURES
    if endpoint in ENDPOINT_NAMES:
        row[ENDPOINT_NAMES.index(endpoint)] = 1.0
    offset = len(ENDPOINT_NAMES)
    row[offset] = float(payload_bytes)
    base = offset + 1
    for i, w in enumerate(WORKERS):
        row[base + i] = float(inflight.get(w, 0))
    base += len(WORKERS)
    for i, w in enumerate(WORKERS):
        row[base + i] = float(pending_work.get(w, 0.0))
    base += len(WORKERS)
    for i, w in enumerate(WORKERS):
        row[base + i] = float(recent_latency.get(w, 0.0))
    base += len(WORKERS)
    for i, w in enumerate(WORKERS):
        if w == worker:
            row[base + i] = 1.0
    return row


class LatencyNet(nn.Module):
    """Небольшая полносвязная сеть: признаки -> log(латентность)."""

    def __init__(self, n_features: int = N_FEATURES, hidden: int = 64):
        super().__init__()
        self.net = nn.Sequential(
            nn.Linear(n_features, hidden),
            nn.ReLU(),
            nn.Linear(hidden, hidden // 2),
            nn.ReLU(),
            nn.Linear(hidden // 2, 1),
        )

    def forward(self, x):
        return self.net(x).squeeze(-1)


class LatencyPredictor:
    """Обёртка над обученной моделью для использования в политике."""

    def __init__(self, model: LatencyNet, scaler):
        self.model = model
        self.scaler = scaler
        self.model.eval()

    def predict_all_workers(self, features: RequestFeatures) -> dict[str, float]:
        """Предсказывает латентность на каждом воркере для одного запроса."""
        rows = [
            encode(features.endpoint, features.payload_bytes,
                   features.inflight, worker, features.pending_work,
                   features.recent_latency)
            for worker in WORKERS
        ]
        scaled = self.scaler.transform(np.array(rows, dtype=np.float32))
        with torch.no_grad():
            preds = self.model(torch.FloatTensor(scaled)).numpy()
        return {w: float(math.exp(p)) for w, p in zip(WORKERS, preds)}

    def save(self) -> None:
        DATA_DIR.mkdir(exist_ok=True)
        torch.save(self.model.state_dict(), MODEL_PATH)
        joblib.dump(self.scaler, SCALER_PATH)
        META_PATH.write_text(
            json.dumps({"features": FEATURE_NAMES}, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    @classmethod
    def load(cls) -> "LatencyPredictor":
        if not MODEL_PATH.exists():
            raise FileNotFoundError(
                f"Модель не найдена: {MODEL_PATH}. "
                "Сначала выполните experiments/train_predictor.py"
            )
        model = LatencyNet()
        model.load_state_dict(torch.load(MODEL_PATH, weights_only=True))
        scaler = joblib.load(SCALER_PATH)
        return cls(model, scaler)
