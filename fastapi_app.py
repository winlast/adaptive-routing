"""
Аналогичное FastAPI-приложение с эндпоинтом /process.
Эмулирует I/O-операцию асинхронным sleep.
"""
import asyncio
import uuid

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

app = FastAPI()


@app.api_route("/process", methods=["GET", "POST"])
async def process(request: Request):
    # КРИТИЧНЫЙ ФИКС: читаем тело, чтобы Uvicorn не рвал Keep-Alive соединение
    if request.method == "POST":
        _ = await request.body()

    request_id = request.query_params.get("id") or str(uuid.uuid4())
    
    # Эмуляция неблокирующей I/O-операции
    await asyncio.sleep(0.1)

    return JSONResponse({
        "status": "ok",
        "backend": "fastapi",
        "id": request_id,
    })


@app.get("/health")
async def health():
    return {"status": "healthy", "backend": "fastapi"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000, loop="uvloop", http="httptools")