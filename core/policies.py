"""
Политики маршрутизации: от тривиальных базовых до обучаемой.

Все политики реализуют один интерфейс и получают одни и те же входные
данные — то, что реально доступно шлюзу в момент решения, до того как
запрос обработан:

  * эндпоинт и HTTP-метод,
  * размер тела запроса,
  * сколько запросов сейчас отправлено каждому воркеру и ещё не завершено
    (in-flight — прокси знает это без всякой кооперации с воркерами).

Намеренно недоступно: фактическая длительность запроса и его «тип»
(I/O-bound или CPU-bound). В предыдущей версии прототипа тип приходил в
теле запроса явным числом, из-за чего задача классификации вырождалась в
сравнение с порогом. Здесь политика обязана вывести полезность воркера
из косвенных признаков — либо не выводить вовсе, если она базовая.

Набор базовых политик подобран так, чтобы обучаемой политике было с чем
честно конкурировать: round-robin и least-connections — то, что реально
используется в промышленных балансировщиках, статическое правило —
сильный экспертный baseline, oracle — верхняя граница достижимого.
"""
from __future__ import annotations

import itertools
import random
from dataclasses import dataclass, field

WORKERS = ("sync", "async", "process")


@dataclass
class RequestFeatures:
    """Всё, что известно шлюзу о запросе в момент принятия решения."""

    endpoint: str
    method: str
    payload_bytes: int
    inflight: dict[str, int] = field(default_factory=dict)
    # Оценка оставшейся работы на каждом воркере в миллисекундах. Именно
    # этой величины нет у least-connections: он считает задачи, не
    # различая лёгкие и тяжёлые.
    pending_work: dict[str, float] = field(default_factory=dict)

    def inflight_vector(self) -> list[int]:
        return [self.inflight.get(w, 0) for w in WORKERS]

    def work_vector(self) -> list[float]:
        return [self.pending_work.get(w, 0.0) for w in WORKERS]


class Policy:
    """Базовый интерфейс политики маршрутизации."""

    name = "base"

    def choose(self, features: RequestFeatures) -> str:
        raise NotImplementedError

    def observe(self, features: RequestFeatures, worker: str, latency_ms: float) -> None:
        """Обратная связь по завершённому запросу. По умолчанию не нужна."""
        return None


class RandomPolicy(Policy):
    """Случайный выбор. Нужен и как baseline, и для сбора обучающих данных."""

    name = "random"

    def __init__(self, seed: int = 0):
        self._rng = random.Random(seed)

    def choose(self, features: RequestFeatures) -> str:
        return self._rng.choice(WORKERS)


class RoundRobinPolicy(Policy):
    """Классический round-robin: по очереди, не глядя на запрос."""

    name = "round_robin"

    def __init__(self):
        self._cycle = itertools.cycle(WORKERS)

    def choose(self, features: RequestFeatures) -> str:
        return next(self._cycle)


class LeastConnectionsPolicy(Policy):
    """
    Least-connections: запрос уходит наименее загруженному воркеру.

    Самый сильный из «слепых» промышленных балансировщиков: он учитывает
    загрузку, но по-прежнему ничего не знает о характере запроса, поэтому
    не отличает вычислительную задачу от ожидания ввода-вывода.
    """

    name = "least_conn"

    def choose(self, features: RequestFeatures) -> str:
        inflight = features.inflight
        return min(WORKERS, key=lambda w: inflight.get(w, 0))


class LeastExpectedWorkPolicy(Policy):
    """
    Выбор воркера с наименьшим ожидаемым объёмом работы в очереди.

    Отличается от least-connections тем, что взвешивает очередь: пять
    лёгких запросов и пять тяжёлых для него разные состояния. Стоимость
    берётся из таблицы замеров, никакого обучения здесь нет — это честный
    сильный baseline, который проверяет, нужна ли вообще модель, или
    достаточно учитывать вес очереди арифметически.
    """

    name = "least_work"

    def __init__(self, cost_table: dict[str, dict[str, float]],
                 default_cost_ms: float = 150.0):
        self.cost_table = cost_table
        self.default_cost = default_cost_ms

    def _cost(self, endpoint: str, worker: str) -> float:
        row = self.cost_table.get(endpoint)
        if not row:
            return self.default_cost
        value = row.get(worker)
        return float(value) if value is not None else self.default_cost

    def choose(self, features: RequestFeatures) -> str:
        return min(
            WORKERS,
            key=lambda w: features.pending_work.get(w, 0.0)
            + self._cost(features.endpoint, w),
        )


