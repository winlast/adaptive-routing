"""
Быстро наполняет decisions_log.csv синтетическими данными
для проверки analyze_logs.py без запуска полного стенда.
"""
import sys
import random
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
BASE_DIR = SCRIPTS_DIR.parent
DATA_DIR = BASE_DIR / "data"

sys.path.insert(0, str(BASE_DIR))
from router import AdaptiveRouter  # noqa: E402


def main():
    router = AdaptiveRouter(
        model_path=str(DATA_DIR / "router_model.pth"),
        scaler_path=str(DATA_DIR / "scaler.pkl"),
        mode="neural",
        log_path=str(DATA_DIR / "decisions_log.csv"),
    )

    for _ in range(200):
        data = {
            "load": random.uniform(0, 200),
            "io_intensity": random.uniform(0.01, 0.3),
            "cpu_usage": random.uniform(0, 100),
        }
        mode = random.choice(["heuristic", "neural"])
        router.decide_backend(data, mode_override=mode)

    print(f"Лог наполнен: {DATA_DIR / 'decisions_log.csv'}")


if __name__ == "__main__":
    main()