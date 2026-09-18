"""
Политики маршрутизации.

Устройство набора политик подчинено одной цели — измерить вклад оценки
стоимости запроса, не смешивая его ни с чем другим. Поэтому все
содержательные политики используют одно и то же правило выбора:

    занятость(движок) = уже накопленная работа в его очереди
                        + оценка занятости, которую добавит этот запрос

и отличаются исключительно тем, откуда берётся вторая величина. Если
подставить туда константу, правило вырождается в least-connections; если
подставить точную измеренную величину — получается верхняя граница
достижимого. Всё остальное лежит между ними, и расстояние между
крайностями показывает, сколько вообще можно выиграть за счёт понимания
того, насколько тяжёл конкретный запрос.

Отдельно стоят две политики, которые маршрутизации не выполняют вовсе:
весь трафик уходит в один движок. Они нужны как точка отсчёта, потому
что вопрос статьи — не «какая политика лучше другой политики», а «даёт
ли разделение трафика между двумя движками выигрыш по сравнению с
развёртыванием на одном».
"""
from __future__ import annotations

import itertools
import os
import random
from dataclasses import dataclass, field

# Основной эксперимент ведётся на паре «синхронный — асинхронный движок».
# Пул процессов подключается переменной окружения как расширение.
WORKERS: tuple[str, ...] = tuple(
    os.environ.get("WORKERS", "sync,async").split(",")
)


@dataclass
class RequestFeatures:
    """Всё, что известно шлюзу о запросе в момент принятия решения."""

    endpoint: str
    method: str
    payload_bytes: int
    # Параметр запроса (limit, size, days). Прокси видит его в строке
    # запроса или в теле, не заглядывая в бизнес-логику сервиса. Именно
    # он, а не маршрут, определяет объём работы.
    param: float | None = None
    inflight: dict[str, int] = field(default_factory=dict)
    # Оценка оставшейся занятости каждого движка в миллисекундах.
    pending_work: dict[str, float] = field(default_factory=dict)
    # Фактическая скорость ответа движка в последние запросы.
    recent_latency: dict[str, float] = field(default_factory=dict)


class Policy:
    """Базовый интерфейс политики маршрутизации."""

    name = "base"

    def choose(self, features: RequestFeatures) -> str:
        raise NotImplementedError

    def observe(self, features: RequestFeatures, worker: str,
                latency_ms: float) -> None:
        return None


class FixedWorkerPolicy(Policy):
    """
    Весь трафик в один движок — развёртывание без маршрутизации.

    Это не конкурирующая политика, а базовая точка отсчёта: так выглядит
    обычный сервис, целиком написанный либо синхронно, либо асинхронно.
    """

    def __init__(self, worker: str):
        self.worker = worker
        self.name = f"all_{worker}"

    def choose(self, features: RequestFeatures) -> str:
        return self.worker


class RandomPolicy(Policy):
    """Случайный выбор. Нижняя граница осмысленности и способ покрытия."""

    name = "random"

    def __init__(self, seed: int = 0):
        self._rng = random.Random(seed)

    def choose(self, features: RequestFeatures) -> str:
        return self._rng.choice(WORKERS)


class RoundRobinPolicy(Policy):
    """Классический round-robin: по очереди, не глядя ни на что."""

    name = "round_robin"

    def __init__(self):
        self._cycle = itertools.cycle(WORKERS)

    def choose(self, features: RequestFeatures) -> str:
        return next(self._cycle)


class LeastConnectionsPolicy(Policy):
    """
    Least-connections: запрос уходит туда, где сейчас меньше запросов.

    Самый распространённый промышленный балансировщик. Учитывает
    загрузку, но считает запросы штуками: очередь из пяти обращений по
    10 мс и очередь из пяти обращений по 900 мс для него неразличимы.
    """

    name = "least_conn"

    def choose(self, features: RequestFeatures) -> str:
        return min(WORKERS, key=lambda w: features.inflight.get(w, 0))


