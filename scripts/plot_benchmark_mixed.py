"""
Визуализация результатов benchmark_mixed.py: сравнение adaptive
против прямого обращения к каждому движку на СМЕШАННОЙ нагрузке.
"""
from pathlib import Path
import pandas as pd
import matplotlib.pyplot as plt

SCRIPTS_DIR = Path(__file__).resolve().parent
BASE_DIR = SCRIPTS_DIR.parent
DATA_DIR = BASE_DIR / "data"
FIGURES_DIR = BASE_DIR / "figures"

INPUT_FILE = DATA_DIR / "benchmark_mixed_results.csv"


def main():
    FIGURES_DIR.mkdir(exist_ok=True)
    df = pd.read_csv(INPUT_FILE)

    fig, axes = plt.subplots(1, 2, figsize=(14, 5))
    for scenario in df["scenario"].unique():
        subset = df[df["scenario"] == scenario].sort_values("concurrency")
        axes[0].plot(subset["concurrency"], subset["throughput_rps"], marker="o", label=scenario)
        axes[1].plot(subset["concurrency"], subset["avg_latency_ms"], marker="o", label=scenario)

    axes[0].set_title("Пропускная способность (смешанная нагрузка)")
    axes[0].set_xlabel("Конкурентная нагрузка")
    axes[0].set_ylabel("RPS")
    axes[0].legend()

    axes[1].set_title("Среднее время ответа (смешанная нагрузка)")
    axes[1].set_xlabel("Конкурентная нагрузка")
    axes[1].set_ylabel("мс")
    axes[1].legend()

    plt.tight_layout()
    plt.savefig(FIGURES_DIR / "benchmark_mixed_comparison.png", dpi=150)
    print(f"Сохранено: {FIGURES_DIR / 'benchmark_mixed_comparison.png'}")

    pivot = df.pivot(index="concurrency", columns="scenario", values="throughput_rps")
    if {"adaptive", "flask_only", "fastapi_only"}.issubset(pivot.columns):
        pivot["best_single"] = pivot[["flask_only", "fastapi_only"]].max(axis=1)
        pivot["gain_pct"] = (pivot["adaptive"] - pivot["best_single"]) / pivot["best_single"] * 100
        print("\nПрирост adaptive vs лучший одиночный движок (смешанная нагрузка):")
        print(pivot[["adaptive", "best_single", "gain_pct"]])
        pivot.to_csv(DATA_DIR / "hypothesis_mixed_throughput.csv")
        print(f"Сохранено: {DATA_DIR / 'hypothesis_mixed_throughput.csv'}")


if __name__ == "__main__":
    main()