"""
Генерирует синтетический датасет для обучения RouterClassifier.

Правило разметки физически привязано к тому, что реально делают бэкенды
(см. workload.py): каждый запрос — это либо I/O-ожидание, либо CPU-bound
вычисление, и это целиком определяется его io_intensity. FastAPI (1)
размечается как оптимальный бэкенд для I/O-bound запросов (asyncio.sleep
не блокирует event loop), Flask (0) — для CPU-bound (busy-loop в своём
потоке не блокирует остальные запросы). `load` не входит в правило
разметки напрямую — это контекстный признак текущей нагрузки системы,
который эвристический режим использует отдельно (см. router.py).
"""
import sys
from pathlib import Path

import pandas as pd
import numpy as np

SCRIPTS_DIR = Path(__file__).resolve().parent
BASE_DIR = SCRIPTS_DIR.parent
DATA_DIR = BASE_DIR / "data"
sys.path.insert(0, str(BASE_DIR))

from workload import IO_INTENSITY_THRESHOLD  # noqa: E402

OUTPUT_FILE = DATA_DIR / "router_data.csv"


def generate_data(num_samples=10000, filename=OUTPUT_FILE):
    np.random.seed(42)

    current_load = np.random.randint(0, 201, size=num_samples)
    io_intensity = np.random.uniform(0.0, 1.0, size=num_samples)
    cpu_usage = np.random.uniform(0.0, 100.0, size=num_samples)

    best_backend = (io_intensity >= IO_INTENSITY_THRESHOLD).astype(int)

    df = pd.DataFrame({
        'current_load': current_load,
        'io_intensity': io_intensity,
        'cpu_usage': cpu_usage,
        'best_backend': best_backend
    })

    DATA_DIR.mkdir(exist_ok=True)
    df.to_csv(filename, index=False)
    print(f"Сгенерировано {num_samples} строк. Данные сохранены в '{filename}'")

    print("\nРаспределение классов:")
    print(df['best_backend'].value_counts(normalize=True))


if __name__ == "__main__":
    generate_data()