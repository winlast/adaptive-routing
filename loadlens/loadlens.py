#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
LoadLens — показывает, чего не видит ваш балансировщик нагрузки.

Балансировщики распределяют запросы по кругу или по числу соединений.
Веса, если они есть, задаются на маршрут. Но стоимость запроса
маршрутом не определяется: `/api/search?limit=10` и
`/api/search?limit=5000` идут по одному адресу и различаются на порядки.
Настройка весов по маршрутам поэтому структурно неверна — она обязана
приписать обоим запросам одно число.

Инструмент читает обычный журнал обращений и отвечает на три вопроса.

  1. Насколько стоимость запросов различается ВНУТРИ одного маршрута.
  2. Объясняется ли этот разброс параметрами запроса, которые видит
     прокси, — и если да, то каким и по какому закону.
  3. Во сколько раз ошибается оценка по маршруту против оценки по
     параметрам запроса.

Зависимостей нет: только стандартная библиотека. Запускается на любом
стеке, потому что читает журнал, а не код.

    python loadlens.py access.log
    python loadlens.py access.log --html report.html

Поддерживаются: combined-формат nginx с временем ответа, JSON-строки,
CSV. Формат определяется автоматически.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import re
import statistics
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import parse_qsl, urlsplit


# ---------------------------------------------------------------------------
# Чтение журналов
# ---------------------------------------------------------------------------


@dataclass
class Request:
    """Одно обращение, каким его видит прокси."""

    path: str
    params: dict[str, float]
    duration_ms: float
    started_at: float | None = None

    @property
    def ended_at(self) -> float | None:
        if self.started_at is None:
            return None
        return self.started_at + self.duration_ms / 1000.0


# Числовые сегменты пути (идентификаторы) заменяются заглушкой: иначе
# каждый /api/user/12345 стал бы отдельным маршрутом.
_ID_SEGMENT = re.compile(r"^(\d+|[0-9a-f]{8,}|[0-9a-f-]{32,})$", re.I)


def normalize_path(raw: str) -> tuple[str, dict[str, float]]:
    """Разделяет обращение на шаблон маршрута и числовые параметры."""
    parts = urlsplit(raw)
    segments = []
    for seg in parts.path.split("/"):
        segments.append("{id}" if _ID_SEGMENT.match(seg) else seg)
    route = "/".join(segments) or "/"

    params: dict[str, float] = {}
    for key, value in parse_qsl(parts.query, keep_blank_values=False):
        try:
            params[key] = float(value)
            continue
        except ValueError:
            pass
        # Нечисловой параметр тоже влияет на стоимость: сортировка,
        # фильтр, набор запрашиваемых полей. Числом его не выразить, но
        # само его присутствие — уже признак, и прокси его видит.
        params[f"есть:{key}"] = 1.0
        if "," in value:
            # Перечисление (например, список полей) — его длина обычно
            # пропорциональна объёму работы.
            params[f"число:{key}"] = float(value.count(",") + 1)
    return route, params


_NGINX = re.compile(
    r'"(?P<method>[A-Z]+)\s+(?P<url>\S+)\s+HTTP/[\d.]+"'
    r'.*?(?P<rt>\d+\.\d{3})\s*$'
)

_DURATION_KEYS = ("duration_ms", "latency_ms", "response_time_ms", "took_ms",
                  "duration", "latency", "request_time", "upstream_response_time",
                  "response_time", "elapsed")
_PATH_KEYS = ("path", "url", "uri", "request", "request_uri", "endpoint",
              "route")
_TIME_KEYS = ("timestamp", "time", "ts", "start", "started_at", "@timestamp")


def _as_ms(value: float, key: str) -> float:
    """Приводит длительность к миллисекундам по имени поля."""
    if key.endswith("_ms") or key.endswith("ms"):
        return float(value)
    # Поля вроде request_time у nginx измеряются в секундах.
    return float(value) * 1000.0


