"""
Сборка документов с сохранением разметки оригинала.

Разметка не переизобретается: абзацы собираются ровно теми же
свойствами, что в исходных файлах, — Times New Roman 12 пт,
межстрочный интервал 1,5, отступ первой строки 709 твипов, выключка по
ширине, висячий отступ 567 у источников. Из оригинала дословно
переносится только шапка: УДК, заголовок, сведения об авторе и
организации. Аннотация и ключевые слова собираются заново вместе с
остальным текстом.
"""
from __future__ import annotations

import re
import shutil
import zipfile
from pathlib import Path

SZ = '<w:sz w:val="24"/><w:szCs w:val="24"/>'

PPR = {
    # начало раздела: отбивка сверху, отступ первой строки, по ширине
    "head": '<w:pPr><w:spacing w:before="240" w:line="360" w:lineRule="auto"/>'
            '<w:ind w:firstLine="709"/><w:jc w:val="both"/></w:pPr>',
    # продолжение раздела
    "cont": '<w:pPr><w:spacing w:line="360" w:lineRule="auto"/>'
            '<w:ind w:firstLine="709"/><w:jc w:val="both"/></w:pPr>',
    # пункт перечисления
    "bullet": '<w:pPr><w:spacing w:after="60" w:line="360" w:lineRule="auto"/>'
              '<w:ind w:left="360"/><w:jc w:val="both"/></w:pPr>',
    # подпись к таблице
    "caption": '<w:pPr><w:spacing w:before="240" w:line="360" '
               'w:lineRule="auto"/><w:ind w:firstLine="709"/>'
               '<w:jc w:val="both"/></w:pPr>',
    # аннотация и ключевые слова: без отступа первой строки
    "abstract": '<w:pPr><w:spacing w:before="240" w:line="360" '
                'w:lineRule="auto"/><w:jc w:val="both"/></w:pPr>',
    "refs_title": '<w:pPr><w:spacing w:after="150" w:line="360" '
                  'w:lineRule="auto"/></w:pPr>',
    "ref": '<w:pPr><w:spacing w:after="120" w:line="360" w:lineRule="auto"/>'
           '<w:ind w:left="567" w:hanging="567"/></w:pPr>',
    "cell": '<w:pPr><w:spacing w:line="360" w:lineRule="auto"/>'
            '<w:jc w:val="center"/></w:pPr>',
}


def esc(text: str) -> str:
    return (text.replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;"))


def run(text: str, bold: bool = False, sized: bool = True) -> str:
    rpr = ("<w:rPr>" + ("<w:b/><w:bCs/>" if bold else "")
           + (SZ if sized else "") + "</w:rPr>") if (bold or sized) else ""
    return (f"<w:r>{rpr}<w:t xml:space=\"preserve\">{esc(text)}</w:t></w:r>")


def para(kind: str, *parts, sized: bool = True) -> str:
    """parts — либо строка, либо (строка, True) для полужирного."""
    body = ""
    for part in parts:
        if isinstance(part, tuple):
            body += run(part[0], bold=part[1], sized=sized)
        else:
            body += run(part, sized=sized)
    return f"<w:p>{PPR[kind]}{body}</w:p>"


def reference(number: int, text: str) -> str:
    return (f"<w:p>{PPR['ref']}"
            f"<w:r><w:rPr>{SZ}</w:rPr><w:t>[{number}]</w:t></w:r>"
            f"<w:r><w:rPr>{SZ}</w:rPr><w:tab/>"
            f"<w:t xml:space=\"preserve\">{esc(text)}</w:t></w:r></w:p>")


def table(rows: list[list[str]], widths: list[int]) -> str:
    """Таблица с теми же границами и шириной колонок, что в оригинале."""
    borders = "".join(
        f'<w:{side} w:val="single" w:sz="4" w:space="0" w:color="auto"/>'
        for side in ("top", "left", "bottom", "right", "insideH", "insideV"))
    total = sum(widths)
    grid = "".join(f'<w:gridCol w:w="{w}"/>' for w in widths)
    out = [f'<w:tbl><w:tblPr><w:tblW w:w="{total}" w:type="dxa"/>'
           f'<w:tblBorders>{borders}</w:tblBorders>'
           f'<w:tblCellMar><w:left w:w="10" w:type="dxa"/>'
           f'<w:right w:w="10" w:type="dxa"/></w:tblCellMar>'
           f'</w:tblPr><w:tblGrid>{grid}</w:tblGrid>']
    for i, row in enumerate(rows):
        out.append("<w:tr>")
        for j, cell in enumerate(row):
            bold = "<w:rPr><w:b/><w:bCs/></w:rPr>" if i == 0 else ""
            out.append(
                f'<w:tc><w:tcPr><w:tcW w:w="{widths[j]}" w:type="dxa"/>'
                f'</w:tcPr><w:p>{PPR["cell"]}'
                f'<w:r>{bold}<w:t xml:space="preserve">{esc(cell)}</w:t>'
                f'</w:r></w:p></w:tc>')
        out.append("</w:tr>")
    out.append("</w:tbl>")
    return "".join(out)


def split_blocks(xml: str) -> tuple[list[str], str, str]:
    """Разбивает документ на голову, блоки тела и хвост."""
    start = xml.index("<w:body>") + len("<w:body>")
    end = xml.index("<w:sectPr")
    blocks = re.findall(r"<w:tbl>.*?</w:tbl>|<w:p[ >](?:(?!<w:p[ >]).)*?</w:p>",
                        xml[start:end], re.S)
    return blocks, xml[:start], xml[end:]


def rebuild(source: Path, target: Path, keep_head: int, body: list[str]) -> None:
    """
    Собирает новый документ: первые `keep_head` блоков оригинала
    (шапка и аннотация) остаются дословно, остальное заменяется.
    """
    with zipfile.ZipFile(source) as z:
        xml = z.read("word/document.xml").decode("utf-8")
        names = z.namelist()
        payload = {n: z.read(n) for n in names}

    blocks, head, tail = split_blocks(xml)
    new_xml = head + "".join(blocks[:keep_head]) + "".join(body) + tail
    payload["word/document.xml"] = new_xml.encode("utf-8")

    target.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as z:
        for name in names:
            z.writestr(name, payload[name])
