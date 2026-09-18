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
    """Описание эндпоинта: что он делает и с какими параметрами."""

    name: str
    kind: str  # "io" | "cpu" | "hybrid"
    # Параметры операции. Смысл зависит от kind.
    io_wait_ms: int = 0  # сколько ждёт внешний сервис
    cpu_iterations: int = 0  # итерации PBKDF2
    image_side: int = 0  # сторона изображения для сжатия
    sort_size: int = 0  # размер массива для сортировки
    db_rows: int = 0  # сколько строк читать из SQLite
    payload_bytes: int = 0  # размер тела запроса, который шлёт клиент


# Шесть эндпоинтов с намеренно разными профилями. Оптимальный воркер для
# каждого зависит не только от типа работы, но и от объёма передаваемых
# данных, поэтому свести выбор к одному порогу нельзя.
ENDPOINTS: dict[str, EndpointSpec] = {
    # Лёгкий I/O: быстрый ответ внешнего сервиса. ~28 мс.
    "/api/user/profile": EndpointSpec(
        name="/api/user/profile", kind="io",
        io_wait_ms=25, db_rows=50, payload_bytes=200,
    ),
    # Тяжёлый I/O: долгое ожидание внешнего сервиса. ~123 мс.
    "/api/feed": EndpointSpec(
        name="/api/feed", kind="io",
        io_wait_ms=120, db_rows=200, payload_bytes=300,
    ),
    # Чистый CPU, компактные данные: хеширование пароля. ~120 мс.
    "/api/auth/verify": EndpointSpec(
        name="/api/auth/verify", kind="cpu",
        cpu_iterations=600_000, payload_bytes=150,
    ),
    # Самый тяжёлый CPU: обработка изображения. ~200 мс.
    "/api/image/thumbnail": EndpointSpec(
        name="/api/image/thumbnail", kind="cpu",
        image_side=2000, payload_bytes=400,
    ),
    # Гибрид с перекосом в I/O: обращение наружу, затем сортировка. ~95 мс.
    "/api/search": EndpointSpec(
        name="/api/search", kind="hybrid",
        io_wait_ms=35, db_rows=400, sort_size=300_000, payload_bytes=250,
    ),
    # Гибрид с перекосом в CPU: отчёт по данным из БД. ~140 мс.
    "/api/report/generate": EndpointSpec(
        name="/api/report/generate", kind="hybrid",
        io_wait_ms=45, db_rows=800, cpu_iterations=300_000,
        sort_size=200_000, payload_bytes=350,
    ),
}

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


def run_cpu_part(spec: EndpointSpec) -> dict[str, Any]:
    """CPU-часть запроса. Одинакова для всех воркеров, включая процессы."""
    result: dict[str, Any] = {}
    if spec.cpu_iterations:
        result["hash"] = cpu_hash(spec.cpu_iterations)[:16]
    if spec.image_side:
        result["thumb_bytes"] = cpu_image(spec.image_side)
    if spec.sort_size:
        result["median"] = cpu_sort(spec.sort_size)
    return result


def run_sync(endpoint: str) -> dict[str, Any]:
    """Полное выполнение запроса в синхронном контексте (поток/процесс)."""
    spec = ENDPOINTS[endpoint]
    out: dict[str, Any] = {"endpoint": endpoint}
    if spec.db_rows:
        out["db_total"] = db_read(spec.db_rows)
    if spec.io_wait_ms:
        out["io_bytes"] = io_wait_sync(spec.io_wait_ms)
    out.update(run_cpu_part(spec))
    return out


async def run_async(endpoint: str, client: httpx.AsyncClient) -> dict[str, Any]:
    """
    Полное выполнение запроса в асинхронном контексте.

    I/O выполняется неблокирующе. CPU-часть выполняется прямо в event loop —
    намеренно, без выноса в executor: именно так ведёт себя типичный
    async-сервис, в который попала вычислительная задача, и именно этот
    режим отказа исследуется в работе.
    """
    spec = ENDPOINTS[endpoint]
    out: dict[str, Any] = {"endpoint": endpoint}
    if spec.db_rows:
        out["db_total"] = db_read(spec.db_rows)
    if spec.io_wait_ms:
        out["io_bytes"] = await io_wait_async(spec.io_wait_ms, client)
    out.update(run_cpu_part(spec))
    return out


def measure_sync(endpoint: str) -> tuple[dict[str, Any], float]:
    """Выполняет запрос и возвращает результат вместе с длительностью в мс."""
    start = time.perf_counter()
    result = run_sync(endpoint)
    return result, (time.perf_counter() - start) * 1000
