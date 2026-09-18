"""
Оценка стоимости запроса — центральный элемент этой работы.

Маршрутизация сама по себе устроена одинаково во всех рассматриваемых
политиках: запрос уходит туда, где ожидаемое время его завершения
наименьшее. Различаются политики ровно одним — тем, откуда берётся
оценка стоимости конкретного запроса. Такое построение сделано
намеренно: оно позволяет измерить вклад именно оценки стоимости, не
смешивая его с различиями в правиле выбора.

Рассматриваются четыре источника оценки, от самого грубого к точному:

  * `EndpointMeanCost` — одно число на эндпоинт, среднее по трафику.
    Это то, что даёт обычное профилирование сервиса, и то, на чём
    работают промышленные балансировщики с весами.
  * `LinearParamCost` — экспертная поправка «стоимость пропорциональна
    параметру запроса». Правило, которое разработчик может написать
    руками, зная смысл параметра. Верно для выборок из базы и неверно
    для операций, сложность которых растёт быстрее.
  * `LearnedCost` — модель, обученная на журнале обращений. Показатель
    зависимости стоимости от параметра ей не сообщается, она выводит
    его из данных отдельно для каждого маршрута и каждого движка.
  * `MeasuredCost` — таблица, снятая прямым профилированием по всем
    значениям параметра. В работающей системе недоступна (требует
    останавливающего замера по всей сетке), поэтому используется только
    как верхняя граница достижимого.

Все оценки возвращают величину в миллисекундах и имеют общий интерфейс
`cost(endpoint, param, worker)`.
"""
from __future__ import annotations

import bisect
import json
from pathlib import Path

from core.workload import ENDPOINTS

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
COST_GRID_PATH = DATA_DIR / "cost_grid.json"

DEFAULT_COST_MS = 120.0


class CostEstimator:
    """Общий интерфейс оценки стоимости запроса на конкретном движке."""

    name = "base"

    def cost(self, endpoint: str, param: float | None, worker: str) -> float:
        raise NotImplementedError


def load_grid() -> dict:
    """Читает таблицу профилирования по сетке значений параметра."""
    if COST_GRID_PATH.exists():
        return json.loads(COST_GRID_PATH.read_text(encoding="utf-8"))
    return {}


class MeasuredCost(CostEstimator):
    """
    Точная стоимость из таблицы профилирования, с линейной интерполяцией
    между узлами сетки.

    Служит верхней границей: показывает, чего можно было бы достичь,
    если бы стоимость каждого запроса была известна заранее.
    """

    name = "measured"

    def __init__(self, grid: dict | None = None):
        self.grid = grid if grid is not None else load_grid()

    def cost(self, endpoint: str, param: float | None, worker: str) -> float:
        row = self.grid.get(endpoint)
        if not row:
            return DEFAULT_COST_MS
        points = row.get(worker)
        if not points:
            return DEFAULT_COST_MS
        params = [float(p) for p, _ in points]
        values = [float(v) for _, v in points]
        if len(params) == 1 or param is None:
            return values[0]
        x = float(param)
        if x <= params[0]:
            return values[0]
        if x >= params[-1]:
            return values[-1]
        i = bisect.bisect_left(params, x)
        x0, x1 = params[i - 1], params[i]
        y0, y1 = values[i - 1], values[i]
        return y0 + (y1 - y0) * (x - x0) / (x1 - x0)


class EndpointMeanCost(CostEstimator):
    """
    Средняя стоимость эндпоинта, взвешенная по распределению параметра.

    Именно такую оценку даёт профилирование сервиса по маршрутам: она
    верна «в среднем по больнице» и тем хуже описывает отдельный запрос,
    чем сильнее запросы внутри маршрута различаются между собой.
    """

    name = "endpoint_mean"

    def __init__(self, grid: dict | None = None):
        measured = MeasuredCost(grid)
        self.table: dict[str, dict[str, float]] = {}
        for endpoint, spec in ENDPOINTS.items():
            values = spec.param_values if spec.param_name else (None,)
            weights = spec.param_weights if spec.param_name else (1.0,)
            row = {}
            for worker in (measured.grid.get(endpoint) or {}):
                total = sum(
                    w * measured.cost(endpoint, p, worker)
                    for p, w in zip(values, weights)
                )
                row[worker] = total / sum(weights)
            self.table[endpoint] = row

    def cost(self, endpoint: str, param: float | None, worker: str) -> float:
        return self.table.get(endpoint, {}).get(worker, DEFAULT_COST_MS)


class LinearParamCost(CostEstimator):
    """
    Экспертная поправка на параметр запроса в предположении, что
    стоимость растёт пропорционально ему.

    Это сильный и совершенно реалистичный конкурент обучаемой модели:
    разработчик, знающий смысл параметра `limit`, напишет такое правило
    за пять минут. Проверяется ровно то, добавляет ли обучение что-то
    сверх этого очевидного соображения.
    """

    name = "linear_param"

    def __init__(self, grid: dict | None = None):
        self.measured = MeasuredCost(grid)

    def cost(self, endpoint: str, param: float | None, worker: str) -> float:
        spec = ENDPOINTS.get(endpoint)
        base = self.measured.cost(endpoint, None, worker)
        if spec is None or not spec.param_name:
            return base
        # Опорная точка — стоимость при базовом значении параметра.
        base = self.measured.cost(endpoint, spec.param_base, worker)
        if param is None:
            return base
        ratio = max(float(param), 1.0) / float(spec.param_base)
        return max(base * ratio, 1.0)


class LearnedCost(CostEstimator):
    """Оценка стоимости обученной моделью."""

    name = "learned"

    def __init__(self, predictor):
        self.predictor = predictor

    def cost(self, endpoint: str, param: float | None, worker: str) -> float:
        return self.predictor.predict(endpoint, param, worker)


class ConstantCost(CostEstimator):
    """Никакой оценки: все запросы считаются одинаковыми."""

    name = "constant"

    def __init__(self, value: float = DEFAULT_COST_MS):
        self.value = value

    def cost(self, endpoint: str, param: float | None, worker: str) -> float:
        return self.value


def build_estimator(name: str) -> CostEstimator:
    grid = load_grid()
    if name == "measured":
        return MeasuredCost(grid)
    if name == "endpoint_mean":
        return EndpointMeanCost(grid)
    if name == "linear_param":
        return LinearParamCost(grid)
    if name == "constant":
        return ConstantCost()
    if name == "learned":
        from core.predictor import CostPredictor

        return LearnedCost(CostPredictor.load())
    raise ValueError(f"Неизвестная оценка стоимости: {name}")
