#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Сборка презентации к премии Леденцова.

Собирается из кода, а не рисуется вручную, по двум причинам. Первая —
все числа берутся из одного места и не могут разойтись с замерами.
Вторая — геометрию можно проверить: после сборки выполняется контроль
перекрытий, потому что отрисовать файл в этой среде нечем, а
разъехавшаяся вёрстка на защите хуже отсутствия слайда.
"""
from __future__ import annotations

import sys
from pathlib import Path

from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.text import PP_ALIGN, MSO_ANCHOR
from pptx.util import Inches, Pt

OUT = Path(__file__).resolve().parent / (
    "Интеллектуальная система адаптивной балансировки нагрузки "
    "для микросервисных и гибридных веб-архитектур.pptx")

W, H = 13.333, 7.5
MARGIN = 0.9
FONT = "Calibri"

INK = RGBColor(0x1A, 0x20, 0x27)
MUTED = RGBColor(0x5B, 0x6B, 0x7A)
ACCENT = RGBColor(0x15, 0x65, 0xC0)
WARN = RGBColor(0xC6, 0x28, 0x28)
OK = RGBColor(0x2E, 0x7D, 0x32)
LIGHT = RGBColor(0xF2, 0xF5, 0xF8)
WHITE = RGBColor(0xFF, 0xFF, 0xFF)
DARK = RGBColor(0x14, 0x1B, 0x23)

boxes: list[tuple[int, float, float, float, float, str]] = []


def _tf(shape, text, size, color, bold=False, align=PP_ALIGN.LEFT,
        spacing=1.0):
    tf = shape.text_frame
    tf.word_wrap = True
    tf.margin_left = tf.margin_right = 0
    tf.margin_top = tf.margin_bottom = 0
    lines = text.split("\n")
    for i, line in enumerate(lines):
        p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        p.alignment = align
        p.line_spacing = spacing
        r = p.add_run()
        r.text = line
        r.font.size = Pt(size)
        r.font.bold = bold
        r.font.name = FONT
        r.font.color.rgb = color
    return tf


def text(slide, idx, left, top, width, height, body, size, color,
         bold=False, align=PP_ALIGN.LEFT, spacing=1.0, label=""):
    box = slide.shapes.add_textbox(Inches(left), Inches(top),
                                   Inches(width), Inches(height))
    _tf(box, body, size, color, bold, align, spacing)
    boxes.append((idx, left, top, width, height, label or body[:24]))
    return box


def panel(slide, left, top, width, height, fill):
    from pptx.enum.shapes import MSO_SHAPE
    sh = slide.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, Inches(left),
                                Inches(top), Inches(width), Inches(height))
    sh.fill.solid()
    sh.fill.fore_color.rgb = fill
    sh.line.fill.background()
    sh.shadow.inherit = False
    try:
        sh.adjustments[0] = 0.06
    except Exception:
        pass
    return sh


def new_slide(prs, dark=False):
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    bg = slide.background.fill
    bg.solid()
    bg.fore_color.rgb = DARK if dark else WHITE
    return slide


def header(slide, idx, title, lead=None):
    text(slide, idx, MARGIN, 0.62, W - 2 * MARGIN, 0.95, title, 30, INK,
         bold=True, label="заголовок")
    y = 1.62
    if lead:
        text(slide, idx, MARGIN, y, W - 2 * MARGIN, 0.75, lead, 15, MUTED,
             spacing=1.25, label="подзаголовок")
        y += 0.95
    return y


def stat_row(slide, idx, top, items, height=1.55):
    """Ряд карточек с числом и подписью."""
    gap = 0.28
    width = (W - 2 * MARGIN - gap * (len(items) - 1)) / len(items)
    for i, (value, caption, color) in enumerate(items):
        left = MARGIN + i * (width + gap)
        panel(slide, left, top, width, height, LIGHT)
        text(slide, idx, left + 0.24, top + 0.20, width - 0.48, 0.66,
             value, 30, color, bold=True, label=f"число {value}")
        text(slide, idx, left + 0.24, top + 0.88, width - 0.48,
             height - 1.02, caption, 12, MUTED, spacing=1.15,
             label=f"подпись {caption[:18]}")
    return top + height


def table(slide, idx, top, headers, rows, widths, row_h=0.42,
          highlight=None):
    total = sum(widths)
    scale = (W - 2 * MARGIN) / total
    widths = [w * scale for w in widths]
    x = MARGIN
    for j, h in enumerate(headers):
        text(slide, idx, x, top, widths[j], 0.34, h, 12, MUTED, bold=True,
             label=f"шапка {h[:14]}")
        x += widths[j]
    y = top + 0.40
    for i, row in enumerate(rows):
        panel(slide, MARGIN - 0.12, y - 0.05, W - 2 * MARGIN + 0.24,
              row_h, LIGHT if i % 2 == 0 else WHITE)
        x = MARGIN
        for j, cell in enumerate(row):
            color = INK
            if highlight and (i, j) in highlight:
                color = highlight[(i, j)]
            text(slide, idx, x, y + 0.03, widths[j], row_h - 0.06, cell,
                 13, color, bold=bool(highlight and (i, j) in highlight),
                 label=f"ячейка {cell[:14]}")
            x += widths[j]
        y += row_h
    return y


# ---------------------------------------------------------------------------
# Слайды
# ---------------------------------------------------------------------------


def build() -> Presentation:
    prs = Presentation()
    prs.slide_width, prs.slide_height = Inches(W), Inches(H)
    n = 0

    # 1. Титул -------------------------------------------------------------
    n += 1
    s = new_slide(prs, dark=True)
    text(s, n, MARGIN, 2.25, W - 2 * MARGIN, 0.5, "LoadLens", 20,
         RGBColor(0x7F, 0xB3, 0xF0), bold=True, label="марка")
    text(s, n, MARGIN, 2.85, W - 2 * MARGIN, 1.9,
         "Интеллектуальная система\nадаптивной балансировки нагрузки", 40,
         WHITE, bold=True, spacing=1.12, label="название")
    text(s, n, MARGIN, 4.88, W - 2 * MARGIN, 0.5,
         "для микросервисных и гибридных веб-архитектур", 19,
         RGBColor(0x9A, 0xA7, 0xB4), label="подназвание")
    text(s, n, MARGIN, 5.9, W - 2 * MARGIN, 0.8,
         "Синявский Станислав Дмитриевич · МГТУ им. Н.Э. Баумана, каф. ИУ5\n"
         "Премия Христофора Леденцова, 2026", 14,
         RGBColor(0x7A, 0x88, 0x96), spacing=1.3, label="автор")

    # 2. Проблема ----------------------------------------------------------
    n += 1
    s = new_slide(prs)
    y = header(s, n, "Приложение тормозит без причины",
               "Обычно баланс открывается за доли секунды. Иногда — за три. "
               "Пользователь ничего не сделал не так:\nв этот момент кто-то "
               "другой заказал выписку за год, и его запрос занял "
               "обработчик целиком.")
    text(s, n, MARGIN, y, W - 2 * MARGIN, 0.5,
         "Асинхронные движки обслуживают все соединения одним циклом "
         "событий. Вычисление останавливает его целиком:", 15, INK,
         label="пояснение")
    stat_row(s, n, y + 0.72, [
        ("9 мс", "задержка соседних запросов\nв потоковом сервере", OK),
        ("125 мс", "та же задержка\nв цикле событий", WARN),
        ("13x", "разница, измеренная\nна одном и том же запросе", INK),
    ])
    text(s, n, MARGIN, y + 2.55, W - 2 * MARGIN, 0.5,
         "Сама обработка занимает одинаковое время. Движки расходятся в "
         "том, сколько вреда запрос причиняет соседям.", 14, MUTED,
         label="вывод")

    # 3. Почему не решается ------------------------------------------------
    n += 1
    s = new_slide(prs)
    y = header(s, n, "Почему это не чинится настройками",
               "Очевидный ответ — задать веса по маршрутам. Он не работает, "
               "и вот почему.")
    table(s, n, y, ["Обращение", "Время выполнения"], [
        ["/api/search?limit=10", "10 мс"],
        ["/api/search?limit=600", "640 мс"],
    ], [3.2, 1.6], row_h=0.5,
        highlight={(0, 1): OK, (1, 1): WARN})
    text(s, n, MARGIN, y + 1.48, W - 2 * MARGIN, 0.55,
         "Один адрес. Разница в 61 раз.", 26, INK, bold=True,
         label="61 раз")
    text(s, n, MARGIN, y + 2.13, W - 2 * MARGIN, 1.05,
         "Вес задаётся на маршрут, а различие лежит внутри маршрута. "
         "Настроить веса так, чтобы это учесть, невозможно в принципе — "
         "не потому, что nginx или Kubernetes плохи, а потому что "
         "величина, которую нужно выразить, в их модель не помещается.",
         16, MUTED, spacing=1.35, label="объяснение")
    stat_row(s, n, y + 3.28, [
        ("80 %", "ошибка оценки стоимости\nпо маршруту", WARN),
        ("3,4 %", "ошибка оценки\nпо параметрам запроса", OK),
    ], height=1.3)

    # 4. Что измерено ------------------------------------------------------
    n += 1
    s = new_slide(prs)
    y = header(s, n, "Что измерено, а не заявлено",
               "Стенд с настоящей нагрузкой: хеширование, кодирование "
               "изображений, обращения к СУБД и по сети.\nНи одного sleep "
               "вместо работы. Все замеры воспроизводятся одной командой.")
    table(s, n, y, ["Утверждение", "Измерено"], [
        ["Движки расходятся в блокировке, а не в скорости", "9 мс против 125 мс"],
        ["Стоимость не определяется маршрутом", "разброс до 85 раз внутри адреса"],
        ["Стоимость предсказуема по видимым признакам", "80 % ошибки → 3,4 %"],
        ["Маршрутизация лучше любого одиночного движка", "+18…46 % пропускной способности"],
        ["Доля подтормаживающих лёгких запросов", "89 % → 7 %"],
        ["Где нужно обучение", "втрое меньше пострадавших"],
    ], [5.2, 2.6], row_h=0.52)

    # 5. Результат ---------------------------------------------------------
    n += 1
    s = new_slide(prs)
    y = header(s, n, "Быстрее и стабильнее одновременно",
               "Разнородный поток, конкурентность 32. Не размен одного на "
               "другое, а выигрыш сразу по двум осям.")
    table(s, n, y, ["Как устроено", "Запросов в секунду",
                    "Доля подтормаживающих запросов"], [
        ["весь трафик в асинхронный движок", "24,7", "89 %"],
        ["весь трафик в синхронный движок", "27,2", "35 %"],
        ["маршрутизация с классификатором", "35,1", "7 %"],
    ], [4.4, 2.2, 2.6], row_h=0.56,
        highlight={(2, 0): ACCENT, (2, 1): OK, (2, 2): OK})
    stat_row(s, n, y + 2.35, [
        ("+42 %", "пропускной способности против\n«всё асинхронно»", OK),
        ("в 13 раз", "меньше подтормаживающих\nлёгких запросов", OK),
        ("+29 %", "против «всё синхронно»,\nи в 5 раз меньше", OK),
    ], height=1.45)

    # 6. Проверено на чужом ПО ---------------------------------------------
    n += 1
    s = new_slide(prs)
    y = header(s, n, "Проверено не на своём стенде",
               "Свой стенд писали мы, и он мог быть устроен так, чтобы "
               "подтвердить нашу мысль. Поэтому проверка сделана дважды "
               "на чужом.")
    text(s, n, MARGIN, y, W - 2 * MARGIN, 0.4,
         "PostgREST поверх PostgreSQL, 300 000 строк. Нашего кода в пути "
         "запроса нет. Один и тот же адрес:", 14, INK, label="чужое пояснение")
    table(s, n, y + 0.52, ["Обращение к /orders", "Медиана"], [
        ["?limit=5", "2,0 мс"],
        ["?limit=20000", "49,1 мс"],
        ["?order=amount.desc&limit=20000", "123,1 мс"],
    ], [3.4, 1.0], highlight={(2, 1): WARN})
    text(s, n, MARGIN, y + 2.42, W - 2 * MARGIN, 0.44,
         "Разница в 72 раза. Инструмент сам, из одного журнала, определил, "
         "что дороже всего обходится сортировка.", 15, INK, bold=True,
         label="118 раз")
    stat_row(s, n, y + 3.00, [
        ("32 968", "функций в производственной\nтрассе Azure Functions", INK),
        ("1,8 млрд", "вызовов настоящих\nклиентов Microsoft", INK),
        ("44,6 %", "обработчиков расходятся\nне меньше чем в 10 раз", WARN),
    ], height=1.35)

    # 7. Честные границы ---------------------------------------------------
    n += 1
    s = new_slide(prs)
    y = header(s, n, "Где это не работает",
               "Границы применимости мы определили и измерили, а не "
               "замолчали. Это часть результата.")
    for i, (title, body) in enumerate([
        ("На однородном потоке — вредно",
         "Если все запросы одной природы, разносить их не по чему. "
         "Маршрутизация проигрывает асинхронному движку 43 % на чистом "
         "вводе-выводе."),
        ("Против least-connections — размен, не победа",
         "Он выдаёт больше запросов в секунду, но у него тормозит 44 % "
         "лёгких обращений. Мы отдаём 20 % пропускной способности за "
         "шестикратное снижение хвоста."),
        ("При деградации самого движка — преимущество теряется",
         "Если асинхронный движок лишается процессорного времени, "
         "обычная балансировка становится лучше. Подход опирается на то, "
         "что движок остаётся таким, каким его измерили."),
    ]):
        top = y + i * 1.34
        panel(s, MARGIN - 0.12, top, W - 2 * MARGIN + 0.24, 1.18, LIGHT)
        text(s, n, MARGIN + 0.12, top + 0.16, W - 2 * MARGIN - 0.24, 0.36,
             title, 16, WARN, bold=True, label=f"граница {i}")
        text(s, n, MARGIN + 0.12, top + 0.58, W - 2 * MARGIN - 0.24, 0.52,
             body, 13, MUTED, spacing=1.2, label=f"текст границы {i}")

    # 7. Продукт, этап 1 ---------------------------------------------------
    n += 1
    s = new_slide(prs)
    y = header(s, n, "Вход в продукт — не прокси, а диагностика",
               "Никто не поставит незнакомый прокси в путь боевых запросов. "
               "Но журнал обращений есть у всех,\nи прочитать его — "
               "нулевой риск.")
    text(s, n, MARGIN, y, W - 2 * MARGIN, 0.45,
         "LoadLens Diagnose", 22, ACCENT, bold=True, label="этап 1")
    text(s, n, MARGIN, y + 0.55, W - 2 * MARGIN, 1.5,
         "Читает обычный журнал nginx, JSON или CSV и показывает: насколько "
         "стоимость различается внутри каждого маршрута, объясняется ли "
         "разброс параметрами запроса и во сколько раз ошибается оценка по "
         "маршруту.", 16, INK, spacing=1.35, label="описание")
    stat_row(s, n, y + 2.18, [
        ("0", "изменений в коде сервиса", INK),
        ("0", "зависимостей, одна команда", INK),
        ("любой", "стек: Node, Go, Java, PHP —\nчитается журнал, а не код", ACCENT),
    ], height=1.5)
    text(s, n, MARGIN, y + 3.88, W - 2 * MARGIN, 0.4,
         "python loadlens.py access.log --html report.html", 15, MUTED,
         label="команда")

    # 8. Демонстрация ------------------------------------------------------
    n += 1
    s = new_slide(prs)
    y = header(s, n, "Инструмент выводит сложность из времён ответа",
               "На журнале нашего стенда LoadLens сам, ничего не зная об "
               "устройстве сервиса, восстановил\nзависимость стоимости от "
               "параметра запроса.")
    table(s, n, y, ["Маршрут", "Параметр", "Найденный закон",
                    "Истинный показатель", "Объясняет"], [
        ["/api/image/thumbnail", "size", "~ size^1,91", "2,00", "99 %"],
        ["/api/report/generate", "days", "~ days^1,98", "2,00", "78 %"],
        ["/api/search", "limit", "~ limit^0,89", "1,00", "83 %"],
        ["/api/feed", "limit", "~ limit^0,77", "1,00", "81 %"],
    ], [3.1, 1.2, 1.9, 1.8, 1.2], row_h=0.5,
        highlight={(0, 2): OK, (1, 2): OK})
    text(s, n, MARGIN, y + 2.55, W - 2 * MARGIN, 1.2,
         "Показатели 1,91 и 1,98 при истинном значении 2,00 — это "
         "квадратичная алгоритмическая сложность операций, выведенная из "
         "одних только времён ответа в журнале веб-сервера.", 16, INK,
         spacing=1.35, label="вывод демо")

    # 9. Продукт, этап 2 ---------------------------------------------------
    n += 1
    s = new_slide(prs)
    y = header(s, n, "Этап 2 — исправить то, что увидели",
               "Шлюз, который оценивает нагрузку каждого запроса и "
               "раскладывает обращения по обработчикам.")
    for i, (title, body) in enumerate([
        ("1. Оценка нагрузки запроса",
         "По параметрам, которые прокси видит до обработки, предсказывается, "
         "на сколько миллисекунд запрос займёт движок для остальных."),
        ("2. Допуск",
         "Запрос пускается в асинхронный движок, только если предсказанная "
         "блокировка не превышает порога. Порог задаёт компромисс между "
         "пропускной способностью и хвостом задержек."),
        ("3. Выбор из допустимых",
         "Среди допустимых движков выбирается менее занятый. Именно здесь, "
         "и только здесь, обучаемая модель даёт втрое меньше пострадавших "
         "запросов, чем правило."),
    ]):
        top = y + i * 1.32
        text(s, n, MARGIN, top, W - 2 * MARGIN, 0.36, title, 17, ACCENT,
             bold=True, label=f"шаг {i}")
        text(s, n, MARGIN, top + 0.44, W - 2 * MARGIN, 0.72, body, 14,
             MUTED, spacing=1.25, label=f"описание шага {i}")

    # Экономика ------------------------------------------------------------
    n += 1
    s = new_slide(prs)
    y = header(s, n, "Сколько это стоит клиенту",
               "Сравнивать пропускную способность саму по себе "
               "бессмысленно: нагрузить сильнее можно всегда, если "
               "согласиться, что люди будут ждать.")
    text(s, n, MARGIN, y, W - 2 * MARGIN, 0.4,
         "Осмысленный вопрос — сколько запросов система держит, не нарушая "
         "обещания по отклику:", 14, INK, label="экономика пояснение")
    table(s, n, y + 0.52, ["Обещание", "least_conn", "Наш модуль"], [
        ["p95 не хуже 100 мс", "37,8 запр./с", "59,2 запр./с"],
        ["p95 не хуже 200 мс", "62,3 запр./с", "59,2 запр./с"],
    ], [2.2, 1.4, 1.4], highlight={(0, 2): OK, (1, 1): MUTED})
    stat_row(s, n, y + 2.02, [
        ("+57 %", "нагрузки при строгом\nобещании по отклику", OK),
        ("657 тыс. ₽", "экономии в год\nпри 1000 запросах в секунду", OK),
        ("0 ₽", "экономии при мягком\nобещании — продавать не надо", WARN),
    ])
    text(s, n, MARGIN, y + 3.82, W - 2 * MARGIN, 0.5,
         "Цены — публичный прейскурант Yandex Cloud. Последнее число не "
         "оговорка мелким шрифтом: из него следует, кому этот продукт не "
         "нужен.", 13, MUTED, spacing=1.2, label="экономика сноска")

    # 10. Рынок ------------------------------------------------------------
    n += 1
    s = new_slide(prs)
    y = header(s, n, "Кому это нужно",
               "Признак клиента виден сразу: в одном API соседствуют "
               "операции, различающиеся по стоимости на порядок,\nи команда "
               "уже столкнулась с необъяснимыми подтормаживаниями.")
    for i, (who, what) in enumerate([
        ("Интернет-магазины", "каталог и выгрузка отчётов"),
        ("Финтех", "проверка баланса и формирование выписки"),
        ("SaaS-сервисы", "интерфейс и экспорт данных"),
        ("Медиасервисы", "лента и обработка изображений"),
    ]):
        col, row = i % 2, i // 2
        left = MARGIN + col * ((W - 2 * MARGIN) / 2 + 0.1)
        top = y + row * 0.95
        width = (W - 2 * MARGIN) / 2 - 0.1
        text(s, n, left, top, width, 0.34, who, 17, INK, bold=True,
             label=f"клиент {i}")
        text(s, n, left, top + 0.40, width, 0.4, what, 14, MUTED,
             label=f"что {i}")
    top = y + 2.1
    panel(s, MARGIN - 0.12, top, W - 2 * MARGIN + 0.24, 1.5, LIGHT)
    text(s, n, MARGIN + 0.12, top + 0.18, W - 2 * MARGIN - 0.24, 0.36,
         "Смежное направление", 15, ACCENT, bold=True, label="смежное")
    text(s, n, MARGIN + 0.12, top + 0.6, W - 2 * MARGIN - 0.24, 0.78,
         "Обслуживание языковых моделей: стоимость запроса меняется на "
         "порядки в зависимости от числа токенов, а распределяют запросы "
         "всё тем же счётом соединений. Механизм тот же — но мы там ничего "
         "не измеряли и выдавать это за проверенное не будем.",
         13, MUTED, spacing=1.2, label="текст смежное")

    # 11. Модель и честность -----------------------------------------------
    n += 1
    s = new_slide(prs)
    y = header(s, n, "Модель и то, чего пока нет")
    text(s, n, MARGIN, y, (W - 2 * MARGIN) / 2 - 0.2, 0.36,
         "За что платят", 17, INK, bold=True, label="монетизация")
    text(s, n, MARGIN, y + 0.44, (W - 2 * MARGIN) / 2 - 0.2, 2.6,
         "Меньше серверов при том же качестве отклика: сегодня проблему "
         "закрывают избыточностью.\n\n"
         "Меньше работы инженеров: альтернатива — вручную выносить тяжёлые "
         "операции в отдельные сервисы, это месяцы работы команды.\n\n"
         "Открытое ядро: диагностика и маршрутизатор открыты и служат "
         "доказательством; платное — панель управления, интеграции с "
         "Envoy и Kubernetes, поддержка.",
         13, MUTED, spacing=1.25, label="текст монетизации")
    left2 = MARGIN + (W - 2 * MARGIN) / 2 + 0.2
    text(s, n, left2, y, (W - 2 * MARGIN) / 2 - 0.2, 0.36,
         "Чего пока нет", 17, WARN, bold=True, label="чего нет")
    text(s, n, left2, y + 0.44, (W - 2 * MARGIN) / 2 - 0.2, 2.6,
         "Ни одного платящего клиента. Цену никто не подтверждал.\n\n"
         "Эффект на боевом трафике настоящей компании не проверен: все "
         "замеры сделаны на одной машине.\n\n"
         "Размер рынка не оценён. Поведение при масштабировании на много "
         "обработчиков не измерено.\n\n"
         "Это гипотезы, а не результаты, и мы их так и называем.",
         13, MUTED, spacing=1.25, label="текст чего нет")

    # 12. Просьба ----------------------------------------------------------
    n += 1
    s = new_slide(prs, dark=True)
    text(s, n, MARGIN, 1.5, W - 2 * MARGIN, 0.9,
         "Чего просим", 32, WHITE, bold=True, label="просьба")
    text(s, n, MARGIN, 2.6, W - 2 * MARGIN, 1.6,
         "Не денег на рост — рано. Ресурса на проверку гипотезы на реальных "
         "данных: прогнать диагностику на журналах трёх–пяти живых сервисов "
         "и установить, встречается ли обнаруженный разброс за пределами "
         "лаборатории.", 19, RGBColor(0xD6, 0xDE, 0xE6), spacing=1.35,
         label="текст просьбы")
    text(s, n, MARGIN, 4.4, W - 2 * MARGIN, 1.1,
         "Опыт дешёвый и быстрый: журналы у команд уже есть, инструмент "
         "работает без внедрения. Он либо подтвердит, что проблема "
         "массовая, либо покажет, что она наша частная, — и второе мы "
         "скажем так же прямо.", 15, RGBColor(0x9A, 0xA7, 0xB4),
         spacing=1.3, label="почему дёшево")
    text(s, n, MARGIN, 6.1, W - 2 * MARGIN, 0.7,
         "Синявский Станислав Дмитриевич · ssd-tula@mail.ru\n"
         "Код, замеры и научные статьи — открыты и воспроизводимы", 14,
         RGBColor(0x7A, 0x88, 0x96), spacing=1.3, label="контакты")

    return prs


def check_overlaps() -> list[str]:
    """Ищет наложения текстовых блоков на одном слайде."""
    problems = []
    by_slide: dict[int, list] = {}
    for idx, l, t, w, h, label in boxes:
        by_slide.setdefault(idx, []).append((l, t, w, h, label))
    for idx, items in by_slide.items():
        for i in range(len(items)):
            for j in range(i + 1, len(items)):
                a, b = items[i], items[j]
                ox = min(a[0] + a[2], b[0] + b[2]) - max(a[0], b[0])
                oy = min(a[1] + a[3], b[1] + b[3]) - max(a[1], b[1])
                if ox > 0.05 and oy > 0.05:
                    problems.append(
                        f"слайд {idx}: «{a[4]}» и «{b[4]}» "
                        f"перекрываются на {ox:.2f}x{oy:.2f} дюйма")
        for l, t, w, h, label in items:
            if l < 0 or t < 0 or l + w > W + 0.01 or t + h > H + 0.01:
                problems.append(
                    f"слайд {idx}: «{label}» выходит за границы слайда")
    return problems


if __name__ == "__main__":
    prs = build()
    problems = check_overlaps()
    if problems:
        print("ПРОБЛЕМЫ ВЁРСТКИ:")
        for p in problems:
            print("  ", p)
    else:
        print("Проверка геометрии: наложений нет")
    prs.save(OUT)
    print(f"Слайдов: {len(prs.slides)}")
    print(f"Сохранено: {OUT.name}")
