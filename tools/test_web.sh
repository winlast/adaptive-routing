#!/usr/bin/env bash
# Проверяет браузерную версию диагностики.
#
# Две проверки, и обе обязательны:
#   1) разбор на JavaScript даёт те же числа, что версия на Python;
#   2) собранная страница действительно строит отчёт.
#
# Node в системе не нужен — запускается в контейнере.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

node_run() { docker run --rm -v "$ROOT:/w" -w /w node:20-alpine node "$@"; }

python3 tools/build_web.py

for log in loadlens/demo_access.log thirdparty/logs/access.log; do
  [[ -f "$log" ]] || { echo "пропуск (нет файла): $log"; continue; }
  python3 - "$log" <<'PY' > /tmp/ll_py.json
import json, sys
sys.path.insert(0, "loadlens")
from loadlens import read_log, analyse
from pathlib import Path
a = analyse(read_log(Path(sys.argv[1])))
print(json.dumps({
  "обращений": a.total,
  "ошибка_по_маршруту": round(a.route_error, 1),
  "ошибка_по_параметру": round(a.param_error, 1),
  "доля_времени_у_тяжёлых": round(a.heavy_share_of_time, 1),
  "маршруты": [{
    "маршрут": r.route, "обращений": r.count,
    "p50": round(r.p50, 1), "p99": round(r.p99, 1),
    "разброс": round(r.spread, 1), "параметр": r.best_param,
    "показатель": round(r.exponent, 2),
    "объясняет": round(r.explained * 100, 1),
    "много_признаков_объясняет": round(r.multi_explained * 100, 1),
    "отложенная_один": round(r.holdout_param_error, 1),
    "отложенная_все": round(r.holdout_multi_error, 1),
  } for r in a.routes],
}, ensure_ascii=False, indent=2))
PY
  node_run loadlens/web/verify.mjs "$log" > /tmp/ll_js.json
  python3 - "$log" <<'PY'
import json, sys
A = json.load(open("/tmp/ll_py.json", encoding="utf-8"))
B = json.load(open("/tmp/ll_js.json", encoding="utf-8"))
diffs = []
def walk(x, y, path=""):
    if isinstance(x, dict):
        for k in x: walk(x[k], (y or {}).get(k), f"{path}.{k}")
    elif isinstance(x, list):
        for i, v in enumerate(x):
            walk(v, y[i] if y and i < len(y) else None, f"{path}[{i}]")
    elif isinstance(x, (int, float)) and isinstance(y, (int, float)):
        if abs(x - y) > 0.05: diffs.append(f"{path}: python={x} js={y}")
    elif x != y: diffs.append(f"{path}: python={x!r} js={y!r}")
walk(A, B)
if diffs:
    print(f"РАСХОЖДЕНИЯ на {sys.argv[1]}:")
    for d in diffs[:10]: print("   ", d)
    raise SystemExit(1)
print(f"{sys.argv[1]}: числа совпадают")
PY
done

node_run loadlens/web/page_test.mjs
