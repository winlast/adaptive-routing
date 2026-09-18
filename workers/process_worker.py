"""
Процессный воркер: асинхронный приём запросов, исполнение в пуле процессов.

Сильная сторона — настоящий параллелизм на вычислениях: процессы не делят
GIL, поэтому N CPU-задач действительно выполняются на N ядрах, а не
по очереди.

Слабая сторона — пул фиксированного размера. Когда все процессы заняты,
запросы встают в очередь, и время ожидания добавляется к времени
выполнения. Кроме того, аргументы и результат каждой задачи проходят
через сериализацию между процессами.

Размер пула намеренно ограничен четырьмя процессами: это типичная
production-настройка (а не «по числу ядер»), и именно ограниченность пула
делает выбор воркера зависимым от текущей загрузки, а не только от типа
запроса.
"""
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import asyncio

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from core.workload import ensure_database, run_sync

app = FastAPI()
WORKER_NAME = "process"
POOL_SIZE = 4

executor: ProcessPoolExecutor | None = None


@app.on_event("startup")
async def startup():
    global executor
    ensure_database()
    executor = ProcessPoolExecutor(max_workers=POOL_SIZE)


@app.on_event("shutdown")
async def shutdown():
    if executor:
        executor.shutdown(wait=False, cancel_futures=True)


@app.post("/process")
async def process(request: Request):
    payload = await request.json()
    endpoint = payload.get("endpoint")
    loop = asyncio.get_running_loop()
    result = await loop.run_in_executor(executor, run_sync, endpoint)
    result["worker"] = WORKER_NAME
    return JSONResponse(result)


@app.get("/health")
async def health():
    return {"status": "healthy", "worker": WORKER_NAME, "pool_size": POOL_SIZE}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        app, host="127.0.0.1", port=8203,
        log_level="warning", loop="uvloop", http="httptools",
    )
