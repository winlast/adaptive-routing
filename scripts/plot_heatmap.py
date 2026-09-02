"""
Визуализация тепловой карты решений роутера.
Вход: data/heatmap_data.csv
Выход: figures/heatmap_probability.png, figures/heatmap_decision.png
"""
from pathlib import Path

import pandas as pd
import matplotlib.pyplot as plt
import seaborn as sns

SCRIPTS_DIR = Path(__file__).resolve().parent
BASE_DIR = SCRIPTS_DIR.parent
DATA_DIR = BASE_DIR / "data"
FIGURES_DIR = BASE_DIR / "figures"

INPUT_FILE = DATA_DIR / "heatmap_data.csv"


def main():
    FIGURES_DIR.mkdir(exist_ok=True)
    df = pd.read_csv(INPUT_FILE)

    df["load_r"] = df["load"].round(1)
    df["io_r"] = df["io_intensity"].round(3)

    pivot_prob = df.pivot_table(
        index="io_r", columns="load_r", values="probability", aggfunc="mean"
    )

    plt.figure(figsize=(12, 8))
    sns.heatmap(pivot_prob, cmap="RdYlBu_r", cbar_kws={"label": "Вероятность выбора FastAPI"})
    plt.title("Тепловая карта: вероятность выбора FastAPI\n(cpu_usage зафиксирован)")
    plt.xlabel("load")
    plt.ylabel("io_intensity")
    plt.gca().invert_yaxis()
    plt.tight_layout()
    plt.savefig(FIGURES_DIR / "heatmap_probability.png", dpi=150)
    print(f"Сохранено: {FIGURES_DIR / 'heatmap_probability.png'}")
    plt.close()

    df["decision_binary"] = (df["backend"] == "fastapi").astype(int)
    pivot_decision = df.pivot_table(
        index="io_r", columns="load_r", values="decision_binary", aggfunc="mean"
    )

    plt.figure(figsize=(12, 8))
    sns.heatmap(pivot_decision, cmap="coolwarm", cbar_kws={"label": "0 = Flask, 1 = FastAPI"})
    plt.title("Граница принятия решения нейросети")
    plt.xlabel("load")
    plt.ylabel("io_intensity")
    plt.gca().invert_yaxis()
    plt.tight_layout()
    plt.savefig(FIGURES_DIR / "heatmap_decision.png", dpi=150)
    print(f"Сохранено: {FIGURES_DIR / 'heatmap_decision.png'}")
    plt.close()


if __name__ == "__main__":
    main()