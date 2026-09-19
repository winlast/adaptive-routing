"""
Реальные операции нагрузки.

В отличие от предыдущей версии прототипа, где нагрузка эмулировалась
вызовами sleep() и пустым busy-loop, здесь каждый запрос выполняет
настоящую работу: криптографическое хеширование, сжатие изображения,
сортировку данных, обращение к SQLite и HTTP-вызов к внешнему сервису.

Это важно по двум причинам:
  1. Честность измерений. sleep() не нагружает ни CPU, ни память, ни
     аллокатор, ни GIL — а реальная работа нагружает, и поведение
     воркеров разных типов на ней отличается не так, как на sleep().
  2. Стоимость передачи данных. Для process-pool воркера принципиальна
     сериализация аргументов и результата (pickle): операция на 2 КБ и
     операция на 2 МБ ведут себя принципиально по-разному, и именно это
     делает выбор воркера нетривиальным.

I/O-операции определены дважды — синхронная и асинхронная реализация с
одинаковой семантикой (как psycopg2 против asyncpg в реальном коде),
чтобы каждый воркер использовал идиоматичный для себя способ и сравнение
оставалось честным.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import random
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
import requests
from PIL import Image

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
DB_PATH = DATA_DIR / "workload.db"

# Внешний сервис, имитирующий обращение к другому микросервису или к БД
# по сети. Поднимается отдельным процессом (workers/stub_service.py).
STUB_URL = os.environ.get("STUB_URL", "http://127.0.0.1:8100/wait")


# ---------------------------------------------------------------------------
# Профили запросов
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class EndpointSpec:
    """
    Описание эндпоинта.

    Ключевое отличие от первой версии прототипа: стоимость запроса
    определяется не маршрутом, а параметром запроса. В реальных API это
    правило, а не исключение — `?limit=10` и `?limit=5000` идут по
    одному и тому же пути, но различаются по стоимости на два порядка, а
    время обработки изображения растёт как квадрат его стороны.

    Базовые величины ниже задают стоимость при `param = param_base`.
    Фактический объём работы получается умножением на коэффициент,
    который зависит от параметра и от показателя степени `exponent`:
    для выборок из базы зависимость близка к линейной, для операций над
    изображениями и для агрегации за период — квадратичная.

    Следствие, ради которого всё это и введено: таблица вида
    «эндпоинт → стоимость» перестаёт быть верной, потому что усредняет
    запросы, различающиеся в десятки раз.
    """

    name: str
    kind: str  # "io" | "cpu" | "hybrid"
    # Параметр запроса, который клиент передаёт вместе с ним и который
    # виден шлюзу (строка запроса или тело). Именно он, а не путь,
    # определяет объём работы.
    param_name: str = ""
    param_base: int = 1
    # Диапазон, в котором параметр реально встречается в трафике.
    # Значение может быть любым внутри него: клиент не обязан выбирать
    # из заранее известного списка, и модель, оценивающая стоимость,
    # обязана обобщать на не встречавшиеся значения.
    param_range: tuple[int, int] = (1, 1)
    # Сетка, по которой снимается эталонное профилирование. Нужна только
    # проверке точности оценок стоимости, политике она недоступна.
    param_values: tuple[int, ...] = (1,)
    param_weights: tuple[float, ...] = (1.0,)
    # Показатель степени зависимости стоимости от параметра:
    # 1.0 — линейная, 2.0 — квадратичная, 0.0 — параметр не влияет.
    exponent: float = 1.0
    # Параметры операции при param = param_base.
    io_wait_ms: int = 0  # сколько ждёт внешний сервис
    cpu_iterations: int = 0  # итерации PBKDF2
    image_side: int = 0  # сторона изображения для сжатия
    sort_size: int = 0  # размер массива для сортировки
    db_rows: int = 0  # сколько строк читать из SQLite
    payload_bytes: int = 0  # размер тела запроса, который шлёт клиент

    def factor(self, param: float | None) -> float:
        """Во сколько раз параметр запроса меняет объём работы."""
        if self.exponent == 0.0 or not self.param_name:
            return 1.0
        value = self.param_base if param is None else float(param)
        ratio = max(value, 1.0) / float(self.param_base)
        return max(min(ratio ** self.exponent, 40.0), 0.01)


# Шесть эндпоинтов. Профили подобраны так, чтобы внутри одного и того же
# маршрута встречались и почти бесплатные, и очень дорогие запросы: у
# `/api/search` при limit = 10 вычислительная часть занимает единицы
# миллисекунд, а при limit = 600 — сотни. Это и делает выбор движка
# зависящим от конкретного запроса, а не от пути.
#
# Веса значений параметра убывают: мелкие выборки запрашивают часто,
# крупные редко. Так устроен реальный трафик, и именно поэтому средняя
# стоимость по эндпоинту плохо описывает отдельный запрос.
ENDPOINTS: dict[str, EndpointSpec] = {
    # Лёгкий I/O при любом параметре. Основная «жертва» блокировок:
    # именно такие запросы страдают, когда рядом считается тяжёлая задача.
    "/api/user/profile": EndpointSpec(
        name="/api/user/profile", kind="io",
        param_name="limit", param_base=100, param_range=(10, 400),
        param_values=(10, 25, 50, 100, 200, 400),
        param_weights=(0.28, 0.24, 0.20, 0.14, 0.09, 0.05),
        exponent=1.0,
        io_wait_ms=25, db_rows=50, payload_bytes=200,
    ),
    # Тяжёлый I/O: почти всё время — ожидание внешнего сервиса.
    "/api/feed": EndpointSpec(
        name="/api/feed", kind="io",
        param_name="limit", param_base=100, param_range=(20, 400),
        param_values=(20, 50, 100, 200, 400),
        param_weights=(0.30, 0.27, 0.22, 0.14, 0.07),
        exponent=1.0,
        io_wait_ms=120, db_rows=200, payload_bytes=300,
    ),
    # Чистый CPU с фиксированной стоимостью: число раундов хеширования
    # задано политикой безопасности и от запроса не зависит. Нужен как
    # контрольный случай — эндпоинт, для которого таблица по маршруту
    # как раз работает.
    "/api/auth/verify": EndpointSpec(
        name="/api/auth/verify", kind="cpu",
        exponent=0.0,
        cpu_iterations=600_000, payload_bytes=150,
    ),
    # Обработка изображения: стоимость растёт как квадрат стороны.
    "/api/image/thumbnail": EndpointSpec(
        name="/api/image/thumbnail", kind="cpu",
        param_name="size", param_base=1400, param_range=(400, 2200),
        param_values=(400, 700, 1000, 1400, 1800, 2200),
        param_weights=(0.30, 0.25, 0.20, 0.13, 0.08, 0.04),
        exponent=2.0,
        image_side=1400, payload_bytes=400,
    ),
    # Гибрид: обращение наружу, затем сортировка выборки. При маленьком
    # limit это лёгкий I/O-запрос, при большом — вычислительная задача.
    # Оптимальный движок для него меняется вместе с параметром.
    "/api/search": EndpointSpec(
        name="/api/search", kind="hybrid",
        param_name="limit", param_base=100, param_range=(10, 600),
        param_values=(10, 30, 60, 100, 250, 600),
        param_weights=(0.26, 0.24, 0.20, 0.15, 0.10, 0.05),
        exponent=1.0,
        io_wait_ms=35, db_rows=400, sort_size=300_000, payload_bytes=250,
    ),
    # Отчёт за период: агрегация растёт быстрее длины периода, поэтому
    # зависимость квадратичная. Линейная поправка «умножить на число
    # дней» для него систематически занижает стоимость.
    "/api/report/generate": EndpointSpec(
        name="/api/report/generate", kind="hybrid",
        param_name="days", param_base=30, param_range=(8, 75),
        param_values=(8, 15, 30, 45, 60, 75),
        param_weights=(0.28, 0.25, 0.20, 0.14, 0.09, 0.04),
        exponent=2.0,
        io_wait_ms=45, db_rows=800, cpu_iterations=300_000,
        sort_size=200_000, payload_bytes=350,
    ),
}


# Смещение распределения параметра в сторону малых значений. Мелкие
# выборки запрашивают часто, крупные редко — так устроен реальный
# трафик, и именно поэтому средняя стоимость по маршруту оказывается
# плохим описанием отдельного запроса: она сдвинута к дешёвому концу,
# а тяжёлый хвост, который и создаёт проблемы, в ней растворяется.
PARAM_SKEW = 1.7


def sample_param(spec: EndpointSpec, rng) -> int | None:
    """
    Выбирает значение параметра из его диапазона.

    Распределение логарифмически равномерное со смещением к малым
    значениям. Значения не привязаны к сетке профилирования: оценка
    стоимости обязана работать и на тех параметрах, которых в замерах
    не было.
    """
    if not spec.param_name:
        return None
    lo, hi = spec.param_range
    u = rng.random() ** PARAM_SKEW
    value = lo * (hi / lo) ** u
    return int(round(value))


ENDPOINT_NAMES = list(ENDPOINTS.keys())


# ---------------------------------------------------------------------------
# Инициализация SQLite
# ---------------------------------------------------------------------------


def ensure_database(rows: int = 20_000) -> None:
    """Создаёт БД с тестовыми данными, если её ещё нет."""
    DATA_DIR.mkdir(exist_ok=True)
    if DB_PATH.exists():
        return

    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        "CREATE TABLE items ("
        "  id INTEGER PRIMARY KEY, category TEXT, score REAL, payload TEXT"
        ")"
    )
    rng = random.Random(42)
    categories = ["alpha", "beta", "gamma", "delta"]
    conn.executemany(
        "INSERT INTO items (category, score, payload) VALUES (?, ?, ?)",
        [
            (rng.choice(categories), rng.random(), "x" * rng.randint(40, 160))
            for _ in range(rows)
        ],
    )
    conn.execute("CREATE INDEX idx_category ON items(category)")
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# Элементарные операции
# ---------------------------------------------------------------------------


def db_read(rows: int) -> float:
    """Настоящее обращение к SQLite: чтение и агрегация строк."""
    if rows <= 0:
        return 0.0
    conn = sqlite3.connect(DB_PATH)
    try:
        cur = conn.execute(
            "SELECT score, payload FROM items WHERE id <= ? ORDER BY score",
            (rows,),
        )
        total = 0.0
        for score, payload in cur:
            total += score * len(payload)
        return total
    finally:
        conn.close()


def cpu_hash(iterations: int) -> str:
    """Криптографическое хеширование — чистая нагрузка на CPU, мало данных."""
    if iterations <= 0:
        return ""
    return hashlib.pbkdf2_hmac(
        "sha256", b"adaptive-routing", b"static-salt", iterations
    ).hex()


def cpu_image(side: int) -> int:
    """Сжатие изображения — нагрузка на CPU с крупными данными."""
    if side <= 0:
        return 0
    rng = random.Random(side)
    img = Image.new("RGB", (side, side))
    img.putdata([
        (rng.randint(0, 255), rng.randint(0, 255), rng.randint(0, 255))
        for _ in range(side * side // 64)
    ] * 64)
    buf = io.BytesIO()
    img.resize((side // 3, side // 3)).save(buf, format="JPEG", quality=85)
    return buf.tell()


def cpu_sort(size: int) -> float:
    """Сортировка и агрегация массива — нагрузка на CPU и аллокатор."""
    if size <= 0:
        return 0.0
    rng = random.Random(size)
    data = [rng.random() for _ in range(size)]
    data.sort()
    return data[size // 2]


def io_wait_sync(wait_ms: int) -> int:
    """Синхронное обращение к внешнему сервису (реальный сокет)."""
    if wait_ms <= 0:
        return 0
    resp = requests.get(STUB_URL, params={"ms": wait_ms}, timeout=30)
    return len(resp.content)


async def io_wait_async(wait_ms: int, client: httpx.AsyncClient) -> int:
    """Асинхронное обращение к тому же сервису."""
    if wait_ms <= 0:
        return 0
    resp = await client.get(STUB_URL, params={"ms": wait_ms}, timeout=30)
    return len(resp.content)


# ---------------------------------------------------------------------------
# Выполнение запроса целиком
# ---------------------------------------------------------------------------


def run_cpu_part(spec: EndpointSpec, param: float | None = None) -> dict[str, Any]:
    """Вычислительная часть запроса. Одинакова для всех движков."""
    factor = spec.factor(param)
    result: dict[str, Any] = {}
    if spec.cpu_iterations:
        result["hash"] = cpu_hash(int(spec.cpu_iterations * factor))[:16]
    if spec.image_side:
        # Для изображения параметр — это и есть сторона, поэтому
        # квадратичная зависимость возникает сама собой, из геометрии.
        side = int(param) if param else spec.image_side
        result["thumb_bytes"] = cpu_image(max(64, min(side, 3000)))
    if spec.sort_size:
        result["median"] = cpu_sort(int(spec.sort_size * factor))
    return result


def run_sync(endpoint: str, param: float | None = None) -> dict[str, Any]:
    """Полное выполнение запроса в синхронном контексте (поток/процесс)."""
    spec = ENDPOINTS[endpoint]
    factor = spec.factor(param)
    out: dict[str, Any] = {"endpoint": endpoint, "param": param}
    if spec.db_rows:
        out["db_total"] = db_read(int(spec.db_rows * factor))
    if spec.io_wait_ms:
        out["io_bytes"] = io_wait_sync(int(spec.io_wait_ms * factor))
    out.update(run_cpu_part(spec, param))
    return out


async def run_async(endpoint: str, client: httpx.AsyncClient,
                    param: float | None = None) -> dict[str, Any]:
    """
    Полное выполнение запроса в асинхронном контексте.

    I/O выполняется неблокирующе. Вычислительная часть выполняется прямо
    в event loop — намеренно, без выноса в executor: именно так ведёт
    себя типичный async-сервис, в который попала CPU-задача, и именно
    этот режим отказа исследуется в работе.
    """
    spec = ENDPOINTS[endpoint]
    factor = spec.factor(param)
    out: dict[str, Any] = {"endpoint": endpoint, "param": param}
    if spec.db_rows:
        out["db_total"] = db_read(int(spec.db_rows * factor))
    if spec.io_wait_ms:
        out["io_bytes"] = await io_wait_async(int(spec.io_wait_ms * factor),
                                              client)
    out.update(run_cpu_part(spec, param))
    return out


def measure_sync(endpoint: str, param: float | None = None
                 ) -> tuple[dict[str, Any], float]:
    """Выполняет запрос и возвращает результат вместе с длительностью в мс."""
    start = time.perf_counter()
    result = run_sync(endpoint, param)
    return result, (time.perf_counter() - start) * 1000
