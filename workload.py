"""
Общая модель имитируемой нагрузки для flask_app.py и fastapi_app.py.

Ключевая идея эксперимента: в отличие от исходной версии прототипа (оба
бэкенда делали идентичный sleep(0.1), поэтому у маршрутизации не было
физической причины давать выигрыш), здесь тип работы КАЖДОГО конкретного
запроса — I/O-ожидание или CPU-вычисление — определяется его собственным
io_intensity, и оба бэкенда реально выполняют РАЗНУЮ работу в зависимости
от типа. Классификатор AdaptiveRouter учится угадывать этот тип по тем же
признакам (load, io_intensity, cpu_usage) и направлять запрос на движок,
который эту работу физически обрабатывает быстрее/безопаснее:

  - I/O-bound  -> FastAPI: asyncio.sleep не блокирует event loop, можно
    держать тысячи параллельных ожиданий почти бесплатно.
  - CPU-bound  -> Flask: busy-loop в отдельном OS-потоке не останавливает
    обработку остальных запросов (GIL переключает потоки каждые ~5 мс).
    Тот же busy-loop, выполненный НАПРЯМУЮ внутри async-хендлера FastAPI
    (без вынесения в executor), полностью останавливает единственный
    event loop на всё время вычисления — это реалистичный сценарий
    ошибочного использования async-фреймворка под CPU-нагрузку.
"""
import time

IO_INTENSITY_THRESHOLD = 0.15

IO_BASE_S = 0.03
IO_SCALE_S = 0.09

CPU_BASE_S = 0.08
CPU_SCALE_S = 0.22


def is_io_bound(io_intensity: float) -> bool:
    return io_intensity >= IO_INTENSITY_THRESHOLD


def io_bound_duration(io_intensity: float) -> float:
    io_intensity = min(max(io_intensity, 0.0), 1.0)
    return IO_BASE_S + io_intensity * IO_SCALE_S


def cpu_bound_duration(cpu_usage: float) -> float:
    cpu_usage = min(max(cpu_usage, 0.0), 100.0)
    return CPU_BASE_S + (cpu_usage / 100.0) * CPU_SCALE_S


def cpu_bound_work(cpu_usage: float) -> int:
    """Синхронная CPU-нагрузка заданной длительности (busy-loop, не sleep)."""
    deadline = time.perf_counter() + cpu_bound_duration(cpu_usage)
    acc = 0
    while time.perf_counter() < deadline:
        for i in range(2000):
            acc += i * i
    return acc