class StaticRulePolicy(Policy):
    """
    Экспертное правило: фиксированное отображение эндпоинта на воркер.

    Составляется человеком по результатам профилирования и не меняется во
    время работы. Это главный конкурент обучаемой политики: если она не
    выигрывает у грамотно составленной статики, ML в этой задаче не нужен,
    и об этом следует сказать прямо.
    """

    name = "static_rule"

    def __init__(self, mapping: dict[str, str], default: str = "sync"):
        self.mapping = mapping
        self.default = default

    def choose(self, features: RequestFeatures) -> str:
        return self.mapping.get(features.endpoint, self.default)


class OraclePolicy(Policy):
    """
    Оракул: знает заранее измеренную стоимость эндпоинта на каждом воркере
    и добавляет к ней штраф за текущую очередь.

    Физически нереализуем в проде (требует знания будущего), поэтому
    служит верхней границей: показывает, сколько вообще можно выжать из
    маршрутизации, и тем самым задаёт масштаб для остальных политик.
    """

    name = "oracle"

    def __init__(self, cost_table: dict[str, dict[str, float]],
                 queue_penalty: float = 1.0):
        self.cost_table = cost_table
        self.queue_penalty = queue_penalty

    def choose(self, features: RequestFeatures) -> str:
        costs = self.cost_table.get(features.endpoint)
        if not costs:
            return "sync"
        inflight = features.inflight

        def expected(worker: str) -> float:
            base = costs.get(worker, float("inf"))
            return base * (1 + self.queue_penalty * inflight.get(worker, 0))

        return min(WORKERS, key=expected)


class ModelPolicy(Policy):
    """
    Обучаемая политика: предсказывает латентность запроса на каждом
    воркере и выбирает наименьшую.

    В отличие от классификации «тип запроса», здесь модель решает
    регрессионную задачу на фактически измеренных длительностях, причём
    на вход ей подаётся и текущее состояние очередей. Поэтому её решение
    может меняться для одного и того же эндпоинта в зависимости от
    загрузки — чего статическое правило по построению не умеет.

    Режим marginal учитывает, что решение само меняет состояние системы:
    воркер оценивается по очереди, которая образуется ПОСЛЕ постановки
    в неё текущего запроса. Без этой поправки политика оценивает всех
    кандидатов по текущему состоянию, стабильно выбирает быстрейший
    воркер и в результате перегружает его, оставляя более медленные
    простаивать — а простаивающий воркер это потерянная пропускная
    способность системы, даже если он медленный.
    """

    name = "model"

    def __init__(self, predictor, marginal: bool = True):
        self.predictor = predictor
        self.marginal = marginal

    def choose(self, features: RequestFeatures) -> str:
        if not self.marginal:
            predictions = self.predictor.predict_all_workers(features)
            return min(predictions, key=predictions.get)

        best_worker = None
        best_latency = float("inf")
        for worker in WORKERS:
            probe = RequestFeatures(
                endpoint=features.endpoint,
                method=features.method,
                payload_bytes=features.payload_bytes,
                inflight={
                    w: features.inflight.get(w, 0) + (1 if w == worker else 0)
                    for w in WORKERS
                },
            )
            latency = self.predictor.predict_all_workers(probe)[worker]
            if latency < best_latency:
                best_latency = latency
                best_worker = worker
        return best_worker


class HybridPolicy(Policy):
    """
    Гибрид балансировки и предсказания.

    Эксперимент показал, что least-connections выигрывает по пропускной
    способности за счёт простого выравнивания загрузки, а обучаемая
    политика лучше различает характер запросов, но склонна перегружать
    быстрейший воркер. Гибрид разделяет эти роли: сначала балансировка
    отсекает воркеров, чья очередь длиннее минимальной больше чем на
    slack, а затем модель выбирает лучший из оставшихся.

    При slack = 0 политика вырождается в least-connections, при
    бесконечном slack — в чистое предсказание. Промежуточные значения
    позволяют проверить, где проходит граница полезности модели.
    """

    name = "hybrid"

    def __init__(self, predictor, slack: int = 2):
        self.predictor = predictor
        self.slack = slack

    def choose(self, features: RequestFeatures) -> str:
        inflight = features.inflight
        min_load = min(inflight.get(w, 0) for w in WORKERS)
        candidates = [w for w in WORKERS
                      if inflight.get(w, 0) <= min_load + self.slack]
        if len(candidates) == 1:
            return candidates[0]

        predictions = self.predictor.predict_all_workers(features)
        return min(candidates, key=lambda w: predictions[w])


def build_static_rule_from_costs(cost_table: dict[str, dict[str, float]]
                                 ) -> StaticRulePolicy:
    """Строит экспертное правило, выбирая лучший воркер по таблице замеров."""
    mapping = {
        endpoint: min(costs, key=costs.get)
        for endpoint, costs in cost_table.items()
    }
    return StaticRulePolicy(mapping)