def _from_mapping(row: dict) -> Request | None:
    lower = {str(k).lower(): v for k, v in row.items()}
    path = next((lower[k] for k in _PATH_KEYS if lower.get(k)), None)
    dur_key = next((k for k in _DURATION_KEYS if lower.get(k) not in (None, "")),
                   None)
    if path is None or dur_key is None:
        return None
    try:
        duration = _as_ms(float(lower[dur_key]), dur_key)
    except (TypeError, ValueError):
        return None
    started = None
    for k in _TIME_KEYS:
        raw = lower.get(k)
        if raw in (None, ""):
            continue
        try:
            started = float(raw)
        except (TypeError, ValueError):
            started = None
        break
    route, params = normalize_path(str(path))
    # Параметры могли прийти отдельным полем, а не в строке запроса.
    for key, value in lower.items():
        if key.startswith("param_") or key.startswith("arg_"):
            try:
                params[key.split("_", 1)[1]] = float(value)
            except (TypeError, ValueError):
                continue
    return Request(route, params, duration, started)


def read_log(path: Path) -> list[Request]:
    """Читает журнал, определяя формат по содержимому."""
    text = path.read_text(encoding="utf-8", errors="replace").splitlines()
    if not text:
        return []

    requests: list[Request] = []

    # JSON-строки
    if text[0].lstrip().startswith("{"):
        for line in text:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            req = _from_mapping(row)
            if req:
                requests.append(req)
        if requests:
            return requests

    # CSV с заголовком
    if "," in text[0] and not text[0].startswith("{"):
        try:
            for row in csv.DictReader(text):
                req = _from_mapping(row)
                if req:
                    requests.append(req)
        except csv.Error:
            pass
        if requests:
            return requests

    # combined-формат nginx с $request_time в конце строки
    for line in text:
        m = _NGINX.search(line)
        if not m:
            continue
        route, params = normalize_path(m.group("url"))
        requests.append(
            Request(route, params, float(m.group("rt")) * 1000.0))
    return requests


# ---------------------------------------------------------------------------
# Анализ
# ---------------------------------------------------------------------------


def percentile(values: list[float], p: float) -> float:
    ordered = sorted(values)
    return ordered[min(int(len(ordered) * p), len(ordered) - 1)]


def fit_power_law(xs: list[float], ys: list[float]) -> tuple[float, float, float]:
    """
    Подбирает зависимость y = a * x^b методом наименьших квадратов в
    логарифмах и возвращает (a, b, доля объяснённой дисперсии).

    В логарифмах степенная зависимость выпрямляется, поэтому достаточно
    обычной линейной регрессии. Логарифмирование здесь не приём для
    удобства: распределения длительностей имеют тяжёлый правый хвост, и
    без него несколько самых долгих запросов определяли бы всю подгонку.
    """
    n = len(xs)
    if n < 8:
        return (0.0, 0.0, 0.0)
    lx = [math.log(max(x, 1e-9)) for x in xs]
    ly = [math.log(max(y, 1e-9)) for y in ys]
    mx, my = sum(lx) / n, sum(ly) / n
    sxx = sum((v - mx) ** 2 for v in lx)
    if sxx < 1e-12:
        return (0.0, 0.0, 0.0)
    sxy = sum((lx[i] - mx) * (ly[i] - my) for i in range(n))
    b = sxy / sxx
    a = my - b * mx
    ss_tot = sum((v - my) ** 2 for v in ly)
    ss_res = sum((ly[i] - (a + b * lx[i])) ** 2 for i in range(n))
    r2 = 1.0 - ss_res / ss_tot if ss_tot > 1e-12 else 0.0
    return (math.exp(a), b, max(0.0, r2))


def unqueued(pairs: list[tuple[float, float]],
             keep: float = 0.25, buckets: int = 12
             ) -> list[tuple[float, float]]:
    """
    Оставляет обращения, меньше других пострадавшие от очереди.

    В журнале рабочей системы измеренное время складывается из
    собственной стоимости запроса и ожидания в очереди. Ожидание от
    параметров запроса не зависит и потому только зашумляет
    зависимость. Очередь способна время лишь увеличить, поэтому в
    каждой группе близких значений параметра самые быстрые обращения
    ближе всего к собственной стоимости.

    Значения параметра разбиваются на логарифмически равные группы, и в
    каждой остаётся быстрая четверть. Без этого приёма на журнале
    нагруженной системы зависимость от параметра теряется в шуме.
    """
    if len(pairs) < 16:
        return pairs
    xs = [p[0] for p in pairs]
    lo, hi = math.log(max(min(xs), 1e-9)), math.log(max(xs))
    if hi - lo < 1e-9:
        return pairs
    grouped: dict[int, list[tuple[float, float]]] = defaultdict(list)
    for x, y in pairs:
        idx = min(int((math.log(max(x, 1e-9)) - lo) / (hi - lo) * buckets),
                  buckets - 1)
        grouped[idx].append((x, y))
    out: list[tuple[float, float]] = []
    for items in grouped.values():
        items.sort(key=lambda t: t[1])
        out += items[:max(1, int(len(items) * keep))]
    return out


