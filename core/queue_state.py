"""
Учёт содержимого очередей движков, а не только их длины.

Least-connections опирается на одно число — сколько запросов сейчас
обрабатывает движок. Но пять обращений по 10 мс и пять по 900 мс дают
одинаковую пятёрку, хотя движок освободится в первом случае через
десятки миллисекунд, а во втором почти через пять секунд. Эта потеря
информации и есть слабое место счётчика соединений.

Здесь шлюз помнит, какие именно запросы сейчас выполняются на каждом
движке, и оценивает оставшуюся занятость: для каждого запроса берётся
его ожидаемая занятость и вычитается уже отработанное время. Сумма даёт
ожидаемое время освобождения движка.

Важно, что оценка занятости берётся из того же источника, что и у
политики. Благодаря этому сравнение политик остаётся сравнением
источников оценки: политика с грубой оценкой одинаково грубо и
взвешивает очередь, и оценивает новый запрос — ровно так, как это
произошло бы в реальной системе.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass


@dataclass
class ActiveRequest:
    endpoint: str
    param: float | None
    started_at: float
    expected_ms: float


class QueueTracker:
    """Потокобезопасный учёт активных запросов по движкам."""

    def __init__(self, workers: tuple[str, ...], estimator):
        self._workers = workers
        self._estimator = estimator
        self._active: dict[str, dict[int, ActiveRequest]] = {
            w: {} for w in workers
        }
        self._lock = threading.Lock()
        self._counter = 0
        self._recent_latency: dict[str, float] = {}

    def add(self, worker: str, endpoint: str, param: float | None) -> int:
        """Регистрирует начало обработки, возвращает идентификатор записи."""
        expected = self._estimator.cost(endpoint, param, worker)
        with self._lock:
            self._counter += 1
            token = self._counter
            self._active[worker][token] = ActiveRequest(
                endpoint=endpoint, param=param,
                started_at=time.perf_counter(), expected_ms=expected,
            )
            return token

    def remove(self, worker: str, token: int) -> None:
        with self._lock:
            self._active[worker].pop(token, None)

    def counts(self) -> dict[str, int]:
        """Число активных запросов — то, что видит least-connections."""
        with self._lock:
            return {w: len(self._active[w]) for w in self._workers}

    def pending_work(self) -> dict[str, float]:
        """
        Оценка оставшейся занятости каждого движка в миллисекундах.

        Из ожидаемой занятости каждого активного запроса вычитается уже
        прошедшее время: запрос, отработавший почти всю свою длительность,
        скоро освободит движок и не должен весить столько же, сколько
        только что поступивший.
        """
        now = time.perf_counter()
        with self._lock:
            result = {}
            for worker in self._workers:
                total = 0.0
                for req in self._active[worker].values():
                    elapsed = (now - req.started_at) * 1000
                    total += max(req.expected_ms - elapsed, 0.0)
                result[worker] = total
            return result

    def observe_latency(self, worker: str, latency_ms: float,
                        alpha: float = 0.2) -> None:
        """Скользящая оценка фактической скорости ответа движка."""
        with self._lock:
            previous = self._recent_latency.get(worker)
            self._recent_latency[worker] = (
                latency_ms if previous is None
                else (1 - alpha) * previous + alpha * latency_ms
            )

    def recent_latency(self) -> dict[str, float]:
        with self._lock:
            return {w: self._recent_latency.get(w, 0.0) for w in self._workers}

    def snapshot(self) -> tuple[dict[str, int], dict[str, float]]:
        """Согласованный снимок длин очередей и занятости движков."""
        return self.counts(), self.pending_work()
