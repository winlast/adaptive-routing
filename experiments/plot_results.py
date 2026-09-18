"""
Графики по результатам эксперимента.

Строятся три рисунка, отвечающие трём утверждениям работы.

  1. Блокировка движка. Показывает, почему маршрутизация между
     синхронным и асинхронным движком вообще имеет смысл: одна и та же
     работа занимает поток на считанные миллисекунды и останавливает
     event loop на сотни.
  2. Точность оценки стоимости. Показывает, что стоимость запроса не
     определяется его маршрутом и насколько её удаётся восстановить по
     наблюдаемым признакам.
  3. Достижимая граница. Показывает, во что точность оценки превращается
     на работающей системе: каждая точка — рабочий режим, кривая —
     компромисс между пропускной способностью и хвостом задержек.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

from core.workload import ENDPOINTS

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
FIGURES_DIR = BASE_DIR / "figures"

SOURCES = {
    "endpoint": ("среднее по маршруту", "#C62828", "o"),
    "linear": ("линейная поправка", "#F57C00", "s"),
    "power": ("степенной закон", "#1976D2", "^"),
    "neural": ("нейронная сеть", "#2E7D32", "D"),
    "measured": ("точная таблица (граница)", "#6A1B9A", "*"),
}


def plot_blocking() -> None:
    detail = json.loads((DATA_DIR / "cost_grid_detail.json").read_text(
        encoding="utf-8"))
    shown = ["/api/report/generate", "/api/search", "/api/image/thumbnail",
             "/api/feed"]
    fig, axes = plt.subplots(1, len(shown), figsize=(4.2 * len(shown), 4.2))
    for ax, endpoint in zip(axes, shown):
        spec = ENDPOINTS[endpoint]
        for worker, color in (("sync", "#1976D2"), ("async", "#C62828")):
            rows = detail[endpoint][worker]
            ax.plot([r["param"] for r in rows], [r["block_ms"] for r in rows],
                    marker="o", color=color,
                    label="синхронный (поток)" if worker == "sync"
                    else "асинхронный (event loop)")
        ax.set_title(endpoint, fontsize=10)
        ax.set_xlabel(f"параметр запроса ({spec.param_name})")
        ax.set_ylabel("блокировка движка, мс")
        ax.grid(alpha=0.3)
    axes[0].legend(fontsize=8)
    fig.suptitle("Насколько запрос делает движок недоступным для остальных",
                 fontsize=13)
    fig.tight_layout()
    out = FIGURES_DIR / "blocking_by_engine.png"
    fig.savefig(out, dpi=150)
    print(f"Сохранено: {out}")


def plot_accuracy() -> None:
    data = json.loads((DATA_DIR / "estimator_accuracy.json").read_text(
        encoding="utf-8"))
    names = list(data["overall"])
    endpoints = list(data["by_endpoint"])

    fig, axes = plt.subplots(1, 2, figsize=(15, 5))
    colors = ["#C62828", "#F57C00", "#1976D2", "#2E7D32"]

    axes[0].bar(range(len(names)), [data["overall"][n] for n in names],
                color=colors[:len(names)])
    axes[0].set_xticks(range(len(names)))
    axes[0].set_xticklabels(names, rotation=15, fontsize=9)
    axes[0].set_ylabel("медианная ошибка, %")
    axes[0].set_title("Ошибка оценки стоимости запроса\n"
                      "на не встречавшихся значениях параметра")
    axes[0].grid(axis="y", alpha=0.3)
    for i, n in enumerate(names):
        axes[0].text(i, data["overall"][n], f"{data['overall'][n]:.1f}%",
                     ha="center", va="bottom", fontsize=9)

    width = 0.8 / len(names)
    for i, n in enumerate(names):
        values = [data["by_endpoint"][e][n] for e in endpoints]
        axes[1].bar([x + (i - len(names) / 2) * width
                     for x in range(len(endpoints))],
                    values, width, label=n, color=colors[i % len(colors)])
    axes[1].set_xticks(range(len(endpoints)))
    axes[1].set_xticklabels([e.replace("/api/", "") for e in endpoints],
                            rotation=20, fontsize=9)
    axes[1].set_yscale("log")
    axes[1].set_ylabel("медианная ошибка, % (логарифм)")
    axes[1].set_title("По маршрутам: обучение выигрывает там,\n"
                      "где зависимость не пропорциональна параметру")
    axes[1].legend(fontsize=8)
    axes[1].grid(axis="y", alpha=0.3)

    fig.tight_layout()
    out = FIGURES_DIR / "estimator_accuracy.png"
    fig.savefig(out, dpi=150)
    print(f"Сохранено: {out}")


def plot_frontier() -> None:
    data = json.loads((DATA_DIR / "budget_frontier.json").read_text(
        encoding="utf-8"))
    fig, axes = plt.subplots(1, 2, figsize=(15, 5.6))

    for metric, ax, ylabel, title in (
        ("light_over_budget_pct", axes[0], "доля лёгких запросов дольше 200 мс, %",
         "Компромисс: пропускная способность против\nстрадания лёгких запросов"),
        ("light_p95", axes[1], "95-й процентиль лёгких запросов, мс",
         "То же по хвостовой задержке"),
    ):
        for source, (label, color, marker) in SOURCES.items():
            points = sorted(
                ((v["rps"], v[metric]) for k, v in data.items()
                 if k.startswith(f"budget_{source}_")),
                key=lambda t: t[0])
            if not points:
                continue
            ax.plot([p[0] for p in points], [p[1] for p in points],
                    marker=marker, color=color, label=label, linewidth=1.8)
        for name, color, marker in (("all_sync", "#455A64", "v"),
                                    ("all_async", "#B71C1C", "X"),
                                    ("least_conn", "#000000", "P")):
            if name in data:
                ax.scatter([data[name]["rps"]], [data[name][metric]],
                           s=140, color=color, marker=marker, zorder=5,
                           label={"all_sync": "весь трафик в синхронный",
                                  "all_async": "весь трафик в асинхронный",
                                  "least_conn": "least-connections"}[name])
        ax.set_xlabel("пропускная способность, запросов/с  (больше лучше)")
        ax.set_ylabel(ylabel + "  (меньше лучше)")
        ax.set_title(title)
        ax.grid(alpha=0.3)
    axes[0].legend(fontsize=8, loc="upper left")
    fig.tight_layout()
    out = FIGURES_DIR / "budget_frontier.png"
    fig.savefig(out, dpi=150)
    print(f"Сохранено: {out}")


def main() -> None:
    FIGURES_DIR.mkdir(exist_ok=True)
    plot_blocking()
    if (DATA_DIR / "estimator_accuracy.json").exists():
        plot_accuracy()
    if (DATA_DIR / "budget_frontier.json").exists():
        plot_frontier()


if __name__ == "__main__":
    main()