def solve(a: list[list[float]], b: list[float]) -> list[float] | None:
    """Решение системы методом Гаусса с выбором главного элемента."""
    n = len(b)
    m = [row[:] + [b[i]] for i, row in enumerate(a)]
    for col in range(n):
        pivot = max(range(col, n), key=lambda r: abs(m[r][col]))
        if abs(m[pivot][col]) < 1e-12:
            return None
        m[col], m[pivot] = m[pivot], m[col]
        for r in range(n):
            if r == col:
                continue
            f = m[r][col] / m[col][col]
            for c in range(col, n + 1):
                m[r][c] -= f * m[col][c]
    return [m[i][n] / m[i][i] for i in range(n)]


def fit_multi(rows: list[tuple[dict[str, float], float]],
              names: list[str]) -> tuple[dict[str, float], float] | None:
    """
    Подбирает зависимость стоимости сразу от нескольких признаков.

    Одного параметра мало. В настоящих интерфейсах стоимость определяют
    несколько вещей одновременно: сколько строк просят, нужна ли
    сортировка, сколько полей возвращать, стоит ли фильтр. Выбирая один
    «лучший» параметр, мы объясняли малую долю разброса — это выяснилось
    на чужом сервисе, а не на своём стенде.

    Подгонка ведётся в логарифмах: произведение степеней превращается в
    сумму, и обычный метод наименьших квадратов даёт коэффициенты сразу
    для всех признаков. Возвращается отображение «признак → показатель»
    и доля объяснённой дисперсии.
    """
    if len(rows) < max(12, 3 * (len(names) + 1)):
        return None

    def vec(params: dict[str, float]) -> list[float]:
        out = [1.0]
        for name in names:
            value = params.get(name, 0.0)
            out.append(math.log(max(value, 1.0)) if not name.startswith("есть:")
                       else value)
        return out

    k = len(names) + 1
    ata = [[0.0] * k for _ in range(k)]
    atb = [0.0] * k
    ys = []
    for params, duration in rows:
        x = vec(params)
        y = math.log(max(duration, 1e-9))
        ys.append(y)
        for i in range(k):
            atb[i] += x[i] * y
            for j in range(k):
                ata[i][j] += x[i] * x[j]
    # Небольшая регуляризация: признаки бывают почти коллинеарны, и без
    # неё система оказывается вырожденной.
    for i in range(1, k):
        ata[i][i] += 1e-6

    coef = solve(ata, atb)
    if coef is None:
        return None

    mean = sum(ys) / len(ys)
    ss_tot = sum((y - mean) ** 2 for y in ys)
    ss_res = sum((ys[i] - sum(c * v for c, v in zip(coef, vec(rows[i][0]))))
                 ** 2 for i in range(len(rows)))
    r2 = 1.0 - ss_res / ss_tot if ss_tot > 1e-12 else 0.0
    return ({"(свободный член)": coef[0],
             **{names[i]: coef[i + 1] for i in range(len(names))}},
            max(0.0, min(1.0, r2)))


def predict_multi(model: dict[str, float], params: dict[str, float]) -> float:
    total = model["(свободный член)"]
    for name, coef in model.items():
        if name == "(свободный член)":
            continue
        value = params.get(name, 0.0)
        total += coef * (value if name.startswith("есть:")
                         else math.log(max(value, 1.0)))
    return math.exp(total)


