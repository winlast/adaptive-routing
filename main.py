"""
Точка входа: поднимает Flask и FastAPI-бэкенды в отдельных процессах,
и сам выступает как асинхронный gateway (FastAPI + httpx.AsyncClient).

ВАЖНО (задокументированное архитектурное ограничение): decide_backend()
вызывается синхронно в event loop'е gateway. Это ограничивает
пропускную способность gateway одним ядром CPU при очень высокой
конкурентности (экспериментально показано: деградация начинается
при concurrency > 50-100). Попытки распараллелить через threadpool
и ProcessPoolExecutor не дали устойчивого выигрыша (см. раздел
"Ограничения" в отчёте) — накладные расходы на межпроцессное
взаимодействие оказались сопоставимы с выигрышем от параллелизма
для такой лёгкой модели. Прямой синхронный вызов оказался самым
быстрым и стабильным вариантом на практике.
"""
import os
import multiprocessing
import random
import time
from pathlib import Path

import httpx
import requests
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from router import AdaptiveRouter

import torch
torch.set_num_threads(1)

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"

FLASK_PORT = 5000
FASTAPI_PORT = 8000
GATEWAY_PORT = 9000

BACKEND_URLS = {
    "flask": f"http://127.0.0.1:{FLASK_PORT}/process",
    "fastapi": f"http://127.0.0.1:{FASTAPI_PORT}/process",
}

AB_TEST_ENABLED = os.environ.get("AB_TEST_ENABLED", "false").lower() == "true"
AB_TEST_HEURISTIC_SHARE = float(os.environ.get("AB_TEST_HEURISTIC_SHARE", "0.5"))
PRODUCTION_MODE = os.environ.get("ROUTER_MODE", "neural")


def run_flask():
    from flask_app import app
    app.run(host="0.0.0.0", port=FLASK_PORT, threaded=True)


def run_fastapi():
    from fastapi_app import app
    uvicorn.run(app, host="0.0.0.0", port=FASTAPI_PORT, log_level="warning")


def wait_for_backend(url: str, timeout: float = 10.0):
    deadline = time.time() + timeout
    last_error = None
    health_url = url.replace("/process", "/health")
    while time.time() < deadline:
        try:
            r = requests.get(health_url, timeout=0.5)
            if r.status_code == 200:
                return True
        except requests.exceptions.RequestException as e:
            last_error = e
        time.sleep(0.2)
    raise RuntimeError(f"Бэкенд не поднялся вовремя: {url}. Последняя ошибка: {last_error}")


gateway_app = FastAPI()

router = AdaptiveRouter(
    model_path=str(DATA_DIR / "router_model.pth"),
    scaler_path=str(DATA_DIR / "scaler.pkl"),
    mode=PRODUCTION_MODE,
    log_path=str(DATA_DIR / "decisions_log.csv"),
)
router.decide_backend({"load": 0, "io_intensity": 0.1, "cpu_usage": 50})  # прогрев

http_client: httpx.AsyncClient | None = None


@gateway_app.on_event("startup")
async def startup_event():
    global http_client
    http_client = httpx.AsyncClient(
        limits=httpx.Limits(max_connections=500, max_keepalive_connections=500),
        timeout=10.0,
    )


@gateway_app.on_event("shutdown")
async def shutdown_event():
    await http_client.aclose()


def pick_ab_mode() -> str:
    return "heuristic" if random.random() < AB_TEST_HEURISTIC_SHARE else "neural"


def resolve_mode(request_data: dict) -> str:
    if not AB_TEST_ENABLED:
        return PRODUCTION_MODE
    explicit_mode = request_data.get("mode")
    if explicit_mode in ("heuristic", "neural"):
        return explicit_mode
    return pick_ab_mode()


@gateway_app.post("/route")
async def route_request(request: Request):
    request_data = await request.json()
    mode = resolve_mode(request_data)

    decision = router.decide_backend(request_data, mode)
    backend = decision["backend"]
    target_url = BACKEND_URLS[backend]

    gateway_start = time.perf_counter()
    try:
        resp = await http_client.post(target_url, json=request_data)
        resp.raise_for_status()
    except httpx.HTTPError as e:
        return JSONResponse(
            {"status": "error", "message": f"Ошибка при обращении к бэкенду '{backend}': {e}"},
            status_code=502,
        )
    backend_call_time_ms = (time.perf_counter() - gateway_start) * 1000

    payload = resp.json()
    payload["routed_by"] = "AdaptiveRouter"
    payload["routing_mode"] = decision["mode"]
    payload["routing_probability"] = decision["probability"]
    payload["decision_time_ms"] = decision["decision_time_ms"]
    payload["backend_call_time_ms"] = round(backend_call_time_ms, 4)
    payload["ab_test_enabled"] = AB_TEST_ENABLED

    return payload


@gateway_app.get("/stats")
async def stats():
    return router.stats()


@gateway_app.get("/config")
async def config():
    return {
        "ab_test_enabled": AB_TEST_ENABLED,
        "ab_test_heuristic_share": AB_TEST_HEURISTIC_SHARE,
        "production_mode": PRODUCTION_MODE,
    }


def main():
    flask_proc = multiprocessing.Process(target=run_flask, daemon=True)
    fastapi_proc = multiprocessing.Process(target=run_fastapi, daemon=True)
    flask_proc.start()
    fastapi_proc.start()

    print("Ожидание запуска бэкендов...")
    wait_for_backend(BACKEND_URLS["flask"])
    wait_for_backend(BACKEND_URLS["fastapi"])
    print("Оба бэкенда готовы.")

    print(f"A/B-тест: {'включён' if AB_TEST_ENABLED else 'выключен'}")
    print(f"Режим по умолчанию: {PRODUCTION_MODE}")
    print(f"Gateway запускается на порту {GATEWAY_PORT}...")
    uvicorn.run(
        gateway_app,
        host="0.0.0.0",
        port=GATEWAY_PORT,
        log_level="warning",
        loop="uvloop",
        http="httptools",
    )


if __name__ == "__main__":
    main()