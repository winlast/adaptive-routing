"""
Основной эксперимент: сравнение политик маршрутизации.

Все политики проверяются в идентичных условиях — тот же шлюз, те же
воркеры, та же последовательность запросов (зерно генератора
фиксировано), та же конкурентность. Между прогонами шлюз перезапускается,
чтобы состояние очередей и соединений не переносилось из предыдущего
замера.

Сравниваются:
  random        — случайный выбор, нижняя граница осмысленности;
  round_robin   — классический балансировщик без учёта нагрузки;
  least_conn    — промышленный балансировщик, учитывает загрузку, но не
                  различает характер запроса;
  static_rule   — экспертное правило «эндпоинт -> воркер», составленное
                  по профилированию системы под нагрузкой;
  model         — обучаемая политика, предсказывает латентность на каждом
                  воркере с учётом текущих очередей;
  oracle        — знает измеренные стоимости заранее, физически
                  нереализуем, задаёт верхнюю границу достижимого.

Ключевое сравнение — model против static_rule. Если обучаемая политика не
выигрывает у грамотной статики, применение машинного обучения в этой
задаче не оправдано, и результат следует признать отрицательным.
"""
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

from experiments.load_generator import run_load

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
RESULTS_PATH = DATA_DIR / "policy_comparison.json"

POLICIES = ["random", "round_robin", "least_conn", "static_rule", "model", "oracle"]
CONCURRENCY_LEVELS = [16, 32]
REQUESTS = 300
SEED = 777


def stop_gateway() -> None:
    subprocess.run(["pkill", "-f", "python gateway.py"], capture_output=True)
    time.sleep(2)


def start_gateway(policy: str) -> subprocess.Popen:
    env = dict(os.environ)
    env["POLICY"] = policy
    env["GATEWAY_LOG"] = str(DATA_DIR / "benchmark_requests_log.csv")
    proc = subprocess.Popen(
        [sys.executable, "gateway.py"],
        cwd=str(BASE_DIR), env=env,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    for _ in range(40):
        time.sleep(0.5)
        try:
            resp = httpx.get("http://127.0.0.1:8300/health", timeout=2)
            if resp.status_code == 200 and resp.json().get("policy") == policy:
                return proc
        except Exception:
            continue
    raise RuntimeError(f"Шлюз не поднялся с политикой {policy}")


async def main() -> None:
    results: dict[str, dict] = {}

    for concurrency in CONCURRENCY_LEVELS:
        print(f"\n{'='*72}\nКонкурентность {concurrency}, {REQUESTS} запросов "
              f"на политику\n{'='*72}")
        print(f"{'политика':<14}{'rps':>8}{'avg':>9}{'p50':>9}{'p95':>9}"
              f"{'p99':>9}{'SLO%':>8}")
        print("-" * 72)

        for policy in POLICIES:
            stop_gateway()
            proc = start_gateway(policy)
            try:
                # Прогрев: первые запросы наполняют кеши и пул процессов.
                await run_load(4, 30, seed=1)
                await asyncio.sleep(2)
                res = await run_load(concurrency, REQUESTS, seed=SEED)
            finally:
                proc.send_signal(signal.SIGTERM)
                time.sleep(1.5)

            results.setdefault(policy, {})[str(concurrency)] = res
            print(f"{policy:<14}{res['rps']:>8}{res['avg_ms']:>9}"
                  f"{res['p50_ms']:>9}{res['p95_ms']:>9}{res['p99_ms']:>9}"
                  f"{res['slo_violation_rate']:>8}")
            await asyncio.sleep(2)

    RESULTS_PATH.write_text(json.dumps(results, indent=2, ensure_ascii=False),
                            encoding="utf-8")
    print(f"\nРезультаты сохранены: {RESULTS_PATH}")

    print("\nГлавное сравнение — обучаемая политика против экспертной статики:")
    for concurrency in CONCURRENCY_LEVELS:
        c = str(concurrency)
        model = results["model"][c]
        static = results["static_rule"][c]
        oracle = results["oracle"][c]
        gain = (model["rps"] - static["rps"]) / static["rps"] * 100
        to_oracle = (model["rps"] - oracle["rps"]) / oracle["rps"] * 100
        print(f"  конкурентность {concurrency}: "
              f"model {model['rps']} rps против static {static['rps']} rps "
              f"({gain:+.1f}%), до oracle {to_oracle:+.1f}%")


if __name__ == "__main__":
    asyncio.run(main())
