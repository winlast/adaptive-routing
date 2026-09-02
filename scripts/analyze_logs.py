"""
Анализ лога решений роутера (data/decisions_log.csv) для научной работы.
"""
from pathlib import Path

import pandas as pd
import matplotlib.pyplot as plt

SCRIPTS_DIR = Path(__file__).resolve().parent
BASE_DIR = SCRIPTS_DIR.parent
DATA_DIR = BASE_DIR / "data"
FIGURES_DIR = BASE_DIR / "figures"

LOG_FILE = DATA_DIR / "decisions_log.csv"


def main():
    FIGURES_DIR.mkdir(exist_ok=True)
    df = pd.read_csv(LOG_FILE)

    if df.empty:
        print("Лог пуст — сначала накопите данные через main.py")
        return

    print("=" * 60)
    print(f"Всего решений: {len(df)}")
    print("=" * 60)

    print("\nРаспределение по бэкендам:")
    print(df["backend"].value_counts())

    print("\nРаспределение по режимам:")
    print(df["mode"].value_counts())

    print("\nБэкенд по режимам (кросс-таблица):")
    cross = pd.crosstab(df["mode"], df["backend"])
    print(cross)

    neural_df = df[df["mode"] == "neural"]
    if not neural_df.empty:
        avg_prob = neural_df["probability"].mean()
        print(f"\nСредняя вероятность FastAPI (neural): {avg_prob:.4f}")
        print("\nСредняя вероятность по итоговому бэкенду:")
        print(neural_df.groupby("backend")["probability"].mean())

    print("\nВремя принятия решения (мс), статистика по режимам:")
    print(df.groupby("mode")["decision_time_ms"].describe()[["mean", "std", "min", "max"]])

    fig, axes = plt.subplots(1, 2, figsize=(14, 5))

    df["backend"].value_counts().plot(kind="bar", ax=axes[0], color=["#4C72B0", "#DD8452"])
    axes[0].set_title("Количество запросов по бэкенду")
    axes[0].set_xlabel("Backend")
    axes[0].set_ylabel("Количество")

    df.boxplot(column="decision_time_ms", by="mode", ax=axes[1])
    axes[1].set_title("Время принятия решения по режимам")
    axes[1].set_xlabel("Режим")
    axes[1].set_ylabel("Время (мс)")
    plt.suptitle("")

    plt.tight_layout()
    plt.savefig(FIGURES_DIR / "logs_analysis.png", dpi=150)
    print(f"\nСохранено: {FIGURES_DIR / 'logs_analysis.png'}")

    summary = {
        "total_requests": len(df),
        "flask_count": int((df["backend"] == "flask").sum()),
        "fastapi_count": int((df["backend"] == "fastapi").sum()),
        "avg_probability_fastapi": neural_df["probability"].mean() if not neural_df.empty else None,
        "avg_decision_time_heuristic_ms": df[df["mode"] == "heuristic"]["decision_time_ms"].mean(),
        "avg_decision_time_neural_ms": df[df["mode"] == "neural"]["decision_time_ms"].mean(),
    }
    pd.DataFrame([summary]).to_csv(DATA_DIR / "logs_summary.csv", index=False)
    print(f"Сохранено: {DATA_DIR / 'logs_summary.csv'}")


if __name__ == "__main__":
    main()