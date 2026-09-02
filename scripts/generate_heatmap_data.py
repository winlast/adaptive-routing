"""
Генерирует CSV с решениями модели по сетке (load, io_intensity)
при фиксированном cpu_usage — для построения тепловой карты в отчёте.
"""
import csv
import sys
from pathlib import Path

import numpy as np

SCRIPTS_DIR = Path(__file__).resolve().parent
BASE_DIR = SCRIPTS_DIR.parent
DATA_DIR = BASE_DIR / "data"

# Чтобы можно было запускать и как `python generate_heatmap_data.py` из scripts/,
# и через `python scripts/generate_heatmap_data.py` из корня — добавляем корень
# проекта в sys.path, т.к. router.py лежит там.
sys.path.insert(0, str(BASE_DIR))

from router import AdaptiveRouter  # noqa: E402

FIXED_CPU_USAGE = 50.0
LOAD_RANGE = np.linspace(0, 200, 50)
IO_RANGE = np.linspace(0.01, 0.3, 50)

OUTPUT_FILE = DATA_DIR / "heatmap_data.csv"


def main():
    router = AdaptiveRouter(
        model_path=str(DATA_DIR / "router_model.pth"),
        scaler_path=str(DATA_DIR / "scaler.pkl"),
        mode="neural",
        log_path=str(DATA_DIR / "heatmap_generation_log.csv"),  # отдельный лог, не мешает decisions_log.csv
    )

    rows = []
    for load in LOAD_RANGE:
        for io_intensity in IO_RANGE:
            decision = router.decide_backend({
                "load": float(load),
                "io_intensity": float(io_intensity),
                "cpu_usage": FIXED_CPU_USAGE,
            })
            rows.append({
                "load": load,
                "io_intensity": io_intensity,
                "cpu_usage": FIXED_CPU_USAGE,
                "probability": decision["probability"],
                "backend": decision["backend"],
            })

    with open(OUTPUT_FILE, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["load", "io_intensity", "cpu_usage", "probability", "backend"])
        writer.writeheader()
        writer.writerows(rows)

    print(f"Сохранено {len(rows)} точек в {OUTPUT_FILE}")


if __name__ == "__main__":
    main()