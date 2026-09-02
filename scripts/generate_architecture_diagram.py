"""Генерирует схему архитектуры системы (для презентации)."""
from pathlib import Path
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch

FIGURES_DIR = Path(__file__).resolve().parent.parent / "figures"
FIGURES_DIR.mkdir(exist_ok=True)

fig, ax = plt.subplots(figsize=(12, 5))
ax.set_xlim(0, 12); ax.set_ylim(0, 5); ax.axis("off")

def box(x, y, w, h, text, color="#4C72B0"):
    ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0.1",
                 facecolor=color, edgecolor="black", alpha=0.85))
    ax.text(x + w/2, y + h/2, text, ha="center", va="center",
            fontsize=11, color="white", weight="bold")

def arrow(x1, y1, x2, y2, label=""):
    ax.add_patch(FancyArrowPatch((x1, y1), (x2, y2), arrowstyle="-|>",
                 mutation_scale=20, color="black"))
    if label:
        ax.text((x1+x2)/2, (y1+y2)/2 + 0.25, label, ha="center", fontsize=9)

box(0.3, 2, 1.8, 1, "Клиент\n(hey)", "#55A868")
box(3.5, 2, 2.5, 1, "Gateway\nAdaptiveRouter", "#DD8452")
box(7.5, 3.3, 2, 1, "Flask\n:5000", "#4C72B0")
box(7.5, 0.7, 2, 1, "FastAPI\n:8000", "#4C72B0")

arrow(2.1, 2.5, 3.5, 2.5, "POST /route")
arrow(6.0, 2.9, 7.5, 3.7, "load>50?\n→ Flask")
arrow(6.0, 2.1, 7.5, 1.2, "→ FastAPI")

ax.text(4.75, 1.6, "load, io_intensity,\ncpu_usage → PyTorch → P(FastAPI)",
        ha="center", fontsize=8, style="italic")

plt.title("Архитектура adaptive-шлюза", fontsize=13)
plt.tight_layout()
plt.savefig(FIGURES_DIR / "architecture_diagram.png", dpi=150)
print(f"Сохранено: {FIGURES_DIR / 'architecture_diagram.png'}")