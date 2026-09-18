"""
Профилирование: во что обходится запрос и во что он обходится соседям.

Измеряются две величины для каждой комбинации «маршрут, значение
параметра, движок».

Первая — собственное время обработки без конкуренции. Это привычная
стоимость запроса, и она, как выясняется, почти не зависит от того, в
какой движок запрос попал: одна и та же работа выполняется одинаково
долго и в потоке, и в корутине.

Вторая — задержка, которую запрос причиняет другим. Пока он
обрабатывается, на тот же движок непрерывно идут лёгкие обращения, и
измеряется, насколько выросло время их ответа по сравнению с
незагруженным движком. Именно здесь движки расходятся принципиально:
вычислительная задача в event loop останавливает обработку всех
остальных запросов до своего завершения, тогда как в потоковом сервере
планировщик переключает потоки, и соседи продолжают продвигаться.

Суммарная занятость (собственное время плюс причинённая задержка) и есть
та величина, которую имеет смысл складывать по очереди воркера: она
отвечает на вопрос «на сколько подорожает обслуживание остальных, если
отправить запрос сюда», а не «сколько он будет выполняться сам».

Результат сохраняется в data/cost_grid.json и служит одновременно
таблицей для оракула и эталоном для проверки точности обученной модели.
"""
from __future__ import annotations

import asyncio
import json
import os
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx

from core.workload import ENDPOINTS, ensure_database

BASE_DIR = Path(__file__).resolve().parent.parent
OUTPUT = BASE_DIR / "data" / "cost_grid.json"
DETAIL = BASE_DIR / "data" / "cost_grid_detail.json"

WORKER_URLS = {
    "sync": "http://127.0.0.1:8201/process",
    "async": "http://127.0.0.1:8202/process",
    "process": "http://127.0.0.1:8203/process",
}
WORKERS = tuple(os.environ.get("WORKERS", "sync,async").split(","))

# Лёгкое обращение, которым измеряется мешает ли ему сосед.
PROBE_ENDPOINT = "/api/user/profile"
PROBE_PARAM = 10

REPEATS = 3


async def once(client: httpx.AsyncClient, worker: str, endpoint: str,
               param: int | None) -> float:
    start = asyncio.get_running_loop().time()
    resp = await client.post(WORKER_URLS[worker],
                             json={"endpoint": endpoint, "param": param},
                             timeout=300)
    resp.raise_for_status()
    return (asyncio.get_running_loop().time() - start) * 1000


async def probe_baseline(client: httpx.AsyncClient, worker: str) -> float:
    """Время ответа лёгкого обращения на свободном движке."""
    samples = [await once(client, worker, PROBE_ENDPOINT, PROBE_PARAM)
               for _ in range(7)]
    return statistics.median(samples)


async def measure_pair(client: httpx.AsyncClient, worker: str, endpoint: str,
                       param: int | None, probe_base: float
                       ) -> tuple[float, float, float]:
    """
    Запускает запрос и одновременно обстреливает движок лёгкими
    обращениями.

    Возвращает три величины: собственное время запроса, суммарную
    задержку лёгких обращений сверх их нормы и наибольшую задержку
    одного обращения.

    Различие между суммой и максимумом здесь принципиально, а не
    техническое. Сумма измеряет потерю пропускной способности: сколько
    всего рабочего времени отобрано у остальных. Максимум измеряет
    блокировку: насколько долго обработка остальных была остановлена
    целиком. Для двух рассматриваемых движков эти величины расходятся.
    В потоковом сервере планировщик переключает потоки каждые несколько
    миллисекунд, поэтому лёгкий запрос, пришедший во время чужого
    вычисления, продвигается порциями и завершается быстро — суммарная
    потеря есть, блокировки нет. В event loop вычисление выполняется
    неделимо, и лёгкий запрос ждёт его полностью.

    Поскольку предметом работы является именно блокировка и её влияние
    на хвост распределения задержек, для оценки занятости используется
    максимум. Сумма сохраняется для сравнения и для отчёта.
    """
    probe_latencies: list[float] = []
    done = asyncio.Event()

    async def heavy() -> float:
        try:
            return await once(client, worker, endpoint, param)
        finally:
            done.set()

    async def probes() -> None:
        while not done.is_set():
            try:
                probe_latencies.append(
                    await once(client, worker, PROBE_ENDPOINT, PROBE_PARAM))
            except Exception:
                break

    heavy_task = asyncio.create_task(heavy())
    probe_task = asyncio.create_task(probes())
    own = await heavy_task
    await probe_task

    excess = [max(l - probe_base, 0.0) for l in probe_latencies]
    return own, sum(excess), (max(excess) if excess else 0.0)


async def main() -> None:
    ensure_database()
    grid: dict[str, dict[str, list]] = {}
    detail: dict[str, dict[str, list]] = {}

    limits = httpx.Limits(max_connections=32, max_keepalive_connections=32)
    async with httpx.AsyncClient(limits=limits) as client:
        base = {}
        for worker in WORKERS:
            await once(client, worker, PROBE_ENDPOINT, PROBE_PARAM)  # прогрев
            base[worker] = await probe_baseline(client, worker)
            print(f"лёгкое обращение на свободном движке {worker}: "
                  f"{base[worker]:.1f} мс")

        header = "".join(f"{w + ' own':>12}{w + ' блок':>13}" for w in WORKERS)
        print(f"\n{'маршрут':<24}{'параметр':>9}{header}")
        print("-" * (33 + 26 * len(WORKERS)))

        for endpoint, spec in ENDPOINTS.items():
            values = spec.param_values if spec.param_name else (None,)
            grid[endpoint] = {w: [] for w in WORKERS}
            detail[endpoint] = {w: [] for w in WORKERS}
            for param in values:
                line = f"{endpoint:<24}{str(param):>9}"
                for worker in WORKERS:
                    owns, sums, maxes = [], [], []
                    for _ in range(REPEATS):
                        own, dsum, dmax = await measure_pair(
                            client, worker, endpoint, param, base[worker])
                        owns.append(own)
                        sums.append(dsum)
                        maxes.append(dmax)
                        await asyncio.sleep(0.05)
                    own_m = statistics.median(owns)
                    sum_m = statistics.median(sums)
                    dmg_m = statistics.median(maxes)
                    # Занятость движка этим запросом: собственное время
                    # плюс наибольшая блокировка, которую он создаёт.
                    occupancy = own_m + dmg_m
                    key = spec.param_base if param is None else param
                    grid[endpoint][worker].append(
                        [key, round(occupancy, 1), round(dmg_m, 1)])
                    detail[endpoint][worker].append(
                        {"param": key, "own_ms": round(own_m, 1),
                         "block_ms": round(dmg_m, 1),
                         "damage_sum_ms": round(sum_m, 1)})
                    line += f"{own_m:>12.0f}{dmg_m:>13.0f}"
                print(line, flush=True)

    OUTPUT.write_text(json.dumps(grid, indent=2, ensure_ascii=False),
                      encoding="utf-8")
    DETAIL.write_text(json.dumps(detail, indent=2, ensure_ascii=False),
                      encoding="utf-8")
    print(f"\nСохранено: {OUTPUT}")


if __name__ == "__main__":
    asyncio.run(main())
