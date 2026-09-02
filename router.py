"""
Модуль адаптивной маршрутизации.

AdaptiveRouter поддерживает два режима принятия решения:
  - 'heuristic': простое правило load > threshold
  - 'neural':    предсказание обученной нейросети (RouterClassifier)

Дополнительно:
  - логирование каждого решения в CSV (для последующего анализа в научной работе)
  - накопление метрик (confusion-style статистика по бэкендам)
  - замер времени принятия решения (мс)
  - клиппинг cpu_usage в диапазон [0, 100]
"""
import csv
import os
import queue
import threading
import time
from datetime import datetime
from typing import Dict, Any, Literal, Optional

import joblib
import torch
torch.set_num_threads(1)
import torch.nn as nn

Backend = Literal["flask", "fastapi"]
Mode = Literal["heuristic", "neural"]

DEFAULT_LOAD = 0
DEFAULT_IO_INTENSITY = 0.1
DEFAULT_CPU_USAGE = 50.0

DECISION_THRESHOLD = 0.5
HEURISTIC_LOAD_THRESHOLD = 50

CPU_MIN, CPU_MAX = 0.0, 100.0

LOG_FIELDS = [
    "timestamp",
    "mode",
    "load",
    "io_intensity",
    "cpu_usage",
    "probability",
    "backend",
    "decision_time_ms",
]


class RouterClassifier(nn.Module):
    """Архитектура должна точно совпадать с той, на которой обучалась модель."""

    def __init__(self):
        super(RouterClassifier, self).__init__()
        self.layer1 = nn.Linear(3, 16)
        self.relu1 = nn.ReLU()
        self.layer2 = nn.Linear(16, 8)
        self.relu2 = nn.ReLU()
        self.output = nn.Linear(8, 1)
        self.sigmoid = nn.Sigmoid()

    def forward(self, x):
        x = self.relu1(self.layer1(x))
        x = self.relu2(self.layer2(x))
        x = self.sigmoid(self.output(x))
        return x


