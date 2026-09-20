#!/usr/bin/env bash
# Поднимает движки и nginx с маршрутизацией по оценке стоимости.
#
#   nginx/run.sh          — запустить
#   nginx/run.sh stop     — остановить
#
# После запуска:
#   POST http://127.0.0.1:8600/route     — маршрутизация по оценке
#   POST http://127.0.0.1:8600/baseline  — обычная least_conn
#   GET  http://127.0.0.1:8600/status    — что сейчас висит на движках
set -euo pipefail

NET=loadlens
IMAGE=loadlens:latest
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
NAMES=(ll-nginx ll-sync ll-async ll-stub)

stop() {
  for n in "${NAMES[@]}"; do docker rm -f "$n" >/dev/null 2>&1 || true; done
  echo "Остановлено."
}
if [[ "${1:-}" == "stop" ]]; then stop; docker network rm "$NET" >/dev/null 2>&1 || true; exit 0; fi

stop
docker build -q -t "$IMAGE" -f "$ROOT/docker/Dockerfile" "$ROOT" >/dev/null
docker network create "$NET" >/dev/null 2>&1 || docker network inspect "$NET" >/dev/null

run() {
  local name=$1 alias=$2; shift 2
  docker run -d --name "$name" --network "$NET" --network-alias "$alias" "$@" >/dev/null
}

run ll-stub  stub  "$IMAGE" python workers/stub_service.py
run ll-sync  sync  -e STUB_URL=http://stub:8100/wait \
  "$IMAGE" python workers/sync_worker.py
run ll-async async -e STUB_URL=http://stub:8100/wait \
  "$IMAGE" python workers/async_worker.py

# Модель стоимости и модуль монтируются внутрь: сам образ OpenResty
# остаётся немодифицированным, менять его не требуется.
docker run -d --name ll-nginx --network "$NET" --network-alias nginx \
  -p 8600:8600 \
  -e LOADLENS_BUDGET="${LOADLENS_BUDGET:-30}" \
  -v "$ROOT/nginx/loadlens.lua:/etc/loadlens/loadlens.lua:ro" \
  -v "$ROOT/data/power_law.json:/etc/loadlens/power_law.json:ro" \
  -v "$ROOT/nginx/nginx.conf:/usr/local/openresty/nginx/conf/nginx.conf:ro" \
  openresty/openresty:alpine >/dev/null

echo -n "Ожидание готовности"
for _ in $(seq 30); do
  if curl -fs http://127.0.0.1:8600/status >/dev/null 2>&1; then
    echo; curl -s http://127.0.0.1:8600/status; echo
    echo "Готово: http://127.0.0.1:8600"; exit 0
  fi
  echo -n .; sleep 1
done
echo; echo "Не поднялось. Журнал nginx:"; docker logs ll-nginx 2>&1 | tail -20
exit 1
