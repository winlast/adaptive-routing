"""
Оценка стоимости запроса — центральный элемент этой работы.

Маршрутизация сама по себе устроена одинаково во всех рассматриваемых
политиках: запрос уходит туда, где ожидаемое время его завершения
наименьшее. Различаются политики ровно одним — тем, откуда берётся
оценка стоимости конкретного запроса. Такое построение сделано
намеренно: оно позволяет измерить вклад именно оценки стоимости, не
смешивая его с различиями в правиле выбора.

Рассматриваются четыре источника оценки, от самого грубого к точному:

  * `ConstantCost` — оценки нет, все запросы считаются одинаковыми.
    Подстановка этой оценки превращает правило выбора в обычный
    least-connections, что и служит нижней точкой отсчёта.
  * `EndpointMeanCost` — одно число на маршрут, среднее по трафику.
    Это то, что даёт обычное профилирование сервиса по маршрутам.
  * `LinearParamCost` — экспертная поправка «занятость пропорциональна
    параметру запроса»: показатель степени принят равным единице, а
    множитель подобран по замерам.
  * `PowerLawCost` и `NeuralCost` (в `core/predictor.py`) — обучаемые
    оценки. Показатель зависимости им не сообщается, они выводят его из
    тех же замеров отдельно для каждого маршрута и каждого движка.
  * `MeasuredCost` — таблица, снятая профилированием по всей сетке
    значений параметра. В работающей системе недоступна, используется
    только как верхняя граница достижимого.

Каждая оценка отвечает на два разных вопроса о запросе, и смешивать их
нельзя:

  * `cost` — сколько запрос будет выполняться сам плюс сколько он
    задержит остальных. Эта величина сравнивается между движками при
    выборе;
  * `blocking` — на сколько запрос делает движок недоступным для
    остальных. Этой величиной взвешивается очередь.

Для ожидания ввода-вывода они расходятся радикально: обращение к
внешнему сервису на 500 мс выполняется полсекунды, но движок при этом
почти не занимает — ни в event loop, ни в потоке. Оценка, которая
складывала бы по очереди полное время выполнения, приписала бы такому
запросу вес, которого у него нет.
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
        """Полная цена запроса: собственное время плюс задержка другим."""
        raise NotImplementedError

    def blocking(self, endpoint: str, param: float | None,
                 worker: str) -> float:
        """Время, на которое запрос занимает движок для остальных."""
        return self.cost(endpoint, param, worker)


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
        return self._interpolate(endpoint, param, worker, column=1)

    def blocking(self, endpoint: str, param: float | None,
                 worker: str) -> float:
        return self._interpolate(endpoint, param, worker, column=2)

    def _interpolate(self, endpoint: str, param: float | None, worker: str,
                     column: int) -> float:
        row = self.grid.get(endpoint)
        if not row:
            return DEFAULT_COST_MS
        points = row.get(worker)
        if not points:
            return DEFAULT_COST_MS
        params = [float(p[0]) for p in points]
        values = [float(p[column]) for p in points]
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
    Одно число на маршрут — средняя занятость по наблюдавшемуся трафику.

    Именно такую оценку даёт обычное профилирование сервиса по маршрутам
    и именно на такой оценке работают промышленные балансировщики с
    весами. Она верна в среднем и тем хуже описывает отдельный запрос,
    чем сильнее запросы внутри маршрута различаются между собой.
    """

    name = "endpoint_mean"

    def __init__(self, samples: list[dict]):
        self.table = self._average(samples, "occupancy_ms")
        self.block_table = self._average(samples, "block_ms")

    @staticmethod
    def _average(samples: list[dict], field: str) -> dict[str, dict[str, float]]:
        totals: dict[tuple[str, str], list[float]] = {}
        for row in samples:
            totals.setdefault((row["endpoint"], row["worker"]), []).append(
                row[field])
        table: dict[str, dict[str, float]] = {}
        for (endpoint, worker), values in totals.items():
            table.setdefault(endpoint, {})[worker] = sum(values) / len(values)
        return table

    def cost(self, endpoint: str, param: float | None, worker: str) -> float:
        return self.table.get(endpoint, {}).get(worker, DEFAULT_COST_MS)

    def blocking(self, endpoint: str, param: float | None,
                 worker: str) -> float:
        return self.block_table.get(endpoint, {}).get(worker, DEFAULT_COST_MS)


