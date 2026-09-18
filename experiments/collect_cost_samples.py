"""
Сбор замеров занятости, на которых настраиваются все оценки стоимости.

Это имитация того, что реальная система может собрать о себе сама: во
время работы часть запросов сопровождается замером — фиксируется, сколько
запрос выполнялся и насколько при этом замедлились лёгкие обращения,
шедшие на тот же движок. Никакого знания о внутреннем устройстве
обработчиков здесь не используется, только наблюдаемые времена ответа.

Значения параметра берутся из того же распределения, что и в рабочем
трафике, и не привязаны к сетке: они непрерывны. Поэтому оценка,
настроенная на этих замерах, обязана обобщать на значения, которых в
замерах не было, — а не просто помнить таблицу.

Выборка делится на обучающую и отложенную части по значению параметра,
чтобы точность оценок проверялась на действительно не виденных
значениях, а не на повторах уже измеренного.
"""
from __future__ import annotations

import asyncio
import json
import os
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx

from core.workload import ENDPOINTS, ensure_database, sample_param
from experiments.profile_costs import (
    PROBE_ENDPOINT, PROBE_PARAM, measure_pair, once, probe_baseline,
)

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
TRAIN_PATH = DATA_DIR / "cost_samples.json"
HOLDOUT_PATH = DATA_DIR / "cost_samples_holdout.json"

WORKERS = tuple(os.environ.get("WORKERS", "sync,async").split(","))
SAMPLES_PER_PAIR = 45
HOLDOUT_SHARE = 0.25
SEED = 4242


async def main() -> None:
    ensure_database()
    rng = random.Random(SEED)
    train: list[dict] = []
    holdout: list[dict] = []

    limits = httpx.Limits(max_connections=32, max_keepalive_connections=32)
    async with httpx.AsyncClient(limits=limits) as client:
        base = {}
        for worker in WORKERS:
            await once(client, worker, PROBE_ENDPOINT, PROBE_PARAM)
            base[worker] = await probe_baseline(client, worker)

        for endpoint, spec in ENDPOINTS.items():
            for worker in WORKERS:
                for _ in range(SAMPLES_PER_PAIR):
                    param = sample_param(spec, rng)
                    own, dsum, dmax = await measure_pair(
                        client, worker, endpoint, param, base[worker])
                    row = {
                        "endpoint": endpoint,
                        "param": param,
                        "worker": worker,
                        "own_ms": round(own, 2),
                        "block_ms": round(dmax, 2),
                        "damage_sum_ms": round(dsum, 2),
                        "occupancy_ms": round(own + dmax, 2),
                    }
                    (holdout if rng.random() < HOLDOUT_SHARE else train
                     ).append(row)
                print(f"{endpoint:<24} {worker:<6} готово", flush=True)

    DATA_DIR.mkdir(exist_ok=True)
    TRAIN_PATH.write_text(json.dumps(train, indent=1, ensure_ascii=False),
                          encoding="utf-8")
    HOLDOUT_PATH.write_text(json.dumps(holdout, indent=1, ensure_ascii=False),
                            encoding="utf-8")
    print(f"\nОбучающих замеров: {len(train)}, отложенных: {len(holdout)}")
    print(f"Сохранено: {TRAIN_PATH}")


if __name__ == "__main__":
    asyncio.run(main())