def median_ape(predicted: list[float], actual: list[float]) -> float:
    """
    Медианная относительная ошибка, %.

    Обращения с нулевой измеренной длительностью исключаются. Ноль в
    журнале nginx означает не отсутствие работы, а время ниже
    разрешения записи — одну миллисекунду. Считать по ним относительную
    ошибку нельзя: деление на ноль превращает такое обращение в ошибку
    порядка ста миллиардов процентов, и оно сдвигает медиану вверх, хотя
    о точности оценки не говорит ничего.

    Раньше здесь стояло деление на max(a, 1e-9), и эти обращения
    участвовали в расчёте. На журнале PostgREST их оказалось 11 из 1500,
    и они завышали ошибку обеих оценок.
    """
    errors = [abs(p - a) / a * 100
              for p, a in zip(predicted, actual) if a > 0]
    return statistics.median(errors) if errors else float("nan")


@dataclass
class RouteReport:
    route: str
    count: int
    p50: float
    p95: float
    p99: float
    spread: float           # во сколько раз p99 больше p50
    share_of_time: float    # доля всего процессорного времени, %
    best_param: str | None = None
    exponent: float = 0.0
    explained: float = 0.0  # доля объяснённой дисперсии
    route_error: float = 0.0    # ошибка оценки по маршруту, %
    param_error: float = 0.0    # ошибка оценки по параметру, %
    # Оценка сразу по нескольким признакам запроса. Считается на
    # отложенной половине обращений, и одиночный параметр на той же
    # половине — иначе сравнение нечестно: у модели с бо́льшим числом
    # признаков всегда меньше ошибка на тех данных, где её настраивали.
    multi: dict[str, float] | None = None
    multi_explained: float = 0.0
    holdout_param_error: float = 0.0
    holdout_multi_error: float = 0.0


@dataclass
class Analysis:
    total: int
    total_ms: float
    routes: list[RouteReport] = field(default_factory=list)
    route_error: float = 0.0
    param_error: float = 0.0
    blocked_share: float | None = None
    blocked_factor: float = 0.0
    heavy_share_of_requests: float = 0.0
    heavy_share_of_time: float = 0.0


def analyse(requests: list[Request], heavy_percentile: float = 0.9) -> Analysis:
    by_route: dict[str, list[Request]] = defaultdict(list)
    for r in requests:
        by_route[r.path].append(r)

    total_ms = sum(r.duration_ms for r in requests)
    result = Analysis(total=len(requests), total_ms=total_ms)

    all_route_pred: list[float] = []
    all_param_pred: list[float] = []
    all_actual: list[float] = []

    for route, group in by_route.items():
        durations = [r.duration_ms for r in group]
        p50 = percentile(durations, 0.50)
        p95 = percentile(durations, 0.95)
        p99 = percentile(durations, 0.99)
        report = RouteReport(
            route=route, count=len(group), p50=p50, p95=p95, p99=p99,
            spread=(p99 / p50) if p50 > 0 else 0.0,
            share_of_time=(sum(durations) / total_ms * 100) if total_ms else 0.0,
        )

        # Какой параметр лучше всего объясняет разброс внутри маршрута.
        candidates = defaultdict(list)
        for r in group:
            for key, value in r.params.items():
                if value > 0:
                    candidates[key].append((value, r.duration_ms))
        best = None
        for key, pairs in candidates.items():
            if len(pairs) < max(8, len(group) * 0.3):
                continue
            clean = unqueued(pairs)
            a, b, r2 = fit_power_law([p[0] for p in clean],
                                     [p[1] for p in clean])
            if abs(b) < 0.05:
                continue
            if best is None or r2 > best[3]:
                best = (key, a, b, r2, clean)

        # Точность оценок сравнивается на тех же наименее загруженных
        # обращениях: предсказать ожидание в очереди по признакам самого
        # запроса нельзя ни одной из оценок, и включать его в сравнение
        # значило бы мерить шум.
        if best:
            key, a, b, r2, clean = best
            report.best_param, report.exponent, report.explained = key, b, r2
            eval_x = [p[0] for p in clean]
            eval_y = [p[1] for p in clean]
            base = statistics.mean(eval_y)
            route_pred = [base] * len(eval_y)
            param_pred = [a * (x ** b) for x in eval_x]
        else:
            eval_y = [y for _, y in unqueued([(1.0, d) for d in durations])]
            base = statistics.mean(eval_y)
            route_pred = [base] * len(eval_y)
            param_pred = route_pred

        report.route_error = median_ape(route_pred, eval_y)
        report.param_error = median_ape(param_pred, eval_y)

        # Стоимость редко определяется одним числом. В настоящих
        # интерфейсах на неё влияют сразу несколько вещей: сколько
        # записей просят, нужна ли сортировка, сколько полей вернуть,
        # стоит ли фильтр. Здесь проверяется, добавляют ли остальные
        # признаки что-то поверх лучшего одиночного параметра.
        names = sorted({key for r in group for key in r.params})
        if best and len(names) > 1:
            key = best[0]
            rows = [(r.params, r.duration_ms) for r in group]
            train = [rows[i] for i in range(0, len(rows), 2)]
            test = [rows[i] for i in range(1, len(rows), 2)]
            fitted = fit_multi(train, names)
            single = fit_multi(train, [key])
            if fitted and single and test:
                report.multi = fitted[0]
                report.multi_explained = fitted[1]
                actual = [d for _, d in test]
                report.holdout_multi_error = median_ape(
                    [predict_multi(fitted[0], pr) for pr, _ in test], actual)
                report.holdout_param_error = median_ape(
                    [predict_multi(single[0], pr) for pr, _ in test], actual)
        result.routes.append(report)

        all_route_pred += route_pred
        all_param_pred += param_pred
        all_actual += eval_y

    result.routes.sort(key=lambda r: r.share_of_time, reverse=True)
    result.route_error = median_ape(all_route_pred, all_actual)
    result.param_error = median_ape(all_param_pred, all_actual)

    # Доля тяжёлых запросов и доля времени, которую они занимают.
    threshold = percentile([r.duration_ms for r in requests], heavy_percentile)
    heavy = [r for r in requests if r.duration_ms >= threshold]
    result.heavy_share_of_requests = len(heavy) / len(requests) * 100
    result.heavy_share_of_time = (
        sum(r.duration_ms for r in heavy) / total_ms * 100) if total_ms else 0.0

    result.blocked_share, result.blocked_factor = _blocking_exposure(requests)
    return result


