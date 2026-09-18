"""
Схема работы стенда.

Рисунок изображает то, что система действительно делает, и числа на нём
взяты из `data/cost_grid.json`, а не подобраны для наглядности.

Существенно здесь одно: решение принимается по **конкретному запросу**, а
не по его маршруту. Поэтому на схеме показаны два обращения по одному и
тому же адресу, которые уходят в разные движки, — это и есть содержание
работы, и прежняя версия схемы изображала вместо этого правило,
привязанное к маршруту, которого в системе нет.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch

BASE_DIR = Path(__file__).resolve().parent.parent
FIGURES_DIR = BASE_DIR / "figures"
GRID_PATH = BASE_DIR / "data" / "cost_grid.json"

INK = "#263238"
GATEWAY = "#E3F2FD"
GATEWAY_EDGE = "#1565C0"
SYNC = "#E8F5E9"
SYNC_EDGE = "#2E7D32"
ASYNC = "#FFEBEE"
ASYNC_EDGE = "#C62828"
NEUTRAL = "#ECEFF1"
NEUTRAL_EDGE = "#78909C"


def box(ax, x, y, w, h, text, face, edge, size=10, weight="normal",
        align="center"):
    ax.add_patch(FancyBboxPatch(
        (x, y), w, h, boxstyle="round,pad=0.12,rounding_size=0.18",
        facecolor=face, edgecolor=edge, linewidth=1.6))
    tx = x + w / 2 if align == "center" else x + 0.25
    ax.text(tx, y + h / 2, text, ha=align if align != "center" else "center",
            va="center", fontsize=size, color=INK, fontweight=weight,
            linespacing=1.45)


def arrow(ax, p1, p2, text=None, color=INK, style="-|>", offset=(0, 0),
          size=9, dashed=False):
    ax.add_patch(FancyArrowPatch(
        p1, p2, arrowstyle=style, mutation_scale=16, linewidth=1.5,
        color=color, linestyle="--" if dashed else "-",
        shrinkA=2, shrinkB=2))
    if text:
        ax.text((p1[0] + p2[0]) / 2 + offset[0],
                (p1[1] + p2[1]) / 2 + offset[1], text, ha="center",
                va="center", fontsize=size, color=color,
                bbox=dict(boxstyle="round,pad=0.22", facecolor="white",
                          edgecolor="none", alpha=0.92))


def main() -> None:
    FIGURES_DIR.mkdir(exist_ok=True)
    grid = json.loads(GRID_PATH.read_text(encoding="utf-8"))

    def block(endpoint: str, param: int, worker: str) -> float:
        for row in grid[endpoint][worker]:
            if row[0] == param:
                return row[2]
        raise KeyError((endpoint, param, worker))

    light = block("/api/search", 10, "async")
    heavy_async = block("/api/search", 600, "async")
    heavy_sync = block("/api/search", 600, "sync")
    auth_sync = block("/api/auth/verify", 1, "sync")
    auth_async = block("/api/auth/verify", 1, "async")

    fig, ax = plt.subplots(figsize=(15.5, 9.2))
    ax.set_xlim(0, 15.5)
    ax.set_ylim(0, 9.2)
    ax.axis("off")

    # --- клиент -------------------------------------------------------
    box(ax, 0.3, 6.0, 2.5, 1.5,
        "Клиент\n\nPOST /route\n{маршрут, параметр}", NEUTRAL, NEUTRAL_EDGE, 10)

    # --- шлюз ---------------------------------------------------------
    box(ax, 3.4, 4.0, 5.9, 4.6, "", GATEWAY, GATEWAY_EDGE)
    ax.text(6.35, 8.28, "Шлюз  (порт 8300)", ha="center", va="center",
            fontsize=12, fontweight="bold", color=GATEWAY_EDGE)
    ax.text(3.65, 7.82,
            "видит только то, что видит обратный прокси:\n"
            "маршрут, значение параметра, размер тела,\n"
            "собственные счётчики незавершённых запросов",
            ha="left", va="center", fontsize=8.5, color=INK, linespacing=1.4)

    box(ax, 3.75, 6.15, 5.2, 1.0,
        "1. Оценка нагрузки запроса\n"
        "степенной закон или нейронная сеть →\n"
        "на сколько миллисекунд запрос застопорит event loop",
        "white", GATEWAY_EDGE, 9)

    box(ax, 3.75, 5.05, 5.2, 0.85,
        "2. Допуск: предсказанная блокировка ≤ порога?\n"
        "да → допустимы оба движка       нет → только синхронный",
        "white", GATEWAY_EDGE, 9)

    box(ax, 3.75, 4.1, 5.2, 0.72,
        "3. Из допустимых — тот, у кого меньше занятость очереди\n"
        "вместе с этим запросом",
        "white", GATEWAY_EDGE, 9)

    arrow(ax, (2.8, 6.75), (3.4, 6.65))
    arrow(ax, (6.35, 6.15), (6.35, 5.9))
    arrow(ax, (6.35, 5.05), (6.35, 4.8))

    # --- движки -------------------------------------------------------
    box(ax, 3.3, 1.3, 2.8, 2.0,
        "Синхронный движок\nFlask, поток на запрос\nпорт 8201\n\n"
        "вычисление не\nостанавливает соседей:\nпотоки переключаются,\n"
        "операции на C\nосвобождают GIL",
        SYNC, SYNC_EDGE, 8.5)

    box(ax, 6.6, 1.3, 2.8, 2.0,
        "Асинхронный движок\nFastAPI, один event loop\nпорт 8202\n\n"
        "ожидание почти\nбесплатно, но вычисление\nостанавливает обработку\n"
        "всех остальных\nзапросов целиком",
        ASYNC, ASYNC_EDGE, 8.5)

    arrow(ax, (4.7, 4.1), (4.7, 3.3), color=SYNC_EDGE)
    arrow(ax, (8.0, 4.1), (8.0, 3.3), color=ASYNC_EDGE)

    box(ax, 4.55, 0.25, 3.6, 0.7,
        "Внешний сервис (порт 8100) и SQLite\n"
        "ввод-вывод настоящий: сокет и ожидание ответа",
        NEUTRAL, NEUTRAL_EDGE, 8.5)
    arrow(ax, (4.7, 1.3), (5.6, 0.95), color=NEUTRAL_EDGE)
    arrow(ax, (8.0, 1.3), (7.1, 0.95), color=NEUTRAL_EDGE)

    # Пояснение в свободном поле слева: что в стенде настоящее.
    ax.text(0.35, 5.35,
            "Что здесь настоящее",
            ha="left", va="center", fontsize=10, fontweight="bold",
            color=INK)
    ax.text(0.35, 5.02,
            "• нагрузка — настоящие операции:\n"
            "  PBKDF2, кодек JPEG, сортировка,\n"
            "  SQLite, обращение по сети;\n"
            "  ни одного sleep вместо работы\n\n"
            "• вычисление в асинхронном движке\n"
            "  выполняется прямо в event loop,\n"
            "  без выноса в executor — это не\n"
            "  недосмотр, а исследуемый\n"
            "  режим отказа\n\n"
            "• цель обучения — измеренная\n"
            "  секундомером задержка, а не\n"
            "  придуманная метка класса",
            ha="left", va="top", fontsize=8.3, color=INK,
            linespacing=1.55)

    # --- что решает: один маршрут, разные движки ----------------------
    box(ax, 9.9, 4.6, 5.3, 4.0, "", "white", GATEWAY_EDGE)
    ax.text(12.55, 8.28, "Почему решает не маршрут, а запрос",
            ha="center", va="center", fontsize=11, fontweight="bold",
            color=GATEWAY_EDGE)
    ax.text(12.55, 7.85,
            "Оба обращения идут по одному адресу /api/search.\n"
            "Блокировка event loop измерена профилированием:",
            ha="center", va="center", fontsize=8.8, color=INK,
            linespacing=1.4)

    box(ax, 10.2, 6.55, 4.7, 0.80,
        f"?limit=10   →   {light:.0f} мс блокировки\n"
        "допустим в event loop  →  может уйти в асинхронный",
        ASYNC, ASYNC_EDGE, 9)
    box(ax, 10.2, 5.45, 4.7, 0.80,
        f"?limit=600  →  {heavy_async:.0f} мс блокировки\n"
        "остановит всех  →  только синхронный движок",
        SYNC, SYNC_EDGE, 9)
    ax.text(12.55, 4.95,
            f"Разница по одному адресу — в {heavy_async / light:.0f} раз.\n"
            "Таблица «маршрут → стоимость» описать её не может.",
            ha="center", va="center", fontsize=8.8, color=INK,
            linespacing=1.4)

    # --- измеренная асимметрия движков --------------------------------
    box(ax, 9.9, 0.25, 5.3, 3.95, "", "white", NEUTRAL_EDGE)
    ax.text(12.55, 3.92, "Измеренная асимметрия движков",
            ha="center", va="center", fontsize=11, fontweight="bold",
            color=INK)
    ax.text(12.55, 3.52,
            "на сколько запрос делает движок\nнедоступным для остальных, мс",
            ha="center", va="center", fontsize=8.5, color=INK,
            linespacing=1.4)

    rows = [
        ("/api/auth/verify", auth_sync, auth_async),
        ("/api/search?limit=600", heavy_sync, heavy_async),
    ]
    ax.text(10.35, 3.02, "запрос", fontsize=8.5, color=INK, va="center")
    ax.text(13.4, 3.02, "синхр.", fontsize=8.5, color=SYNC_EDGE,
            va="center", ha="center", fontweight="bold")
    ax.text(14.5, 3.02, "асинхр.", fontsize=8.5, color=ASYNC_EDGE,
            va="center", ha="center", fontweight="bold")
    ax.plot([10.3, 14.85], [2.85, 2.85], color=NEUTRAL_EDGE, linewidth=0.9)
    for i, (name, a, b) in enumerate(rows):
        y = 2.55 - i * 0.45
        ax.text(10.35, y, name, fontsize=8.5, color=INK, va="center")
        ax.text(13.4, y, f"{a:.0f}", fontsize=9.5, color=SYNC_EDGE,
                va="center", ha="center", fontweight="bold")
        ax.text(14.5, y, f"{b:.0f}", fontsize=9.5, color=ASYNC_EDGE,
                va="center", ha="center", fontweight="bold")

    ax.text(12.55, 1.25,
            "Собственное время обработки от движка почти\n"
            "не зависит: 128 против 124 мс и 667 против 633 мс.\n"
            "Расходятся движки в том, сколько вреда запрос\n"
            "причиняет соседям — и для второго запроса эта\n"
            "разница мала, потому что сортировка в Python\n"
            "удерживает GIL и в потоке тоже.",
            ha="center", va="center", fontsize=8.3, color=INK,
            linespacing=1.5)

    fig.suptitle("Маршрутизация запроса между синхронным и асинхронным "
                 "движком по оценке его нагрузки", fontsize=13.5, y=0.975)
    fig.tight_layout(rect=(0, 0, 1, 0.955))
    out = FIGURES_DIR / "architecture.png"
    fig.savefig(out, dpi=150)
    print(f"Сохранено: {out}")


if __name__ == "__main__":
    main()
