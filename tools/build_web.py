#!/usr/bin/env python3
"""
Собирает одностраничную версию диагностики.

Страница делается самодостаточной намеренно: разбор журнала и сама
страница оказываются в одном файле, который открывается двойным щелчком
и работает без сервера. Так посетителю не нужно ничего ставить, а нам не
нужно просить у него журнал — файл не покидает его машину.

Источник разбора один — loadlens/web/loadlens.js. Он же проверяется на
совпадение с версией на Python, поэтому вставляется сюда как есть, без
правок.
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "loadlens" / "web"

# В примере показывается заметный, но не рекордный кусок: первые
# полторы тысячи строк демонстрационного журнала.
DEMO_LINES = 1500


def main() -> None:
    template = (WEB / "index.template.html").read_text(encoding="utf-8")
    module = (WEB / "loadlens.js").read_text(encoding="utf-8")

    # Страница — один файл, поэтому модуль вставляется телом, а слова
    # export в нём не нужны.
    module = module.replace("export function", "function")

    demo_lines = (ROOT / "loadlens" / "demo_access.log").read_text(
        encoding="utf-8").splitlines()[:DEMO_LINES]
    demo = "\n".join(demo_lines).replace("\\", "\\\\").replace("`", "\\`")
    demo = demo.replace("${", "\\${")

    page = template.replace("/* __LOADLENS_JS__ */", module)
    page = page.replace("__DEMO_LOG__", demo)

    out = WEB / "index.html"
    out.write_text(page, encoding="utf-8")
    size = out.stat().st_size / 1024
    print(f"Собрано: {out} ({size:.0f} КБ, пример из {len(demo_lines)} строк)")


if __name__ == "__main__":
    main()
