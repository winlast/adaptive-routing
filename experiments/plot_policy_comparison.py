"""
Графики по результатам сравнения политик.

Строит три панели: пропускная способность, хвостовая задержка p95 и доля
запросов, нарушивших SLO. Хвост и SLO показаны намеренно: средняя
задержка скрывает именно те случаи, ради которых маршрутизация и нужна —
редкие, но долгие запросы, застрявшие за чужой вычислительной задачей.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import matplotlib.pyplot as plt

BASE_DIR = Path(__file__).resolve().parent.parent
RESULTS_PATH = BASE_DIR / "data" / "policy_comparison.json"
FIGURES_DIR = BASE_DIR / "figures"

LABELS = {
    "least_conn": "Least-connections",
    "static_rule": "Экспертное правило",
    "model": "Только модель",
    "hybrid_1": "Гибрид (slack=1)",
    "hybrid_2": "Гибрид (slack=2)",
    "oracle": "Оракул (верхняя граница)",
}
COLORS = {
    "least_conn": "#1976D2",
    "static_rule": "#F57C00",
    "model": "#C62828",
    "hybrid_1": "#66BB6A",
    "hybrid_2": "#2E7D32",
    "oracle": "#7B1FA2",
}


def main() -> None:
    FIGURES_DIR.mkdir(exist_ok=True)
    results = json.loads(RESULTS_PATH.read_text(encoding="utf-8"))
    policies = [p for p in LABELS if p in results]
    levels = sorted({int(c) for p in policies for c in results[p]})

    fig, axes = plt.subplots(1, 3, figsize=(18, 5.5))
    metrics = [
        ("rps_mean", "Пропускная способность", "запросов/с", False),
        ("p95_mean", "Хвостовая задержка p95", "мс", True),
        ("slo_mean", "Нарушения SLO (>500 мс)", "% запросов", True),
    ]

    width = 0.13
    for ax, (key, title, ylabel, lower_better) in zip(axes, metrics):
        for i, policy in enumerate(policies):
            values = [results[policy][str(c)][key] for c in levels]
            positions = [x + (i - len(policies) / 2) * width for x in range(len(levels))]
            errs = ([results[policy][str(c)].get("rps_std", 0) for c in levels]
                    if key == "rps_mean" else None)
            ax.bar(positions, values, width, label=LABELS[policy],
                   color=COLORS[policy], yerr=errs, capsize=3)
        ax.set_title(title + ("  (меньше лучше)" if lower_better else "  (больше лучше)"))
        ax.set_xticks(range(len(levels)))
        ax.set_xticklabels([f"конкурентность {c}" for c in levels])
        ax.set_ylabel(ylabel)
        ax.grid(axis="y", alpha=0.3)

    axes[0].legend(fontsize=8, loc="upper left")
    plt.suptitle("Сравнение политик маршрутизации на смешанном потоке запросов",
                 fontsize=13)
    plt.tight_layout()
    out = FIGURES_DIR / "policy_comparison.png"
    plt.savefig(out, dpi=150)
    print(f"Сохранено: {out}")


if __name__ == "__main__":
    main()
