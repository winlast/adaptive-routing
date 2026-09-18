"""
Учёт содержимого очередей воркеров, а не только их длины.

Least-connections и первая версия нашей модели опираются на одно число —
сколько запросов сейчас висит на воркере. Но пять лёгких обращений к
внешнему сервису и пять тяжёлых вычислений дают одинаковую пятёрку, хотя
воркер освободится в первом случае через десятки миллисекунд, а во втором
через секунду. Эта потеря информации и есть слабое место обеих политик.

Здесь шлюз отслеживает, какие именно запросы сейчас выполняются на каждом
воркере, и оценивает оставшийся объём работы: для каждого запроса берётся
его ожидаемая стоимость на этом воркере и вычитается уже отработанное
время. Сумма по всем активным запросам даёт ожидаемое время освобождения
воркера — величину, которой у least-connections нет по построению.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field


@dataclass
class ActiveRequest:
    endpoint: str
    started_at: float
    expected_ms: float


class QueueTracker:
    """Потокобезопасный учёт активных запросов по воркерам."""

    def __init__(self, workers: tuple[str, ...],
                 cost_table: dict[str, dict[str, float]] | None = None,
                 default_cost_ms: float = 150.0):
        self._workers = workers
        self._cost_table = cost_table or {}
        self._default_cost = default_cost_ms
        self._active: dict[str, dict[int, ActiveRequest]] = {
            w: {} for w in workers
        }
        self._lock = threading.Lock()
        self._counter = 0

    def expected_cost(self, endpoint: str, worker: str) -> float:
        """Ожидаемая стоимость запроса на воркере по таблице замеров."""
        row = self._cost_table.get(endpoint)
        if not row:
            return self._default_cost
        value = row.get(worker)
        return float(value) if value is not None else self._default_cost

    def add(self, worker: str, endpoint: str) -> int:
        """Регистрирует начало обработки, возвращает идентификатор записи."""
        with self._lock:
            self._counter += 1
            token = self._counter
            self._active[worker][token] = ActiveRequest(
                endpoint=endpoint,
                started_at=time.perf_counter(),
                expected_ms=self.expected_cost(endpoint, worker),
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
        Оценка оставшейся работы на каждом воркере в миллисекундах.

        Из ожидаемой стоимости каждого активного запроса вычитается уже
        прошедшее время: запрос, который выполняется почти секунду из
        ожидаемых 900 мс, скоро освободит воркер и не должен считаться
        так же, как только что поступивший.
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

    def snapshot(self) -> tuple[dict[str, int], dict[str, float]]:
        """Согласованный снимок длин очередей и объёма работы в них."""
        return self.counts(), self.pending_work()
