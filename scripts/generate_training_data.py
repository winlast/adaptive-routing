"""
Генерирует синтетический датасет для обучения RouterClassifier.
Правило разметки: FastAPI (1), если load > 50 И io_intensity > 0.1, иначе Flask (0).
"""
from pathlib import Path

import pandas as pd
import numpy as np

SCRIPTS_DIR = Path(__file__).resolve().parent
BASE_DIR = SCRIPTS_DIR.parent
DATA_DIR = BASE_DIR / "data"

OUTPUT_FILE = DATA_DIR / "router_data.csv"


def generate_data(num_samples=10000, filename=OUTPUT_FILE):
    np.random.seed(42)

    current_load = np.random.randint(0, 201, size=num_samples)
    io_intensity = np.random.uniform(0.01, 0.3, size=num_samples)
    cpu_usage = np.random.uniform(0.0, 100.0, size=num_samples)

    best_backend = ((current_load > 50) & (io_intensity > 0.1)).astype(int)

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