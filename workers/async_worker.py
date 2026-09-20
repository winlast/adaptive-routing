"""
Асинхронный воркер: FastAPI на uvicorn, единственный event loop.

Сильная сторона — I/O-ожидание почти бесплатно: пока корутина ждёт ответа
внешнего сервиса, event loop обслуживает другие запросы, поэтому тысячи
одновременных I/O-соединений держатся одним потоком.

Слабая сторона — вычислительная задача выполняется прямо в event loop и
на всё время вычисления останавливает обработку ВСЕХ остальных запросов,
включая лёгкие. Это не недосмотр реализации, а исследуемый режим отказа:
именно так ведёт себя типичный async-сервис, в который попала CPU-задача
без выноса в executor.
"""
import os
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from core.workload import ensure_database, run_async

app = FastAPI()
WORKER_NAME = "async"

client: httpx.AsyncClient | None = None

# Фоновая нагрузка, отбирающая у процесса процессорное время. Нужна,
# чтобы воспроизвести обычную для реальных развёртываний ситуацию: рядом
# появился сосед, и движок стал медленнее, хотя код его не менялся.
# Для асинхронного движка это означает, что каждое вычисление держит
# event loop дольше, чем показало профилирование, — то есть снятая
# заранее оценка блокировки становится заниженной.
noise = {"threads": 0, "stop": None}


def _burn(stop_event: threading.Event) -> None:
    value = 0
    while not stop_event.is_set():
        for i in range(20_000):
            value = (value * 31 + i) % 1_000_003
        time.sleep(0)


@app.on_event("startup")
async def startup():
    global client
    ensure_database()
    client = httpx.AsyncClient(
        limits=httpx.Limits(max_connections=1000, max_keepalive_connections=1000)
    )


@app.on_event("shutdown")
async def shutdown():
    if client:
        await client.aclose()


@app.post("/process")
async def process(request: Request):
    payload = await request.json()
    endpoint = payload.get("endpoint")
    result = await run_async(endpoint, client, payload.get("param"))
    result["worker"] = WORKER_NAME
    return JSONResponse(result)


@app.post("/noise")
async def set_noise(threads: int = 0):
    """Включает или выключает фоновую нагрузку на этот движок."""
    if noise["stop"] is not None:
        noise["stop"].set()
        noise["stop"] = None
    noise["threads"] = threads
    if threads > 0:
        stop_event = threading.Event()
        noise["stop"] = stop_event
        for _ in range(threads):
            threading.Thread(target=_burn, args=(stop_event,),
                             daemon=True).start()
    return {"noise_threads": threads}


@app.get("/health")
async def health():
    return {"status": "healthy", "worker": WORKER_NAME,
            "noise_threads": noise["threads"]}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        app, host=os.environ.get("BIND_HOST", "127.0.0.1"), port=8202,
        log_level="warning", loop="uvloop", http="httptools",
    )