def _blocking_exposure(requests: list[Request],
                       factor: float = 10.0) -> tuple[float | None, float]:
    """
    Доля быстрых запросов, которые выполнялись одновременно с запросом
    во много раз тяжелее.

    Это верхняя оценка подверженности блокировке, а не сама блокировка:
    по журналу видно, что запросы перекрывались во времени, но не видно,
    обслуживались ли они одним потоком выполнения. На однопоточном цикле
    событий такое перекрытие означает ожидание, на многопоточном
    сервере — только конкуренцию. Величина показывает, насколько часто
    возникает ситуация, в которой блокировка возможна.
    """
    timed = [r for r in requests if r.started_at is not None]
    if len(timed) < 50:
        return None, factor
    timed.sort(key=lambda r: r.started_at)
    median = statistics.median(r.duration_ms for r in timed)
    light = [r for r in timed if r.duration_ms <= median]
    heavy = sorted((r for r in timed if r.duration_ms >= median * factor),
                   key=lambda r: r.started_at)
    if not light or not heavy:
        return 0.0, factor

    starts = [r.started_at for r in heavy]
    import bisect

    exposed = 0
    for r in light:
        i = bisect.bisect_right(starts, r.ended_at)
        # Достаточно проверить ближайшие тяжёлые запросы слева.
        for h in heavy[max(0, i - 12):i]:
            if h.ended_at > r.started_at:
                exposed += 1
                break
    return exposed / len(light) * 100, factor


# ---------------------------------------------------------------------------
# Отчёт
# ---------------------------------------------------------------------------


