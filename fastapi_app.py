"""
FastAPI-приложение с эндпоинтом /process.

Та же модель нагрузки, что и в flask_app.py (см. workload.py), но
CPU-bound ветка выполняется НАПРЯМУЮ внутри async-хендлера — без
вынесения в executor. Это намеренно: одиночный event loop uvicorn
на время busy-loop полностью останавливается, и ни один другой
конкурентный запрос (в том числе I/O-bound, ожидающий в asyncio.sleep)
не может быть обработан. Это реалистичный сценарий деградации async-
сервиса при попадании в него CPU-тяжёлых запросов — именно от него
и должна защищать адаптивная маршрутизация.
"""
import asyncio

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from workload import cpu_bound_work, io_bound_duration, is_io_bound

app = FastAPI()


@app.api_route("/process", methods=["GET", "POST"])
async def process(request: Request):
    if request.method == "POST":
        payload = await request.json()
    else:
        payload = dict(request.query_params)

    io_intensity = float(payload.get("io_intensity", 0.1))
    cpu_usage = float(payload.get("cpu_usage", 50.0))

    if is_io_bound(io_intensity):
        task_type = "io_bound"
        await asyncio.sleep(io_bound_duration(io_intensity))
    else:
        task_type = "cpu_bound"
        cpu_bound_work(cpu_usage)  # намеренно без await/executor — блокирует event loop

    return JSONResponse({
        "status": "ok",
        "backend": "fastapi",
        "task_type": task_type,
    })


@app.get("/health")
async def health():
    return {"status": "healthy", "backend": "fastapi"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000, loop="uvloop", http="httptools")
