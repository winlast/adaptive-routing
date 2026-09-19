"""
Оценка числа страниц документа по его разметке.

Word в этой среде недоступен, поэтому вёрстка моделируется: ширина
строки измеряется шрифтом Liberation Serif, метрически совместимым с
Times New Roman, текст переносится по словам, высота считается по
межстрочному интервалу и отступам между абзацами.

Оценка приблизительная и служит для того, чтобы не выйти за лимит, а не
для точного совпадения с Word. Итоговую вёрстку всё равно проверяет
человек, открыв файл.
"""
from __future__ import annotations

import re
import sys
import zipfile
from pathlib import Path

from PIL import ImageFont

FONT_DIR = "/usr/share/fonts/truetype/liberation"
TWIP = 20.0  # твипов в пункте


def load_fonts(size_pt: float, scale: int = 8):
    def f(name):
        return ImageFont.truetype(f"{FONT_DIR}/{name}", int(size_pt * scale))
    return {
        (False, False): f("LiberationSerif-Regular.ttf"),
        (True, False): f("LiberationSerif-Bold.ttf"),
        (False, True): f("LiberationSerif-Italic.ttf"),
        (True, True): f("LiberationSerif-BoldItalic.ttf"),
    }, scale


def parse(path: Path):
    with zipfile.ZipFile(path) as z:
        xml = z.read("word/document.xml").decode("utf-8")

    sect = re.search(r"<w:sectPr.*?</w:sectPr>", xml, re.S).group(0)
    pg = re.search(r'<w:pgSz w:w="(\d+)" w:h="(\d+)"', sect)
    mar = re.search(r'<w:pgMar w:top="(\d+)" w:right="(\d+)" '
                    r'w:bottom="(\d+)" w:left="(\d+)"', sect)
    w, h = int(pg.group(1)), int(pg.group(2))
    top, right, bottom, left = (int(mar.group(i)) for i in (1, 2, 3, 4))
    width_pt = (w - left - right) / TWIP
    height_pt = (h - top - bottom) / TWIP

    body = xml[:xml.index("<w:sectPr")]
    # Верхнеуровневые блоки: абзацы и таблицы. Абзацы внутри таблиц
    # нельзя считать наравне с обычными — строка таблицы занимает
    # столько, сколько её самая высокая ячейка, а не сумму ячеек.
    blocks = re.findall(r"<w:tbl>.*?</w:tbl>|<w:p[ >](?:(?!<w:p[ >]).)*?</w:p>",
                        body, re.S)
    return blocks, width_pt, height_pt


def paragraph_height(para: str, fonts, scale: int, width_pt: float) -> float:
    ppr = re.search(r"<w:pPr>.*?</w:pPr>", para, re.S)
    ppr = ppr.group(0) if ppr else ""

    def twip(attr, default=0.0):
        m = re.search(rf'w:{attr}="(\d+)"', ppr)
        return int(m.group(1)) / TWIP if m else default

    before = twip("before")
    after = twip("after")
    ind_first = twip("firstLine")
    ind_left = twip("left")
    hanging = twip("hanging")

    line_m = re.search(r'<w:line w:val="(\d+)"', ppr) or \
        re.search(r'w:line="(\d+)"', ppr)
    line_mult = int(line_m.group(1)) / 240.0 if line_m else 1.0

    # Куски текста с их начертанием.
    pieces = []
    for run in re.findall(r"<w:r[ >](?:(?!</w:r>).)*</w:r>", para, re.S):
        text = "".join(re.findall(r"<w:t[^>]*>(.*?)</w:t>", run, re.S))
        if not text:
            continue
        text = (text.replace("&amp;", "&").replace("&lt;", "<")
                .replace("&gt;", ">").replace("&quot;", '"'))
        sz = re.search(r'<w:sz w:val="(\d+)"', run)
        size = int(sz.group(1)) / 2 if sz else 12.0
        pieces.append((text, "<w:b/>" in run, "<w:i/>" in run, size))

    if not pieces:
        return before + after + 12.0 * 1.15 * line_mult

    max_size = max(p[3] for p in pieces)
    avail_first = width_pt - ind_left - (ind_first - hanging if hanging else ind_first)
    avail_rest = width_pt - ind_left

    # Перенос по словам с учётом начертания каждого куска.
    words = []
    for text, bold, italic, size in pieces:
        for w_ in re.split(r"(\s+)", text):
            if w_:
                words.append((w_, bold, italic, size))

    lines = 1
    cur = 0.0
    avail = avail_first
    for w_, bold, italic, size in words:
        font = fonts[(bold, italic)]
        adv = font.getlength(w_) / scale * (size / 12.0)
        if w_.isspace():
            cur += adv
            continue
        if cur + adv > avail and cur > 0:
            lines += 1
            cur = adv
            avail = avail_rest
        else:
            cur += adv

    line_h = max_size * 1.15 * line_mult
    return before + after + lines * line_h


def table_height(tbl: str, fonts, scale: int, width_pt: float) -> float:
    """Высота таблицы: сумма по строкам, в строке — самая высокая ячейка."""
    grid = [int(w) / TWIP for w in re.findall(r'<w:gridCol w:w="(\d+)"', tbl)]
    total = 0.0
    for row in re.findall(r"<w:tr[ >].*?</w:tr>", tbl, re.S):
        cells = re.findall(r"<w:tc>.*?</w:tc>", row, re.S)
        heights = []
        for j, cell in enumerate(cells):
            cell_w = grid[j] if j < len(grid) else width_pt / max(len(cells), 1)
            for para in re.findall(r"<w:p[ >].*?</w:p>", cell, re.S):
                heights.append(paragraph_height(para, fonts, scale, cell_w))
        total += max(heights) if heights else 0.0
    return total


def estimate(path: Path) -> tuple[float, int]:
    blocks, width_pt, height_pt = parse(path)
    fonts, scale = load_fonts(12.0)
    total = 0.0
    for b in blocks:
        if b.startswith("<w:tbl>"):
            total += table_height(b, fonts, scale, width_pt)
        else:
            total += paragraph_height(b, fonts, scale, width_pt)
    return total / height_pt, len(blocks)


if __name__ == "__main__":
    for arg in sys.argv[1:]:
        p = Path(arg)
        pages, n = estimate(p)
        print(f"{p.name}: примерно {pages:.2f} стр. ({n} абзацев)")
