#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Живая демонстрация: как один тяжёлый запрос останавливает остальные.

Показывает то, что словами объяснить трудно. На странице идёт поток
лёгких обращений, время ответа рисуется в реальном времени. По кнопке
вбрасывается один тяжёлый запрос — и в режиме «всё в асинхронный
движок» график лёгких обращений взлетает: они стоят, пока вычисление не
закончится. В режиме с классификатором тяжёлый запрос уходит в
потоковый движок, и лёгкие продолжают отвечать как ни в чём не бывало.

Запуск (движки должны быть подняты, см. README):

    python demo/live_demo.py     # затем открыть http://127.0.0.1:8400

Переключение режима перезапускает шлюз, поэтому занимает пару секунд.
"""
from __future__ import annotations

import asyncio
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx
from fastapi import FastAPI
from fastapi.responses import HTMLResponse, StreamingResponse

from core.workload import ENDPOINTS, sample_param
from experiments.load_generator import LIGHT_ENDPOINT, TRAFFIC_MIX

BASE = Path(__file__).resolve().parent.parent
GATEWAY_HOST = os.environ.get("GATEWAY_HOST", "127.0.0.1")
GATEWAY = f"http://{GATEWAY_HOST}:8300/route"
PORT = 8400

# Лёгкое обращение: быстрый ответ, именно оно и страдает от блокировки.
LIGHT = {"endpoint": "/api/user/profile", "param": 10}
# Сколько лёгких обращений идёт одновременно.
#
# Величина существенна для достоверности показа. При одном потоке в
# системе с двумя движками всегда остаётся свободный движок, и даже
# обычная балансировка уводит на него лёгкие запросы — блокировка не
# проявляется, а демонстрация показывает не то, что есть в реальности.
# Настоящий сервис обслуживает многих пользователей сразу, поэтому оба
# движка заняты, и тяжёлому запросу неизбежно достаются соседи.
CONCURRENCY = 8
# Окно показа задаётся временем, а не числом точек: при восьми потоках
# сто двадцать измерений покрывают около секунды, и всплеск от тяжёлого
# запроса успевает уехать за край раньше, чем зритель его заметит.
WINDOW_SECONDS = 14.0
# Конкурентность при включённом смешанном потоке.
#
# В этом режиме демонстрация воспроизводит ровно тот трафик, на котором
# получены числа в статьях: тот же состав маршрутов, то же распределение
# параметров, та же конкурентность. Одиночный вброс показывает сам
# механизм блокировки, но политики на нём почти не различаются —
# тяжёлый запрос один, и обычной балансировке есть куда его увести.
# Различие проявляется только на потоке, где заняты оба движка.
MIXED_CONCURRENCY = 24
# Тяжёлое: почти секунда вычислений.
HEAVY = {"endpoint": "/api/report/generate", "param": 75}

MODES = {
    "all_async": "Всё в асинхронный движок",
    "budget_power_30": "С классификатором нагрузки",
    "least_conn": "Обычная балансировка",
}

app = FastAPI()
state: dict = {"mode": "all_async", "proc": None, "samples": [],
               "heavy_at": None, "running": True, "heavy_stream": False}


def stop_gateway() -> None:
    proc = state.get("proc")
    if proc and proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=8)
        except subprocess.TimeoutExpired:
            proc.kill()
    state["proc"] = None


def start_gateway(mode: str) -> None:
    stop_gateway()
    env = dict(os.environ)
    env["POLICY"] = mode
    env["GATEWAY_LOG"] = str(BASE / "data" / "demo_requests_log.csv")
    state["proc"] = subprocess.Popen(
        [sys.executable, "gateway.py"], cwd=str(BASE), env=env,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(50):
        time.sleep(0.3)
        try:
            r = httpx.get(f"http://{GATEWAY_HOST}:8300/health", timeout=2)
            if r.status_code == 200 and r.json().get("policy") == mode:
                state["mode"] = mode
                return
        except Exception:
            continue
    raise RuntimeError(f"шлюз не поднялся в режиме {mode}")


async def pulse() -> None:
    """
    Поток обращений к шлюзу и измерение того, что видит пользователь.

    В спокойном режиме идут только лёгкие обращения — на них видно, как
    единственный тяжёлый запрос останавливает остальные. При включённом
    смешанном потоке воспроизводится ровно тот трафик, на котором
    получены числа в статьях, и на график по-прежнему попадают только
    лёгкие обращения: именно их время видит пользователь сервиса.
    """
    import random

    endpoints = list(TRAFFIC_MIX)
    weights = [TRAFFIC_MIX[e] for e in endpoints]
    limits = httpx.Limits(max_connections=MIXED_CONCURRENCY + 8,
                          max_keepalive_connections=MIXED_CONCURRENCY + 8)

    async with httpx.AsyncClient(timeout=120, limits=limits) as client:

        async def one_stream(rng: random.Random, index: int) -> None:
            while state["running"]:
                mixed = state["heavy_stream"]
                if mixed:
                    endpoint = rng.choices(endpoints, weights=weights, k=1)[0]
                    body = {"endpoint": endpoint,
                            "param": sample_param(ENDPOINTS[endpoint], rng)}
                elif index < CONCURRENCY:
                    endpoint, body = LIGHT["endpoint"], LIGHT
                else:
                    # Лишние потоки работают только в смешанном режиме.
                    await asyncio.sleep(0.2)
                    continue

                start = time.perf_counter()
                try:
                    await client.post(GATEWAY, json=body)
                    ms = (time.perf_counter() - start) * 1000
                except Exception:
                    ms = None
                if endpoint == LIGHT_ENDPOINT:
                    state["samples"].append({"t": time.time(), "ms": ms})
                    del state["samples"][:-4000]
                if not mixed:
                    await asyncio.sleep(0.05)

        rngs = [random.Random(1000 + i) for i in range(MIXED_CONCURRENCY)]
        await asyncio.gather(*[one_stream(rngs[i], i)
                               for i in range(MIXED_CONCURRENCY)])


async def send_heavy() -> None:
    state["heavy_at"] = time.time()
    async with httpx.AsyncClient(timeout=120) as client:
        try:
            await client.post(GATEWAY, json=HEAVY)
        except Exception:
            pass


@app.on_event("startup")
async def startup() -> None:
    start_gateway(state["mode"])
    asyncio.create_task(pulse())


@app.on_event("shutdown")
async def shutdown() -> None:
    state["running"] = False
    stop_gateway()


@app.get("/api/stream")
async def stream():
    async def gen():
        last = 0
        while True:
            cutoff = time.time() - WINDOW_SECONDS
            window = [s for s in state["samples"] if s["t"] >= cutoff]
            # Показываем не больше двухсот точек: на графике шириной в
            # тысячу пикселей большее разрешение ничего не добавляет.
            step = max(1, len(window) // 200)
            samples = window[::step]
            payload = {
                "mode": state["mode"],
                "mode_title": MODES.get(state["mode"], state["mode"]),
                "points": [s["ms"] for s in samples],
                "heavy_stream": state["heavy_stream"],
                "window": WINDOW_SECONDS,
                "heavy_ago": (time.time() - state["heavy_at"])
                if state["heavy_at"] else None,
            }
            yield f"data: {json.dumps(payload)}\n\n"
            await asyncio.sleep(0.2)
    return StreamingResponse(gen(), media_type="text/event-stream")


@app.post("/api/mode/{mode}")
async def set_mode(mode: str):
    if mode not in MODES:
        return {"error": "неизвестный режим"}
    await asyncio.get_running_loop().run_in_executor(None, start_gateway, mode)
    state["samples"].clear()
    state["heavy_at"] = None
    return {"mode": mode}


@app.post("/api/heavy")
async def heavy():
    asyncio.create_task(send_heavy())
    return {"ok": True}


@app.post("/api/heavy-stream/{on}")
async def heavy_stream(on: str):
    state["heavy_stream"] = on == "on"
    state["samples"].clear()
    return {"heavy_stream": state["heavy_stream"]}


@app.get("/", response_class=HTMLResponse)
async def index():
    return (Path(__file__).resolve().parent / "live_demo.html").read_text(
        encoding="utf-8")


if __name__ == "__main__":
    import uvicorn

    print(f"Демонстрация: http://127.0.0.1:{PORT}")
    uvicorn.run(app, host=os.environ.get("BIND_HOST", "127.0.0.1"),
                port=PORT, log_level="warning")