class LeastOccupancyPolicy(Policy):
    """
    Выбор движка по наименьшей ожидаемой занятости после добавления
    запроса.

    Величина, которая складывается по очереди, — не собственное время
    выполнения запроса, а занятость движка: собственное время плюс
    задержка, которую запрос причинит остальным. Разделение существенно:
    вычисление на 600 мс в потоковом сервере почти не мешает соседям
    (операция освобождает GIL), а в event loop останавливает их все, и
    складывать поэтому нужно разные числа.

    Источник оценки задаётся извне, и именно он различает политики.
    """

    def __init__(self, estimator, name: str = "occupancy"):
        self.estimator = estimator
        self.name = name

    def choose(self, features: RequestFeatures) -> str:
        return min(
            WORKERS,
            key=lambda w: features.pending_work.get(w, 0.0)
            + self.estimator.cost(features.endpoint, features.param, w),
        )


class StaticRulePolicy(Policy):
    """
    Экспертное правило: фиксированное отображение маршрута на движок,
    составленное по результатам профилирования.

    Это прямая формализация исходной гипотезы работы — «I/O-запросы в
    асинхронный движок, вычислительные в синхронный». Её главное
    ограничение теперь принципиально: правило привязано к маршруту, а
    внутри маршрута встречаются и лёгкие, и тяжёлые запросы, и всех их
    оно вынуждено отправить в одну сторону.
    """

    name = "static_rule"

    def __init__(self, mapping: dict[str, str], default: str = "sync"):
        self.mapping = mapping
        self.default = default

    def choose(self, features: RequestFeatures) -> str:
        return self.mapping.get(features.endpoint, self.default)


class AdaptiveOccupancyPolicy(LeastOccupancyPolicy):
    """
    То же правило, но оценки поправляются по ходу работы.

    Любая таблица верна ровно до тех пор, пока система не изменилась.
    Здесь на каждую пару «маршрут — движок» поддерживается
    мультипликативная поправка: отношение фактической длительности к
    ожидаемой, сглаженное скользящим средним. Машинного обучения здесь
    нет, это скользящее среднее, и если его окажется достаточно — значит
    достаточно, и так и следует написать.
    """

    def __init__(self, estimator, alpha: float = 0.25,
                 name: str = "adaptive_occupancy"):
        super().__init__(estimator, name=name)
        self.alpha = alpha
        self.correction: dict[tuple[str, str], float] = {}

    def choose(self, features: RequestFeatures) -> str:
        return min(
            WORKERS,
            key=lambda w: features.pending_work.get(w, 0.0)
            + self.estimator.cost(features.endpoint, features.param, w)
            * self.correction.get((features.endpoint, w), 1.0),
        )

    def observe(self, features: RequestFeatures, worker: str,
                latency_ms: float) -> None:
        expected = self.estimator.cost(features.endpoint, features.param, worker)
        queue_delay = features.pending_work.get(worker, 0.0)
        own = max(latency_ms - queue_delay, 1.0)
        if expected <= 0:
            return
        ratio = max(min(own / expected, 10.0), 0.1)
        key = (features.endpoint, worker)
        previous = self.correction.get(key, 1.0)
        self.correction[key] = (1 - self.alpha) * previous + self.alpha * ratio


class ExploringPolicy(Policy):
    """
    Обёртка, подмешивающая случайный выбор к решениям базовой политики.

    Нужна для сбора обучающих данных. Собирать их разумной политикой
    нельзя: в журнал попадут только те пары «запрос — движок», которые
    она и так считает удачными, и модель никогда не увидит, чем плохи
    остальные варианты. Собирать полностью случайно тоже нельзя:
    состояния очередей окажутся такими, каких в работе не бывает, и
    модель будет обучена на несуществующем режиме. Компромисс — основную
    часть решений принимает рабочая политика, заданная доля решений
    случайна.
    """

    name = "exploring"

    def __init__(self, base: Policy, epsilon: float = 0.35, seed: int = 0):
        self.base = base
        self.epsilon = epsilon
        self._rng = random.Random(seed)

    def choose(self, features: RequestFeatures) -> str:
        if self._rng.random() < self.epsilon:
            return self._rng.choice(WORKERS)
        return self.base.choose(features)

    def observe(self, features: RequestFeatures, worker: str,
                latency_ms: float) -> None:
        self.base.observe(features, worker, latency_ms)


def build_static_rule(estimator) -> StaticRulePolicy:
    """
    Строит экспертное правило: для каждого маршрута выбирается движок с
    наименьшей средней занятостью.
    """
    mapping = {}
    for endpoint in getattr(estimator, "table", {}):
        row = estimator.table[endpoint]
        if row:
            mapping[endpoint] = min(row, key=row.get)
    return StaticRulePolicy(mapping, default=WORKERS[0])