def print_report(a: Analysis, top: int = 8) -> None:
    print()
    print("LoadLens — что скрыто от балансировщика")
    print("=" * 78)
    print(f"Обращений разобрано: {a.total:,}".replace(",", " "))
    print(f"Маршрутов: {len(a.routes)}")
    print()

    print("Разброс стоимости ВНУТРИ маршрута")
    print("-" * 78)
    print(f"{'маршрут':<34}{'доля времени':>14}{'p50':>8}{'p99':>9}{'разброс':>11}")
    for r in a.routes[:top]:
        print(f"{r.route[:33]:<34}{r.share_of_time:>13.1f}%"
              f"{r.p50:>8.0f}{r.p99:>9.0f}{r.spread:>10.0f}x")
    if len(a.routes) > top:
        print(f"... ещё {len(a.routes) - top} маршрутов")
    print()

    explained = [r for r in a.routes if r.best_param and r.explained > 0.3]
    if explained:
        print("Чем разброс объясняется")
        print("-" * 78)
        print(f"{'маршрут':<34}{'параметр':>12}{'закон':>14}{'объясняет':>12}")
        for r in explained[:top]:
            law = f"~ {r.best_param}^{r.exponent:.2f}"
            print(f"{r.route[:33]:<34}{r.best_param[:11]:>12}{law:>14}"
                  f"{r.explained * 100:>11.0f}%")
        print()

    print("Во что обходится оценка по маршруту")
    print("-" * 78)
    print(f"  оценка по маршруту (как у балансировщика с весами): "
          f"{a.route_error:>6.1f}% ошибки")
    print(f"  оценка по параметрам запроса:                       "
          f"{a.param_error:>6.1f}% ошибки")
    if a.param_error > 0 and a.route_error > a.param_error:
        print(f"  разница: в {a.route_error / a.param_error:.1f} раза точнее")
    print()
    print("  Сравнение ведётся по наименее загруженным обращениям: ожидание")
    print("  в очереди по признакам запроса не предсказуемо ни одной оценкой.")
    print()

    multi = [r for r in a.routes
             if r.multi and r.holdout_param_error > 0]
    if multi:
        print("Одного параметра мало: оценка сразу по нескольким признакам")
        print("-" * 78)
        print(f"{'маршрут':<30}{'один параметр':>16}{'все признаки':>16}"
              f"{'объясняет':>13}")
        for r in multi[:top]:
            print(f"{r.route[:29]:<30}{r.holdout_param_error:>15.1f}%"
                  f"{r.holdout_multi_error:>15.1f}%"
                  f"{r.multi_explained * 100:>12.0f}%")
        print()
        print("  Обе оценки настроены на одной половине обращений и проверены")
        print("  на другой: у модели с бо́льшим числом признаков ошибка на")
        print("  своих же данных всегда меньше, и сравнивать там нечестно.")
        heaviest = multi[0]
        weights = sorted(((k, v) for k, v in heaviest.multi.items()
                          if k != "(свободный член)"),
                         key=lambda kv: -abs(kv[1]))[:4]
        if weights:
            print()
            print(f"  Что влияет на «{heaviest.route[:40]}»:")
            for name, coef in weights:
                print(f"    {name:<28}{coef:+.2f}")
        print()

    print("Концентрация нагрузки")
    print("-" * 78)
    print(f"  {a.heavy_share_of_requests:.0f} % самых тяжёлых обращений "
          f"занимают {a.heavy_share_of_time:.0f} % всего времени обработки")
    if a.blocked_share is not None:
        print(f"  {a.blocked_share:.0f} % быстрых обращений выполнялись "
              f"одновременно с запросом")
        print(f"  в {a.blocked_factor:.0f}+ раз тяжелее — на однопоточном "
              f"цикле событий это ожидание")
    print()

    verdict(a)


