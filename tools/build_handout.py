#!/usr/bin/env python3
"""
Собирает раздаточную карточку к Венчурным играм.

Карточка решает одну задачу: человек, отошедший от стенда, через час
должен вспомнить, о чём был разговор, и иметь возможность проверить
сказанное сам. Поэтому на ней ровно одно утверждение, три числа и
ссылка, по которой проверка занимает минуту.

Все числа берутся из файлов в data/ — это правило проекта. Если файл
изменится, карточка пересоберётся с новыми числами, а не разойдётся с
измерениями.
"""

from __future__ import annotations

import json
from pathlib import Path

import qrcode

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "ledentsov" / "раздатка"
URL = "https://winlast.github.io/adaptive-routing/"
REPO = "https://github.com/winlast/adaptive-routing"


def qr_png(data: str, path: Path, box: int = 10) -> None:
    img = qrcode.QRCode(box_size=box, border=2,
                        error_correction=qrcode.constants.ERROR_CORRECT_M)
    img.add_data(data)
    img.make(fit=True)
    img.make_image(fill_color="black", back_color="white").save(path)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    qr_png(URL, OUT / "qr-диагностика.png")
    qr_png(REPO, OUT / "qr-репозиторий.png")

    cap = json.loads((ROOT / "data" / "nginx_capacity.json")
                     .read_text(encoding="utf-8"))
    eco = json.loads((ROOT / "data" / "unit_economics.json")
                     .read_text(encoding="utf-8"))
    azure = json.loads((ROOT / "data" / "azure_trace.json")
                       .read_text(encoding="utf-8"))
    hand = json.loads((ROOT / "data" / "handmade.json")
                      .read_text(encoding="utf-8"))

    ёмкость_обычная = cap["режимы"]["обычная (least_conn)"][
        "ёмкость_при_пороге"]["100.0"]["рпс"]
    ёмкость_наша = cap["режимы"]["по оценке стоимости"][
        "ёмкость_при_пороге"]["100.0"]["рпс"]
    прирост = round((ёмкость_наша / ёмкость_обычная - 1) * 100)
    экономия = eco["по_порогам"]["100.0"]["разница"]["руб_в_год"]
    доля = azure["разброс_p99_к_p50"]["доля_функций_с_разбросом_от_10_раз_%"]

    быстрый = min(z["медиана_мс"] for z in hand["замеры"])
    медленный = max(z["медиана_мс"] for z in hand["замеры"])

    html = CARD.format(
        прирост=прирост,
        быстрый=f"{быстрый:.1f}".replace(".", ","),
        медленный=f"{медленный:.1f}".replace(".", ","),
        разница=hand["разница_раз"],
        ёмкость_обычная=f"{ёмкость_обычная:.1f}".replace(".", ","),
        ёмкость_наша=f"{ёмкость_наша:.1f}".replace(".", ","),
        экономия=f"{экономия:,}".replace(",", " "),
        доля=f"{доля:.1f}".replace(".", ","),
        url=URL, repo=REPO)
    (OUT / "карточка.html").write_text(html, encoding="utf-8")
    print(f"Собрано в {OUT}:")
    for f in sorted(OUT.iterdir()):
        print(f"  {f.name}  ({f.stat().st_size // 1024} КБ)")
    print(f"\nЧисла: +{прирост} % ёмкости, {экономия} ₽/год, {доля} % обработчиков")


CARD = """<!DOCTYPE html>
<html lang="ru"><head><meta charset="utf-8">
<title>LoadLens — карточка</title>
<style>
@page {{ size: A6 landscape; margin: 0 }}
* {{ box-sizing: border-box }}
body {{ margin:0; font:13px/1.45 "Segoe UI",Roboto,Helvetica,Arial,sans-serif;
  color:#16202b; background:#eef1f4 }}
.card {{ width:148mm; height:105mm; background:#fff; padding:9mm 10mm;
  display:flex; flex-direction:column; margin:0 auto }}
@media print {{ body {{ background:#fff }} .card {{ margin:0 }} }}
h1 {{ font-size:17px; margin:0 0 1mm; letter-spacing:-.02em }}
.lead {{ font-size:12px; color:#41505f; margin:0 0 4mm }}
.lead b {{ color:#c62828 }}
.row {{ display:flex; gap:6mm; flex:1 }}
.nums {{ flex:1; display:flex; flex-direction:column; justify-content:center;
  gap:3.5mm }}
.n {{ display:flex; align-items:baseline; gap:2.5mm }}
.n .v {{ font-size:21px; font-weight:700; letter-spacing:-.02em;
  color:#1565c0; min-width:26mm }}
.n .t {{ font-size:11px; color:#41505f; line-height:1.3 }}
.qr {{ width:32mm; text-align:center; display:flex; flex-direction:column;
  justify-content:center }}
.qr img {{ width:32mm; height:32mm; display:block }}
.qr span {{ font-size:9px; color:#5d6b7a; margin-top:1.5mm; display:block }}
footer {{ border-top:1px solid #dde4ea; margin-top:4mm; padding-top:2.5mm;
  font-size:9.5px; color:#5d6b7a; display:flex; justify-content:space-between }}
</style></head><body>
<div class="card">
  <h1>Балансировщик не знает, сколько стоит запрос</h1>
  <p class="lead">Один и тот же адрес <code>/orders</code>: от {быстрый} мс до
  {медленный} мс — <b>разница в {разница} раза</b>. Веса задаются на маршрут, а различие
  лежит внутри маршрута. Настроить это невозможно в принципе.</p>
  <div class="row">
    <div class="nums">
      <div class="n"><span class="v">+{прирост} %</span><span class="t">нагрузки
        при обещании p95 ≤ 100 мс<br>{ёмкость_обычная} → {ёмкость_наша} запросов в секунду</span></div>
      <div class="n"><span class="v">{экономия} ₽</span><span class="t">в год
        на инфраструктуре<br>при 1000 запросов в секунду</span></div>
      <div class="n"><span class="v">{доля} %</span><span class="t">обработчиков
        в трассе Azure<br>расходятся не меньше чем в 10 раз</span></div>
    </div>
    <div class="qr">
      <img src="qr-диагностика.png" alt="QR">
      <span>Проверьте на своём журнале — он не покинет ваш компьютер</span>
    </div>
  </div>
  <footer>
    <span>Синявский С. Д., МГТУ им. Н. Э. Баумана</span>
    <span>{repo}</span>
  </footer>
</div>
</body></html>
"""


if __name__ == "__main__":
    main()
