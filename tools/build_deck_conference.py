#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Презентация доклада на основе официального шаблона конференции.

Оформление не воспроизводится вручную, а берётся прямо из шаблона
`Регламент и требования/!4_Шаблон презентации.pptx`: из него оставлены
восемь подходящих слайдов, а их содержимое заменено. Так гарантируется,
что шрифты, цвета, фоны и расположение блоков совпадают с требуемыми, —
нарисовать это заново с той же точностью невозможно.

Состав повторяет пример конференции (`!2_Пример презентации.pdf`):
титул, проблема, суть находки, метод, результаты, выводы, благодарность.

Текст заменяется с сохранением начертания первой строки каждого блока:
шаблон задаёт кегль и цвет, а подставляется только содержание.
"""
from __future__ import annotations

import copy
import sys
from pathlib import Path

from pptx import Presentation
from pptx.util import Emu, Inches, Pt

BASE = Path(__file__).resolve().parent.parent
TEMPLATE = BASE / "Регламент и требования" / "!4_Шаблон презентации.pptx"
FIGURES = BASE / "figures"
OUT = BASE / "docs" / "Презентация Русский инженер Синявский СД.pptx"

CONFERENCE = ("Всероссийская научно-практическая конференция\n"
              "«Русский инженер»")

# Номера слайдов шаблона (с единицы), которые остаются, и их назначение.
KEEP = [1, 5, 6, 9, 10, 11, 19, 22]


def drop_slides(prs: Presentation, keep: list[int]) -> None:
    """Оставляет в презентации только указанные слайды шаблона."""
    ids = prs.slides._sldIdLst
    entries = list(ids)
    for i in range(len(entries) - 1, -1, -1):
        if (i + 1) not in keep:
            prs.part.drop_rel(entries[i].rId)
            ids.remove(entries[i])


def by_id(slide, shape_id: int):
    for sh in slide.shapes:
        if sh.shape_id == shape_id:
            return sh
    raise KeyError(f"нет фигуры {shape_id}")


def set_text(shape, lines: list[str], sizes: list[float] | None = None) -> None:
    """
    Заменяет текст, сохраняя оформление первой строки.

    Шаблон задаёт шрифт, цвет и кегль; здесь подставляется только
    содержание, поэтому вид блока не меняется.
    """
    tf = shape.text_frame
    first = tf.paragraphs[0]
    template_run = first.runs[0] if first.runs else None

    for p in list(tf.paragraphs)[1:]:
        p._p.getparent().remove(p._p)
    for r in list(first.runs)[1:]:
        r._r.getparent().remove(r._r)

    if template_run is None:
        first.add_run()
        template_run = first.runs[0]

    template_run.text = lines[0]
    if sizes:
        template_run.font.size = Pt(sizes[0])
    for i, line in enumerate(lines[1:], 1):
        p = copy.deepcopy(first._p)
        first._p.getparent().append(p)
        para = tf.paragraphs[-1]
        para.runs[0].text = line
        if sizes and i < len(sizes):
            para.runs[0].font.size = Pt(sizes[i])


def place(shape, left: float, top: float, width: float,
          height: float) -> None:
    """
    Переставляет и растягивает блок шаблона.

    Блоки шаблона рассчитаны на короткие подписи, а содержательный текст
    доклада длиннее; без подгонки он вылезает за пределы панели.
    """
    shape.left, shape.top = Inches(left), Inches(top)
    shape.width, shape.height = Inches(width), Inches(height)


def remove(shape) -> None:
    shape._element.getparent().remove(shape._element)


def put_image(slide, path: Path, left: float, top: float,
              width: float, height: float):
    """Вставляет рисунок, вписывая его в область с сохранением пропорций."""
    from PIL import Image

    with Image.open(path) as im:
        ratio = im.width / im.height
    if width / height > ratio:
        w, h = height * ratio, height
    else:
        w, h = width, width / ratio
    return slide.shapes.add_picture(
        str(path), Inches(left + (width - w) / 2),
        Inches(top + (height - h) / 2), Inches(w), Inches(h))


def build() -> Presentation:
    prs = Presentation(str(TEMPLATE))
    drop_slides(prs, KEEP)
    s = list(prs.slides)

    # 1. Титул -------------------------------------------------------------
    set_text(by_id(s[0], 84), [
        "Адаптивная маршрутизация HTTP-запросов",
        "между синхронным и асинхронным веб-движками",
        "на основе классификатора нагрузки"], sizes=[34, 34, 34])
    set_text(by_id(s[0], 85), ["Синявский С. Д., МГТУ им. Н.Э. Баумана"])
    set_text(by_id(s[0], 87), CONFERENCE.split("\n"))
    set_text(by_id(s[0], 88), ["Москва, 2026"])

    # 2. Проблема ----------------------------------------------------------
    set_text(by_id(s[1], 140), ["Проблема"])
    place(by_id(s[1], 140), 0.34, 1.55, 3.85, 0.9)
    set_text(by_id(s[1], 142), [
        "Асинхронный движок обслуживает все соединения одним циклом "
        "событий. Вычисление выполняется неделимо и на всё своё время "
        "останавливает обработку остальных запросов.",
        "",
        "Измерено: один и тот же запрос задерживает соседние обращения на "
        "9 мс в потоковом сервере и на 125 мс в цикле событий.",
        "",
        "Движки расходятся не в скорости обработки, а в том, сколько вреда "
        "запрос причиняет соседям."], sizes=[14, 7, 14, 7, 14])
    place(by_id(s[1], 142), 0.34, 2.70, 3.85, 4.30)
    remove(by_id(s[1], 136))
    put_image(s[1], FIGURES / "blocking_by_engine.png", 4.70, 1.70, 8.35, 3.90)
    _caption(s[1], "Рис. 1. Блокировка движка в зависимости от параметра "
             "запроса", 4.70, 5.75)

    # 3. Ключевая находка --------------------------------------------------
    set_text(by_id(s[2], 150), ["Стоимость запроса", "не определяется", "маршрутом"])
    place(by_id(s[2], 150), 0.41, 0.62, 4.00, 1.40)
    set_text(by_id(s[2], 151), [
        "/api/search?limit=10 — 10 мс",
        "то же при limit=600 — 640 мс",
        "",
        "Разброс внутри одного маршрута — до 61 раза. Весовые настройки "
        "задаются на маршрут и выразить это не могут.",
        "",
        "Но стоимость предсказуема по признакам, видимым прокси до "
        "обработки: ошибка падает с 80 до 3,4 %."],
        sizes=[13, 13, 7, 13, 7, 13])
    place(by_id(s[2], 151), 0.41, 2.25, 3.85, 4.70)
    remove(by_id(s[2], 152))
    put_image(s[2], FIGURES / "estimator_accuracy.png", 4.70, 1.95, 8.35, 3.60)
    _caption(s[2], "Рис. 2. Ошибка оценки стоимости на не встречавшихся "
             "значениях параметра", 4.70, 5.70)

    # 4. Метод -------------------------------------------------------------
    set_text(by_id(s[3], 190), ["МЕТОД"])
    place(by_id(s[3], 190), 0.30, 0.70, 3.60, 0.55)
    set_text(by_id(s[3], 189), [
        "Классификатор оценивает, на сколько запрос заблокирует цикл "
        "событий, и допускает его туда только ниже порога.",
        "",
        "Среди допустимых движков выбирается менее занятый."],
        sizes=[14, 7, 14])
    place(by_id(s[3], 189), 0.30, 1.50, 3.65, 3.50)
    remove(by_id(s[3], 188))
    put_image(s[3], FIGURES / "architecture.png", 4.45, 0.90, 8.65, 5.70)

    # 5. Результаты --------------------------------------------------------
    set_text(by_id(s[4], 196), ["РЕЗУЛЬТАТЫ"])
    place(by_id(s[4], 196), 9.72, 2.20, 3.45, 0.70)
    set_text(by_id(s[4], 197), [
        "Прирост 18–46 % к лучшему одиночному движку при конкурентности "
        "от 16 до 128.",
        "",
        "Доля запросов с ухудшенным откликом падает с 89 до 5–7 %.",
        "",
        "На однородном потоке выигрыша нет."], sizes=[13, 7, 13, 7, 13])
    place(by_id(s[4], 197), 9.72, 3.15, 3.40, 3.60)
    remove(by_id(s[4], 198))
    put_image(s[4], FIGURES / "budget_frontier.png", 0.35, 1.30, 8.80, 4.90)
    _caption(s[4], "Рис. 3. Достижимая граница «пропускная способность — "
             "хвост задержек»", 0.35, 6.45)

    # 6. Где необходимо обучение -------------------------------------------
    set_text(by_id(s[5], 208), ["ГДЕ НЕОБХОДИМО ОБУЧЕНИЕ"])
    place(by_id(s[5], 208), 0.76, 0.50, 6.00, 0.45)
    set_text(by_id(s[5], 206), ["Перекрёстный эксперимент"])
    place(by_id(s[5], 206), 0.76, 5.12, 6.60, 0.62)
    set_text(by_id(s[5], 207), [
        "Результат определяется только тем, какой оценкой взвешена "
        "очередь; чем принято решение о допуске — не влияет. Допуску "
        "достаточно верного порядка запросов, взвешиванию нужна верная "
        "абсолютная величина."], sizes=[14])
    place(by_id(s[5], 207), 0.76, 5.85, 11.80, 1.30)
    remove(by_id(s[5], 209))
    put_image(s[5], FIGURES / "where_learning_matters.png", 0.55, 1.05, 12.2, 3.85)

    # 7. Выводы ------------------------------------------------------------
    set_text(by_id(s[6], 379), ["ВЫВОДЫ И ГРАНИЦЫ ПРИМЕНИМОСТИ"])
    for title_id, body_id, title, body in (
        (376, 377, "МЕХАНИЗМ",
         ["Движки различаются блокировкой,",
          "а не скоростью обработки:",
          "9 мс против 125 мс."]),
        (381, 382, "ОЦЕНКА",
         ["Нагрузка предсказуема по признакам",
          "запроса, но не по маршруту:",
          "80 % ошибки против 3,4 %."]),
        (383, 384, "ЦЕЛЕСООБРАЗНОСТЬ",
         ["Обучение нужно при взвешивании",
          "очередей, но не при выборе движка;",
          "решение стоит 1,6 мкс против 166."]),
    ):
        set_text(by_id(s[6], title_id), [title])
        set_text(by_id(s[6], body_id), body, sizes=[13, 13, 13])
        place(by_id(s[6], body_id), by_id(s[6], body_id).left / 914400,
              2.19, 3.55, 1.40)
    set_text(by_id(s[6], 385), [
        "ГРАНИЦА ПРИМЕНИМОСТИ",
        "На однородном потоке подход вреден, а при деградации самого "
        "охраняемого движка преимущество утрачивается полностью."],
        sizes=[14, 14])
    place(by_id(s[6], 385), 0.57, 5.20, 11.80, 1.30)
    for gid in (386, 389, 392):
        remove(by_id(s[6], gid))

    # 8. Спасибо -----------------------------------------------------------
    set_text(by_id(s[7], 450), ["Спасибо за внимание"])
    set_text(by_id(s[7], 451), ["Синявский С. Д.  ·  ssd-tula@mail.ru"])
    return prs


def _caption(slide, text: str, left: float, top: float) -> None:
    from pptx.dml.color import RGBColor
    box = slide.shapes.add_textbox(Inches(left), Inches(top), Inches(8.4),
                                   Inches(0.34))
    tf = box.text_frame
    tf.word_wrap = True
    r = tf.paragraphs[0].add_run()
    r.text = text
    r.font.size = Pt(11)
    r.font.name = "Arial"
    r.font.color.rgb = RGBColor(0x43, 0x43, 0x43)


if __name__ == "__main__":
    prs = build()
    OUT.parent.mkdir(exist_ok=True)
    prs.save(str(OUT))
    print(f"Слайдов: {len(prs.slides)}")
    print(f"Сохранено: {OUT.name}")