def verdict(a: Analysis) -> None:
    worst = max((r for r in a.routes if r.count >= 20),
                key=lambda r: r.spread, default=None)
    print("Вывод")
    print("-" * 78)
    if worst and worst.spread >= 5:
        print(f"  Внутри маршрута {worst.route} стоимость запросов "
              f"различается в {worst.spread:.0f} раз.")
        print("  Балансировщик считает эти обращения одинаковыми: вес "
              "задаётся на маршрут,")
        print("  а различие лежит внутри него. Настроить веса так, чтобы "
              "это учесть, нельзя.")
        if worst.multi and worst.multi_explained > worst.explained:
            top_name = max(
                ((k, v) for k, v in worst.multi.items()
                 if k != "(свободный член)"), key=lambda kv: abs(kv[1]))[0]
            print(f"  При этом разброс предсказуем по видимым признакам "
                  f"запроса на")
            print(f"  {worst.multi_explained * 100:.0f} %, и сильнее всего "
                  f"на стоимость влияет «{top_name}».")
            print(f"  Одного параметра здесь недостаточно: по нему одному "
                  f"объясняется лишь")
            print(f"  {worst.explained * 100:.0f} %. Все признаки видны "
                  f"прокси до обработки запроса.")
        elif worst.best_param:
            print(f"  При этом разброс предсказуем: он объясняется "
                  f"параметром «{worst.best_param}»")
            print(f"  на {worst.explained * 100:.0f} % и виден прокси "
                  f"до обработки запроса.")
    else:
        print("  Заметного разброса внутри маршрутов не обнаружено. "
              "Для такого трафика")
        print("  обычной балансировки достаточно, и усложнение не "
              "оправдано.")
    print()


HTML = """<!DOCTYPE html>
<html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>LoadLens — отчёт</title><style>
:root {{ --ink:#1a2027; --muted:#5b6b7a; --line:#dde3e9; --accent:#1565c0;
        --warn:#c62828; --ok:#2e7d32; --bg:#fff; --card:#f7f9fb; }}
@media (prefers-color-scheme: dark) {{ :root {{ --ink:#e6edf3; --muted:#9aa7b4;
  --line:#2a3542; --bg:#0f141a; --card:#161d26; }} }}
* {{ box-sizing:border-box }}
body {{ margin:0; padding:32px 16px; background:var(--bg); color:var(--ink);
  font:16px/1.6 -apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif }}
.wrap {{ max-width:960px; margin:0 auto }}
h1 {{ font-size:26px; margin:0 0 4px }}
.sub {{ color:var(--muted); margin:0 0 28px }}
.cards {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(200px,1fr));
  gap:14px; margin-bottom:32px }}
.card {{ background:var(--card); border:1px solid var(--line);
  border-radius:12px; padding:16px }}
.card .n {{ font-size:30px; font-weight:650; letter-spacing:-.02em }}
.card .l {{ color:var(--muted); font-size:13px; margin-top:4px }}
h2 {{ font-size:18px; margin:32px 0 10px }}
table {{ width:100%; border-collapse:collapse; font-size:14px }}
th,td {{ text-align:right; padding:9px 10px; border-bottom:1px solid var(--line) }}
th:first-child,td:first-child {{ text-align:left; font-family:ui-monospace,
  SFMono-Regular,Menlo,monospace; font-size:13px }}
th {{ color:var(--muted); font-weight:600; font-size:12px;
  text-transform:uppercase; letter-spacing:.04em }}
.big {{ color:var(--warn); font-weight:650 }}
.good {{ color:var(--ok); font-weight:650 }}
.note {{ background:var(--card); border-left:3px solid var(--accent);
  border-radius:0 8px 8px 0; padding:14px 16px; margin:24px 0;
  color:var(--muted); font-size:14px }}
footer {{ margin-top:40px; padding-top:16px; border-top:1px solid var(--line);
  color:var(--muted); font-size:13px }}
</style></head><body><div class="wrap">
<h1>Что скрыто от балансировщика</h1>
<p class="sub">Разобрано {total} обращений по {n_routes} маршрутам</p>
<div class="cards">
  <div class="card"><div class="n big">{worst_spread}x</div>
    <div class="l">разброс стоимости внутри одного маршрута</div></div>
  <div class="card"><div class="n big">{route_error}%</div>
    <div class="l">ошибка оценки по маршруту</div></div>
  <div class="card"><div class="n good">{param_error}%</div>
    <div class="l">ошибка оценки по параметрам запроса</div></div>
  <div class="card"><div class="n">{heavy_time}%</div>
    <div class="l">времени занимают {heavy_req}% обращений</div></div>
</div>
<div class="note">{verdict}</div>
<h2>Разброс внутри маршрутов</h2>
<table><thead><tr><th>Маршрут</th><th>Обращений</th><th>Доля времени</th>
<th>p50, мс</th><th>p99, мс</th><th>Разброс</th></tr></thead>
<tbody>{rows}</tbody></table>
{explained_block}
<footer>{footer}</footer>
</div></body></html>"""