class AdaptiveRouter:
    def __init__(
        self,
        model_path: str = "router_model.pth",
        scaler_path: str = "scaler.pkl",
        mode: Mode = "neural",
        log_path: str = "decisions_log.csv",
        heuristic_load_threshold: int = HEURISTIC_LOAD_THRESHOLD,
    ):
        if mode not in ("heuristic", "neural"):
            raise ValueError(f"Неизвестный режим: {mode}. Допустимо: 'heuristic', 'neural'.")

        self.mode: Mode = mode
        self.heuristic_load_threshold = heuristic_load_threshold
        self.log_path = log_path

        # Модель загружаем всегда, если файлы есть — это позволяет
        # переключаться в режим 'neural' на лету без пересоздания роутера.
        # Но если файлов нет и режим 'heuristic' — не падаем.
        self.model: Optional[RouterClassifier] = None
        self.scaler = None

        if mode == "neural" or (os.path.exists(model_path) and os.path.exists(scaler_path)):
            if not os.path.exists(model_path):
                raise FileNotFoundError(f"Файл модели не найден: {model_path}")
            if not os.path.exists(scaler_path):
                raise FileNotFoundError(f"Файл scaler не найден: {scaler_path}")

            self.scaler = joblib.load(scaler_path)
            self.model = RouterClassifier()
            self.model.load_state_dict(torch.load(model_path, weights_only=True))
            self.model.eval()

         # Метрики в памяти (для /stats и для итогового анализа)
        self._history: list[Dict[str, Any]] = []

        self._init_log_file()

        # Логирование вынесено в отдельный поток с очередью: decide_backend()
        # кладёт запись в очередь (мгновенно, не блокирует), а фоновый поток
        # пишет в CSV независимо. Убирает файловый I/O с "горячего пути"
        # обработки запроса — критично под конкурентной нагрузкой.
        self._log_queue: "queue.Queue" = queue.Queue()
        self._log_thread = threading.Thread(target=self._log_worker, daemon=True)
        self._log_thread.start()

    # ------------------------------------------------------------------
    # Логирование
    # ------------------------------------------------------------------

    def _init_log_file(self) -> None:
        """Создаёт CSV-файл с заголовком, если его ещё нет."""
        file_exists = os.path.exists(self.log_path)
        if not file_exists:
            with open(self.log_path, "w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=LOG_FIELDS)
                writer.writeheader()

    def _log_worker(self) -> None:
        """Работает в фоновом потоке: разгребает очередь и пишет в CSV."""
        while True:
            entry = self._log_queue.get()
            try:
                with open(self.log_path, "a", newline="", encoding="utf-8") as f:
                    writer = csv.DictWriter(f, fieldnames=LOG_FIELDS)
                    writer.writerow(entry)
            except Exception as e:
                print(f"[AdaptiveRouter] Ошибка записи лога: {e}")
            finally:
                self._log_queue.task_done()

    def _log_decision(self, entry: Dict[str, Any]) -> None:
        # Неблокирующая постановка в очередь вместо синхронной записи в файл.
        self._log_queue.put(entry)

    # ------------------------------------------------------------------
    # Извлечение и нормализация признаков
    # ------------------------------------------------------------------

    def _extract_features(self, request_data: Dict[str, Any]) -> Dict[str, float]:
        def safe_get(key: str, default: float) -> float:
            value = request_data.get(key, default)
            try:
                return float(value)
            except (TypeError, ValueError):
                return default

        load = safe_get("load", DEFAULT_LOAD)
        io_intensity = safe_get("io_intensity", DEFAULT_IO_INTENSITY)
        cpu_usage = safe_get("cpu_usage", DEFAULT_CPU_USAGE)

        # Клиппинг cpu_usage в допустимый диапазон
        cpu_usage = max(CPU_MIN, min(CPU_MAX, cpu_usage))

        return {"load": load, "io_intensity": io_intensity, "cpu_usage": cpu_usage}

    # ------------------------------------------------------------------
    # Принятие решения
    # ------------------------------------------------------------------

    def _decide_heuristic(self, features: Dict[str, float]) -> tuple[Backend, Optional[float]]:
        backend: Backend = "fastapi" if features["load"] > self.heuristic_load_threshold else "flask"
        return backend, None  # у эвристики нет "вероятности"

    def _decide_neural(self, features: Dict[str, float]) -> tuple[Backend, Optional[float]]:
        if self.model is None or self.scaler is None:
            raise RuntimeError(
                "Режим 'neural' выбран, но модель/scaler не загружены. "
                "Проверьте пути model_path/scaler_path."
            )

        vector = [features["load"], features["io_intensity"], features["cpu_usage"]]
        features_scaled = self.scaler.transform([vector])
        features_tensor = torch.FloatTensor(features_scaled)

        with torch.no_grad():
            probability = self.model(features_tensor).item()

        backend: Backend = "fastapi" if probability >= DECISION_THRESHOLD else "flask"
        return backend, probability

    def decide_backend(
        self,
        request_data: Dict[str, Any],
        mode_override: Optional[Mode] = None,
    ) -> Dict[str, Any]:
        """
        Принимает решение о бэкенде.

        Возвращает словарь:
            {
                "backend": "flask" | "fastapi",
                "probability": float | None,   # None для heuristic
                "mode": "heuristic" | "neural",
                "decision_time_ms": float,
            }

        mode_override позволяет переопределить режим на конкретный запрос
        (нужно для A/B-тестирования — см. main.py).
        """
        mode = mode_override or self.mode
        features = self._extract_features(request_data)

        start = time.perf_counter()

        try:
            if mode == "heuristic":
                backend, probability = self._decide_heuristic(features)
            else:
                backend, probability = self._decide_neural(features)
        except Exception as e:
            print(f"[AdaptiveRouter] Ошибка при режиме '{mode}': {e}. Fallback на 'flask'.")
            backend, probability = "flask", None

        decision_time_ms = (time.perf_counter() - start) * 1000

        entry = {
            "timestamp": datetime.utcnow().isoformat(),
            "mode": mode,
            "load": features["load"],
            "io_intensity": features["io_intensity"],
            "cpu_usage": features["cpu_usage"],
            "probability": probability,
            "backend": backend,
            "decision_time_ms": round(decision_time_ms, 4),
        }

        self._history.append(entry)
        # Ограничиваем размер истории в памяти — полная история и так
        # пишется в CSV через _log_decision(). Без этого лимита список
        # растёт неограниченно за время долгого теста, увеличивая нагрузку
        # на сборщик мусора Python и потенциально замедляя event loop.
        if len(self._history) > 2000:
            self._history = self._history[-2000:]
        self._log_decision(entry)

        return {
            "backend": backend,
            "probability": probability,
            "mode": mode,
            "decision_time_ms": entry["decision_time_ms"],
        }

    # ------------------------------------------------------------------
    # Метрики
    # ------------------------------------------------------------------

    def stats(self) -> Dict[str, Any]:
        """
        Возвращает агрегированную статистику по всем накопленным решениям:
          - counts: сколько раз выбран каждый бэкенд
          - avg_probability_fastapi: средняя вероятность FastAPI (только для neural-решений)
          - avg_decision_time_ms: среднее время принятия решения по каждому режиму
        """
        counts = {"flask": 0, "fastapi": 0}
        probs = []
        decision_times_by_mode: Dict[str, list] = {"heuristic": [], "neural": []}

        for entry in self._history:
            counts[entry["backend"]] += 1
            if entry["probability"] is not None:
                probs.append(entry["probability"])
            decision_times_by_mode.setdefault(entry["mode"], []).append(entry["decision_time_ms"])

        avg_prob = sum(probs) / len(probs) if probs else None

        avg_times = {
            mode: (sum(times) / len(times) if times else None)
            for mode, times in decision_times_by_mode.items()
        }

        return {
            "total_requests": len(self._history),
            "counts": counts,
            "avg_probability_fastapi": avg_prob,
            "avg_decision_time_ms": avg_times,
        }