class LinearParamCost(CostEstimator):
    """
    Экспертная поправка: занятость считается пропорциональной параметру.

    Это сильный и совершенно реалистичный конкурент обучаемой оценки.
    Разработчик, понимающий смысл параметра `limit`, напишет такое
    правило за пять минут, не собирая никакой статистики сверх обычного
    профилирования. Коэффициент подбирается по тем же замерам, что и у
    остальных оценок; жёстко задан только показатель степени, принятый
    равным единице.

    Проверяется ровно одно: добавляет ли обучение что-нибудь сверх этого
    очевидного соображения. Для выборок из базы правило верно, для
    операций, сложность которых растёт быстрее длины запроса, — нет.
    """

    name = "linear_param"

    def __init__(self, samples: list[dict]):
        self.table = self._fit(samples, "occupancy_ms")
        self.block_table = self._fit(samples, "block_ms")

    @staticmethod
    def _fit(samples: list[dict], field: str) -> dict[str, dict[str, float]]:
        import math

        groups: dict[tuple[str, str], list[float]] = {}
        for row in samples:
            spec = ENDPOINTS.get(row["endpoint"])
            value = max(row[field], 1.0)
            if spec is None or not spec.param_name or row.get("param") is None:
                ratio = 1.0
            else:
                ratio = max(float(row["param"]), 1.0) / float(spec.param_base)
            # Множитель подбирается при жёстко заданном показателе
            # степени. Усреднение ведётся по логарифмам: распределение
            # имеет тяжёлый правый хвост, и обычное среднее определялось
            # бы несколькими самыми тяжёлыми замерами.
            groups.setdefault((row["endpoint"], row["worker"]), []).append(
                math.log(value / ratio))
        table: dict[str, dict[str, float]] = {}
        for (endpoint, worker), values in groups.items():
            table.setdefault(endpoint, {})[worker] = math.exp(
                sum(values) / len(values))
        return table

    def _scaled(self, table, endpoint: str, param: float | None,
                worker: str) -> float:
        base = table.get(endpoint, {}).get(worker, DEFAULT_COST_MS)
        spec = ENDPOINTS.get(endpoint)
        if spec is None or not spec.param_name or param is None:
            return base
        ratio = max(float(param), 1.0) / float(spec.param_base)
        return max(base * ratio, 1.0)

    def cost(self, endpoint: str, param: float | None, worker: str) -> float:
        return self._scaled(self.table, endpoint, param, worker)

    def blocking(self, endpoint: str, param: float | None,
                 worker: str) -> float:
        return self._scaled(self.block_table, endpoint, param, worker)


class ConstantCost(CostEstimator):
    """Никакой оценки: все запросы считаются одинаковыми."""

    name = "constant"

    def __init__(self, value: float = DEFAULT_COST_MS):
        self.value = value

    def cost(self, endpoint: str, param: float | None, worker: str) -> float:
        return self.value


def build_estimator(name: str) -> CostEstimator:
    """
    Собирает оценку стоимости по имени.

    Все оценки, кроме `measured`, настраиваются на одном и том же наборе
    замеров и отличаются исключительно видом зависимости. `measured`
    читает полную таблицу профилирования по сетке значений параметра и
    в работающей системе недоступна — она задаёт верхнюю границу.
    """
    if name == "measured":
        return MeasuredCost(load_grid())
    if name == "constant":
        return ConstantCost()

    from core.predictor import load_samples

    samples = load_samples()
    if name == "endpoint_mean":
        return EndpointMeanCost(samples)
    if name == "linear_param":
        return LinearParamCost(samples)
    if name == "power_law":
        from core.predictor import PowerLawCost

        return PowerLawCost.load()
    if name == "neural":
        from core.predictor import NeuralCost

        return NeuralCost.load()
    raise ValueError(f"Неизвестная оценка стоимости: {name}")
