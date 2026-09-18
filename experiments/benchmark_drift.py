"""
Эксперимент с изменением условий во время работы.

Все предыдущие замеры велись в стационарном режиме: характеристики
воркеров не менялись, поэтому однажды измеренная таблица стоимостей
оставалась верной до конца прогона. В таких условиях арифметика по
фиксированной таблице заведомо сильна, и обучению негде себя проявить.

Реальные системы так себя не ведут. Внешний сервис деградирует, база
замедляется под нагрузкой, меняется профиль трафика — и любая
предвычисленная таблица устаревает. Здесь это воспроизводится прямо:
в середине прогона один из воркеров теряет большую часть своей ёмкости —
у пула процессов остаётся одно рабочее место из четырёх. Это ухудшение
асимметрично: оно меняет относительный порядок воркеров, поэтому
оптимальный выбор действительно смещается, и политике есть к чему
приспосабливаться. Предыдущая версия эксперимента замедляла общую
внешнюю зависимость, отчего все воркеры дорожали одинаково и
адаптироваться было не к чему.

Смысл замера — отделить политики, которые подстраиваются под изменение,
от тех, которые продолжают действовать по устаревшим представлениям.
Сравниваются три группы:

  * least_conn      — не использует никаких оценок, его деградация не
                      обманывает, но он и не различает запросы;
  * least_work      — фиксированная таблица, измеренная до изменения;
  * adaptive_work   — та же арифметика, но с обновлением оценок на лету;
  * online_model    — обученная модель с онлайн-коррекцией предсказаний.

Ожидание, которое проверяется: политики с фиксированными представлениями
проседают после деградации, адаптивные — восстанавливаются.
"""
import asyncio
import json
import os
import signal
import statistics
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx

from experiments.load_generator import build_plan, SLO_MS

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
RESULTS_PATH = DATA_DIR / "drift_comparison.json"

GATEWAY_URL = "http://127.0.0.1:8300/route"
STUB_CONTROL = "http://127.0.0.1:8100/slowdown"
PROCESS_CAPACITY = "http://127.0.0.1:8203/capacity"

POLICIES = ["least_conn", "least_work", "adaptive_work", "online_model"]
CONCURRENCY = 24
REQUESTS_BEFORE = 250   # стационарный режим
REQUESTS_AFTER = 350    # после деградации зависимости
DEGRADED_CAPACITY = 1   # у пула процессов остаётся одно место из четырёх
REPEATS = 2


def stop_gateway() -> None:
    subprocess.run(["pkill", "-f", "python gateway.py"], capture_output=True)
    time.sleep(2)


def start_gateway(policy: str) -> subprocess.Popen:
    env = dict(os.environ)
    env["POLICY"] = policy
    env["GATEWAY_LOG"] = str(DATA_DIR / "drift_requests_log.csv")
    proc = subprocess.Popen(
        [sys.executable, "gateway.py"], cwd=str(BASE_DIR), env=env,
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


def set_capacity(workers: int) -> None:
    """Меняет ёмкость процессного воркера, ухудшая его относительно других."""
    httpx.post(PROCESS_CAPACITY, params={"workers": workers}, timeout=10)


async def _worker_loop(client, plan_slice, results):
    for endpoint in plan_slice:
        start = time.perf_counter()
        try:
            resp = await client.post(GATEWAY_URL, json={"endpoint": endpoint},
                                     timeout=300)
            resp.raise_for_status()
            results.append((time.perf_counter() - start) * 1000)
        except Exception:
            pass


async def run_phase(total: int, seed: int) -> list[float]:
    plan = build_plan(total, seed)
    slices = [plan[i::CONCURRENCY] for i in range(CONCURRENCY)]
    results: list[float] = []
    limits = httpx.Limits(max_connections=CONCURRENCY + 20,
                          max_keepalive_connections=CONCURRENCY + 20)
    async with httpx.AsyncClient(limits=limits) as client:
        await asyncio.gather(*[
            _worker_loop(client, slices[i], results) for i in range(CONCURRENCY)
        ])
    return results


def summarize(values: list[float]) -> dict:
    if not values:
        return {"rps": 0, "avg": None, "p95": None, "slo": None}
    ordered = sorted(values)
    return {
        "avg": round(statistics.mean(values), 1),
        "p95": round(ordered[min(int(len(ordered) * 0.95), len(ordered) - 1)], 1),
        "slo": round(sum(1 for v in values if v > SLO_MS) / len(values) * 100, 1),
    }


async def main() -> None:
    results: dict[str, dict] = {}

    print(f"Конкурентность {CONCURRENCY}. Фаза 1 — обычный режим, "
          f"фаза 2 — у процессного воркера осталось {DEGRADED_CAPACITY} место "
          f"из 4.")
    print(f"{'политика':<16}{'до: avg':>10}{'до: SLO%':>10}"
          f"{'после: avg':>13}{'после: SLO%':>13}{'рост avg':>11}")
    print("-" * 74)

    for policy in POLICIES:
        before_runs, after_runs = [], []
        for repeat in range(REPEATS):
            set_capacity(4)
            stop_gateway()
            proc = start_gateway(policy)
            try:
                await run_phase(40, seed=1)          # прогрев
                before = await run_phase(REQUESTS_BEFORE, seed=500 + repeat)
                set_capacity(DEGRADED_CAPACITY)      # воркер потерял ресурсы
                after = await run_phase(REQUESTS_AFTER, seed=900 + repeat)
            finally:
                proc.send_signal(signal.SIGTERM)
                time.sleep(1.5)
                set_capacity(4)
            before_runs += before
            after_runs += after
            await asyncio.sleep(2)

        b, a = summarize(before_runs), summarize(after_runs)
        growth = (a["avg"] / b["avg"] - 1) * 100 if b["avg"] else 0
        results[policy] = {"before": b, "after": a, "avg_growth_pct": round(growth, 1)}
        print(f"{policy:<16}{b['avg']:>10}{b['slo']:>10}"
              f"{a['avg']:>13}{a['slo']:>13}{growth:>10.0f}%")

    RESULTS_PATH.write_text(json.dumps(results, indent=2, ensure_ascii=False),
                            encoding="utf-8")
    print(f"\nРезультаты сохранены: {RESULTS_PATH}")

    print("\nДоля трафика на деградировавший воркер после ухудшения —")
    print("ключевой показатель: заметила ли политика, что он испортился.")

    fixed = results["least_work"]["after"]
    adaptive = results["adaptive_work"]["after"]
    online = results["online_model"]["after"]
    print("\nПосле деградации зависимости, средняя задержка:")
    print(f"  фиксированная таблица : {fixed['avg']} мс, SLO {fixed['slo']}%")
    print(f"  адаптивные оценки     : {adaptive['avg']} мс, SLO {adaptive['slo']}%")
    print(f"  модель с коррекцией   : {online['avg']} мс, SLO {online['slo']}%")


if __name__ == "__main__":
    asyncio.run(main())
