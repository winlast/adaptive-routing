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

    Складываются и сравниваются при этом разные величины, и это
    существенно. По очереди суммируется блокировка — время, на которое
    движок недоступен остальным. Ожидание внешнего сервиса на 500 мс
    движок почти не занимает и в очередь весом не входит, тогда как
    вычисление на 500 мс занимает его целиком. К накопленной блокировке
    прибавляется полная цена нового запроса: его собственное время плюс
    задержка, которую он причинит остальным.

    Источник обеих оценок задаётся извне, и именно он различает
    политики.
    """

    # Чем оценивается вклад нового запроса в занятость движка. Величина
    # выбрана по результатам замеров и обе возможности сохранены, потому
    # что выбор между ними — содержательное решение, а не деталь.
    #
    #   "block" — только блокировка: время, на которое движок становится
    #             недоступен остальным. Собственное время выполнения в
    #             сравнении движков не участвует, и это оправдано тем,
    #             что оно, по измерениям, почти не зависит от движка и
    #             потому при сравнении сокращается.
    #   "full"  — блокировка плюс собственное время выполнения.
    SCORE = os.environ.get("SCORE", "block")

    def __init__(self, estimator, name: str = "occupancy"):
        self.estimator = estimator
        self.name = name

    def _added(self, features: RequestFeatures, worker: str) -> float:
        if self.SCORE == "full":
            return self.estimator.cost(features.endpoint, features.param,
                                       worker)
        return self.estimator.blocking(features.endpoint, features.param,
                                       worker)

    def choose(self, features: RequestFeatures) -> str:
        return min(
            WORKERS,
            key=lambda w: features.pending_work.get(w, 0.0)
            + self._added(features, w),
        )


class BlockingBudgetPolicy(Policy):
    """
    Маршрутизация с ограничением на блокировку асинхронного движка.

    Постановка отвечает тому, чем задача является физически.
    Асинхронный движок выгоден ровно до тех пор, пока в него не попадает
    вычислительная работа: одна такая задача останавливает обработку
    всех остальных запросов до своего завершения. Поэтому решение,
    которое нужно принять по каждому запросу, — не «какой движок
    быстрее», а «безопасно ли пускать этот запрос в event loop».

    Политика оценивает, на сколько запрос заблокирует движок, и
    допускает его в асинхронный движок только если эта величина не
    превышает заданного порога. Синхронный движок принимает всё:
    вычисление в потоке никого не останавливает целиком, планировщик
    продолжает переключать потоки. Среди допустимых движков выбирается
    тот, у которого меньше накопленная занятость.

    Порог — не настроечная константа, а параметр компромисса. При нуле
    весь трафик уходит в синхронный движок, при бесконечности политика
    вырождается в обычную балансировку по весу очереди. Промежуточные
    значения задают кривую «пропускная способность против хвоста
    задержек», и качество оценки блокировки определяет, насколько
    выгодной эта кривая окажется: грубая оценка вынуждена относить к
    опасным целые маршруты, точная разделяет запросы внутри маршрута.
    """

    def __init__(self, estimator, budget_ms: float,
                 safe_worker: str = "sync", guarded_worker: str = "async",
                 name: str = "budget"):
        self.estimator = estimator
        self.budget_ms = budget_ms
        self.safe_worker = safe_worker
        self.guarded_worker = guarded_worker
        self.name = name

    def choose(self, features: RequestFeatures) -> str:
        candidates = []
        for worker in WORKERS:
            if worker == self.guarded_worker:
                predicted = self.estimator.blocking(
                    features.endpoint, features.param, worker)
                if predicted > self.budget_ms:
                    continue
            candidates.append(worker)
        if not candidates:
            return self.safe_worker if self.safe_worker in WORKERS else WORKERS[0]
        return min(
            candidates,
            key=lambda w: features.pending_work.get(w, 0.0)
            + self.estimator.blocking(features.endpoint, features.param, w),
        )


class AdaptiveBudgetPolicy(BlockingBudgetPolicy):
    """
    Та же политика, но с поправкой оценок по ходу работы.

    Таблица, снятая профилированием, верна ровно до тех пор, пока
    система не изменилась. На той же машине появился сосед, у движка
    отобрали процессорное время — код не менялся, а ёмкость упала, и
    политика продолжает считать дешёвым то, что подорожало.

    Здесь на каждую пару «маршрут — движок» поддерживается
    мультипликативная поправка: отношение наблюдённой длительности к
    ожидаемой, сглаженное скользящим средним. Поправка применяется и при
    взвешивании очереди, и при допуске в асинхронный движок. Машинного
    обучения в самой поправке нет — это скользящее среднее, и если его
    достаточно, так и следует написать.
    """

    def __init__(self, estimator, budget_ms: float, alpha: float = 0.25,
                 name: str = "adaptive_budget", **kwargs):
        super().__init__(estimator, budget_ms, name=name, **kwargs)
        self.alpha = alpha
        self.correction: dict[tuple[str, str], float] = {}

    def _blocking(self, features: RequestFeatures, worker: str) -> float:
        base = self.estimator.blocking(features.endpoint, features.param,
                                       worker)
        return base * self.correction.get((features.endpoint, worker), 1.0)

    def choose(self, features: RequestFeatures) -> str:
        candidates = [
            w for w in WORKERS
            if w != self.guarded_worker
            or self._blocking(features, w) <= self.budget_ms
        ]
        if not candidates:
            return self.safe_worker if self.safe_worker in WORKERS else WORKERS[0]
        return min(candidates,
                   key=lambda w: features.pending_work.get(w, 0.0)
                   + self._blocking(features, w))

    def observe(self, features: RequestFeatures, worker: str,
                latency_ms: float) -> None:
        expected = self.estimator.cost(features.endpoint, features.param,
                                       worker)
        if expected <= 0:
            return
        queue_delay = features.pending_work.get(worker, 0.0)
        own = max(latency_ms - queue_delay, 1.0)
        ratio = max(min(own / expected, 10.0), 0.1)
        key = (features.endpoint, worker)
        previous = self.correction.get(key, 1.0)
        self.correction[key] = (1 - self.alpha) * previous + self.alpha * ratio


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
            + self._added(features, w)
            * self.correction.get((features.endpoint, w), 1.0),
        )

    def observe(self, features: RequestFeatures, worker: str,
                latency_ms: float) -> None:
        expected = self._added(features, worker)
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


def build_static_rule() -> StaticRulePolicy:
    """
    Строит экспертное правило — прямую формализацию исходной гипотезы
    работы: маршруты, занятые преимущественно ожиданием ввода-вывода,
    обслуживает асинхронный движок, маршруты с вычислениями —
    синхронный. Маршруты смешанного типа содержат заметную
    вычислительную часть и относятся к синхронным.

    Правило составляется человеком по типу маршрута, не использует
    никаких замеров и не меняется во время работы.
    """
    from core.workload import ENDPOINTS

    async_worker = "async" if "async" in WORKERS else WORKERS[-1]
    sync_worker = "sync" if "sync" in WORKERS else WORKERS[0]
    mapping = {
        endpoint: (async_worker if spec.kind == "io" else sync_worker)
        for endpoint, spec in ENDPOINTS.items()
    }
    return StaticRulePolicy(mapping, default=sync_worker)
