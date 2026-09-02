"""
Визуализация результатов benchmark.py + проверка гипотезы:
даёт ли adaptive-режим прирост throughput относительно
лучшего из одиночных движков.
"""
from pathlib import Path

import pandas as pd
import matplotlib.pyplot as plt

SCRIPTS_DIR = Path(__file__).resolve().parent
BASE_DIR = SCRIPTS_DIR.parent
DATA_DIR = BASE_DIR / "data"
FIGURES_DIR = BASE_DIR / "figures"

INPUT_FILE = DATA_DIR / "benchmark_results.csv"


def main():
    FIGURES_DIR.mkdir(exist_ok=True)
    df = pd.read_csv(INPUT_FILE)

    fig, axes = plt.subplots(1, 2, figsize=(14, 5))
    for engine in df["engine"].unique():
        subset = df[df["engine"] == engine].sort_values("concurrency")
        axes[0].plot(subset["concurrency"], subset["throughput_rps"], marker="o", label=engine)
        axes[1].plot(subset["concurrency"], subset["avg_latency_ms"], marker="o", label=engine)

    axes[0].set_title("Пропускная способность")
    axes[0].set_xlabel("Конкурентная нагрузка")
    axes[0].set_ylabel("RPS")
    axes[0].legend()

    axes[1].set_title("Среднее время ответа")
    axes[1].set_xlabel("Конкурентная нагрузка")
    axes[1].set_ylabel("мс")
    axes[1].legend()

    plt.tight_layout()
    plt.savefig(FIGURES_DIR / "benchmark_comparison.png", dpi=150)
    print(f"Сохранено: {FIGURES_DIR / 'benchmark_comparison.png'}")

    pivot_throughput = df.pivot(index="concurrency", columns="engine", values="throughput_rps")
    pivot_latency = df.pivot(index="concurrency", columns="engine", values="avg_latency_ms")

    if {"flask", "fastapi", "adaptive"}.issubset(pivot_throughput.columns):
        pivot_throughput["best_single"] = pivot_throughput[["flask", "fastapi"]].max(axis=1)
        pivot_throughput["throughput_gain_pct"] = (
            (pivot_throughput["adaptive"] - pivot_throughput["best_single"])
            / pivot_throughput["best_single"] * 100
        )

        pivot_latency["best_single"] = pivot_latency[["flask", "fastapi"]].min(axis=1)
        pivot_latency["latency_ratio"] = pivot_latency["best_single"] / pivot_latency["adaptive"]

        print("\n--- Проверка гипотезы ---")
        print("\nПрирост пропускной способности (%) adaptive vs лучший одиночный движок:")
        print(pivot_throughput[["adaptive", "best_single", "throughput_gain_pct"]])

        print("\nОтношение задержек (best_single / adaptive):")
        print(pivot_latency[["adaptive", "best_single", "latency_ratio"]])

        pivot_throughput.to_csv(DATA_DIR / "hypothesis_throughput.csv")
        pivot_latency.to_csv(DATA_DIR / "hypothesis_latency.csv")
        print(f"\nСохранены таблицы: {DATA_DIR / 'hypothesis_throughput.csv'}, {DATA_DIR / 'hypothesis_latency.csv'}")


if __name__ == "__main__":
    main()