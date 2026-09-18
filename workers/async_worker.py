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
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from core.workload import ensure_database, run_async

app = FastAPI()
WORKER_NAME = "async"

client: httpx.AsyncClient | None = None


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


@app.get("/health")
async def health():
    return {"status": "healthy", "worker": WORKER_NAME}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        app, host="127.0.0.1", port=8202,
        log_level="warning", loop="uvloop", http="httptools",
    )
