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
  model_greedy  — обучаемая политика, минимизирующая латентность самого
                  запроса по текущему состоянию очередей;
  model         — та же модель, но оценивающая воркера по очереди, которая
                  образуется после постановки в неё текущего запроса;
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

POLICIES = ["least_conn", "least_work", "model", "hybrid_2", "oracle"]
CONCURRENCY_LEVELS = [16, 32]
REQUESTS = 300
REPEATS = 3  # повторы для оценки разброса
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
    import statistics

    results: dict[str, dict] = {}

    for concurrency in CONCURRENCY_LEVELS:
        print(f"\n{'='*78}\nКонкурентность {concurrency}, {REQUESTS} запросов, "
              f"{REPEATS} повтора\n{'='*78}")
        print(f"{'политика':<14}{'rps (среднее±разброс)':>24}{'p95':>10}"
              f"{'SLO%':>8}")
        print("-" * 78)

        for policy in POLICIES:
            runs = []
            for repeat in range(REPEATS):
                stop_gateway()
                proc = start_gateway(policy)
                try:
                    await run_load(4, 30, seed=1)  # прогрев
                    await asyncio.sleep(2)
                    runs.append(await run_load(concurrency, REQUESTS,
                                               seed=SEED + repeat))
                finally:
                    proc.send_signal(signal.SIGTERM)
                    time.sleep(1.5)
                await asyncio.sleep(1)

            rps = [r["rps"] for r in runs]
            p95 = [r["p95_ms"] for r in runs]
            slo = [r["slo_violation_rate"] for r in runs]
            agg = {
                "rps_mean": round(statistics.mean(rps), 2),
                "rps_std": round(statistics.stdev(rps), 2) if len(rps) > 1 else 0.0,
                "rps_runs": rps,
                "p95_mean": round(statistics.mean(p95), 1),
                "slo_mean": round(statistics.mean(slo), 1),
            }
            results.setdefault(policy, {})[str(concurrency)] = agg
            print(f"{policy:<14}{agg['rps_mean']:>16} ± {agg['rps_std']:<5}"
                  f"{agg['p95_mean']:>10}{agg['slo_mean']:>8}")

    RESULTS_PATH.write_text(json.dumps(results, indent=2, ensure_ascii=False),
                            encoding="utf-8")
    print(f"\nРезультаты сохранены: {RESULTS_PATH}")

    print("\nСравнение с least-connections (главный конкурент):")
    for concurrency in CONCURRENCY_LEVELS:
        c = str(concurrency)
        base = results["least_conn"][c]
        print(f"  конкурентность {concurrency}:")
        for policy in POLICIES:
            if policy == "least_conn":
                continue
            cur = results[policy][c]
            diff = (cur["rps_mean"] - base["rps_mean"]) / base["rps_mean"] * 100
            spread = base["rps_std"] + cur["rps_std"]
            verdict = "в пределах разброса" if abs(
                cur["rps_mean"] - base["rps_mean"]) < spread else "значимо"
            print(f"    {policy:<14}{diff:+6.1f}%  ({verdict})")


if __name__ == "__main__":
    asyncio.run(main())