def write_html(a: Analysis, path: Path) -> None:
    rows = "".join(
        f"<tr><td>{r.route}</td><td>{r.count}</td>"
        f"<td>{r.share_of_time:.1f}%</td><td>{r.p50:.0f}</td>"
        f"<td>{r.p99:.0f}</td>"
        f"<td class=\"{'big' if r.spread >= 5 else ''}\">{r.spread:.0f}x</td></tr>"
        for r in a.routes[:20])

    explained = [r for r in a.routes if r.best_param and r.explained > 0.3]
    if explained:
        erows = "".join(
            f"<tr><td>{r.route}</td><td>{r.best_param}</td>"
            f"<td>~ {r.best_param}<sup>{r.exponent:.2f}</sup></td>"
            f"<td>{r.explained * 100:.0f}%</td>"
            f"<td>{r.route_error:.0f}% → <span class=\"good\">"
            f"{r.param_error:.0f}%</span></td></tr>"
            for r in explained[:20])
        explained_block = (
            "<h2>Чем разброс объясняется</h2><p class=\"sub\">Параметр "
            "виден прокси до обработки запроса — значит, стоимость "
            "предсказуема заранее.</p>"
            "<table><thead><tr><th>Маршрут</th><th>Параметр</th>"
            "<th>Зависимость</th><th>Объясняет</th>"
            "<th>Ошибка оценки</th></tr></thead>"
            f"<tbody>{erows}</tbody></table>")
    else:
        explained_block = ""

    worst = max((r for r in a.routes if r.count >= 20),
                key=lambda r: r.spread, default=None)
    if worst and worst.spread >= 5:
        v = (f"Внутри маршрута <b>{worst.route}</b> стоимость запросов "
             f"различается в {worst.spread:.0f} раз. Балансировщик считает "
             f"эти обращения одинаковыми: вес задаётся на маршрут, а "
             f"различие лежит внутри него.")
        if worst.best_param:
            v += (f" Разброс при этом предсказуем — он объясняется "
                  f"параметром <b>{worst.best_param}</b> на "
                  f"{worst.explained * 100:.0f}&nbsp;%.")
    else:
        v = ("Заметного разброса внутри маршрутов не обнаружено. Для такого "
             "трафика обычной балансировки достаточно.")

    blocked = ""
    if a.blocked_share is not None:
        blocked = (f" {a.blocked_share:.0f}&nbsp;% быстрых обращений шли "
                   f"одновременно с запросом в {a.blocked_factor:.0f}+ раз "
                   f"тяжелее; на однопоточном цикле событий это ожидание.")

    path.write_text(HTML.format(
        total=f"{a.total:,}".replace(",", " "), n_routes=len(a.routes),
        worst_spread=f"{worst.spread:.0f}" if worst else "—",
        route_error=f"{a.route_error:.0f}", param_error=f"{a.param_error:.0f}",
        heavy_time=f"{a.heavy_share_of_time:.0f}",
        heavy_req=f"{a.heavy_share_of_requests:.0f}",
        verdict=v, rows=rows, explained_block=explained_block,
        footer="Отчёт построен по журналу обращений. Ошибка оценки — "
               "медианная относительная." + blocked,
    ), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Показывает, чего не видит балансировщик нагрузки.")
    ap.add_argument("log", type=Path, help="журнал обращений")
    ap.add_argument("--html", type=Path, help="сохранить отчёт в HTML")
    ap.add_argument("--top", type=int, default=8,
                    help="сколько маршрутов показать")
    args = ap.parse_args(argv)

    if not args.log.exists():
        print(f"Файл не найден: {args.log}", file=sys.stderr)
        return 1

    requests = read_log(args.log)
    if len(requests) < 20:
        print("Не удалось разобрать журнал: распознано менее 20 обращений.",
              file=sys.stderr)
        print("Поддерживаются combined-формат nginx с $request_time, "
              "JSON-строки и CSV.", file=sys.stderr)
        return 1

    analysis = analyse(requests)
    print_report(analysis, top=args.top)
    if args.html:
        write_html(analysis, args.html)
        print(f"Отчёт сохранён: {args.html}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
