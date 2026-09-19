"""
Внешний сервис, к которому обращаются воркеры при I/O-операциях.

Моделирует соседний микросервис или базу данных за сетью: принимает
запрос, ждёт заданное время и отвечает. Благодаря этому I/O в
эксперименте настоящий — реальный сокет, реальное ожидание ответа, — а
не вызов sleep() внутри самого воркера.

Сервис асинхронный и держит тысячи одновременных соединений, поэтому он
сам никогда не становится узким местом эксперимента.
"""
import asyncio

from fastapi import FastAPI
from fastapi.responses import JSONResponse

app = FastAPI()

PAYLOAD = "response-body-" + "d" * 512

# Множитель задержки. Позволяет на ходу имитировать деградацию внешней
# зависимости: замедление базы, тормозящий сторонний API, сетевые
# проблемы. В реальных системах это происходит постоянно, и именно такие
# изменения делают однажды измеренную таблицу стоимостей устаревшей.
state = {"slowdown": 1.0}


@app.get("/wait")
async def wait(ms: int = 0):
    await asyncio.sleep(ms * state["slowdown"] / 1000)
    return JSONResponse({"waited_ms": ms, "payload": PAYLOAD})


@app.post("/slowdown")
async def set_slowdown(factor: float = 1.0):
    """Меняет скорость ответа внешнего сервиса во время эксперимента."""
    state["slowdown"] = factor
    return {"slowdown": factor}


@app.get("/health")
async def health():
    return {"status": "healthy"}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        app, host="127.0.0.1", port=8100,
        log_level="warning", loop="uvloop", http="httptools",
    )
