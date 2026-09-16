"""
Визуализация результатов benchmark_realistic.py: adaptive vs flask_only vs
fastapi_only на одном и том же перемежающемся смешанном потоке (CPU-bound +
I/O-bound одновременно, не последовательными фазами).
"""
from pathlib import Path
import pandas as pd
import matplotlib.pyplot as plt

SCRIPTS_DIR = Path(__file__).resolve().parent
BASE_DIR = SCRIPTS_DIR.parent
DATA_DIR = BASE_DIR / "data"
FIGURES_DIR = BASE_DIR / "figures"

INPUT_FILE = DATA_DIR / "benchmark_realistic_results.csv"

LABELS = {"adaptive": "Adaptive (классификатор)", "flask_only": "Flask (весь трафик)",
          "fastapi_only": "FastAPI (весь трафик)"}
COLORS = {"adaptive": "#2E7D32", "flask_only": "#1976D2", "fastapi_only": "#C62828"}


def main():
    FIGURES_DIR.mkdir(exist_ok=True)
    df = pd.read_csv(INPUT_FILE)

    fig, axes = plt.subplots(1, 3, figsize=(18, 5))
    for scenario in ["adaptive", "flask_only", "fastapi_only"]:
        subset = df[df["scenario"] == scenario].sort_values("concurrency")
        style = dict(marker="o", label=LABELS[scenario], color=COLORS[scenario])
        axes[0].plot(subset["concurrency"], subset["throughput_rps"], **style)
        axes[1].plot(subset["concurrency"], subset["avg_latency_ms"], **style)
        axes[2].plot(subset["concurrency"], subset["p99_latency_ms"], **style)

    axes[0].set_title("Пропускная способность")
    axes[0].set_xlabel("Конкурентность")
    axes[0].set_ylabel("RPS")
    axes[0].legend()

    axes[1].set_title("Средняя задержка")
    axes[1].set_xlabel("Конкурентность")
    axes[1].set_ylabel("мс")
    axes[1].legend()

    axes[2].set_title("p99 задержка")
    axes[2].set_xlabel("Конкурентность")
    axes[2].set_ylabel("мс")
    axes[2].legend()

    plt.suptitle("Смешанный поток (70% I/O-bound + 30% CPU-bound), перемежающиеся запросы")
    plt.tight_layout()
    plt.savefig(FIGURES_DIR / "benchmark_realistic_comparison.png", dpi=150)
    print(f"Сохранено: {FIGURES_DIR / 'benchmark_realistic_comparison.png'}")

    pivot = df.pivot(index="concurrency", columns="scenario", values="throughput_rps")
    if {"adaptive", "flask_only", "fastapi_only"}.issubset(pivot.columns):
        pivot["best_single"] = pivot[["flask_only", "fastapi_only"]].max(axis=1)
        pivot["gain_vs_best_single_pct"] = (pivot["adaptive"] - pivot["best_single"]) / pivot["best_single"] * 100
        pivot["gain_vs_flask_pct"] = (pivot["adaptive"] - pivot["flask_only"]) / pivot["flask_only"] * 100
        pivot["gain_vs_fastapi_pct"] = (pivot["adaptive"] - pivot["fastapi_only"]) / pivot["fastapi_only"] * 100
        print("\nПрирост throughput adaptive относительно каждого сценария (%):")
        print(pivot[["adaptive", "flask_only", "fastapi_only", "gain_vs_best_single_pct"]])
        pivot.to_csv(DATA_DIR / "hypothesis_realistic_throughput.csv")
        print(f"Сохранено: {DATA_DIR / 'hypothesis_realistic_throughput.csv'}")


if __name__ == "__main__":
    main()
