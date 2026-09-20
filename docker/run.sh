#!/usr/bin/env bash
# Поднимает весь стенд: внешний сервис, два движка, маршрутизатор и
# живую демонстрацию. Ничего, кроме Docker, ставить не нужно.
#
#   docker/run.sh          — собрать и запустить
#   docker/run.sh stop     — остановить и убрать
#
# После запуска демонстрация открывается на http://127.0.0.1:8400
set -euo pipefail

NET=loadlens
IMAGE=loadlens:latest
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# ll-gateway остаётся в списке уборки: в прежней схеме шлюз был
# отдельным контейнером, и он держал бы сеть занятой.
NAMES=(ll-demo ll-gateway ll-sync ll-async ll-stub)

stop() {
  for n in "${NAMES[@]}"; do docker rm -f "$n" >/dev/null 2>&1 || true; done
  docker network rm "$NET" >/dev/null 2>&1 || true
  echo "Стенд остановлен."
}

if [[ "${1:-}" == "stop" ]]; then stop; exit 0; fi

stop
echo "Сборка образа…"
docker build -q -t "$IMAGE" -f "$ROOT/docker/Dockerfile" "$ROOT"
docker network create "$NET" >/dev/null 2>&1 \
  || docker network inspect "$NET" >/dev/null

run() {  # имя сетевое_имя [аргументы docker...] -- команда
  local name=$1 alias=$2; shift 2
  docker run -d --name "$name" --network "$NET" --network-alias "$alias" \
    "$@" >/dev/null
}

# Внешний сервис, к которому движки ходят по сети: I/O настоящий.
run ll-stub stub "$IMAGE" python workers/stub_service.py

# Два движка. Синхронный раздаёт запросы по потокам, асинхронный держит
# один цикл событий — в этом вся разница, которую использует маршрутизатор.
run ll-sync sync "$IMAGE" python workers/sync_worker.py
run ll-async async "$IMAGE" python workers/async_worker.py

# Живая демонстрация. Маршрутизатор она поднимает сама, рядом с собой:
# переключение режима — это перезапуск шлюза с другой политикой, и
# держать его отдельным контейнером значило бы дать демонстрации право
# перезапускать соседа.
run ll-demo demo -p 8400:8400 \
  -e GATEWAY_HOST=127.0.0.1 \
  -e SYNC_URL=http://sync:8201/process \
  -e ASYNC_URL=http://async:8202/process \
  -e STUB_URL=http://stub:8100/wait \
  "$IMAGE" python demo/live_demo.py

echo -n "Ожидание готовности"
for _ in $(seq 40); do
  if curl -fs http://127.0.0.1:8400/ >/dev/null 2>&1; then
    echo; echo "Готово: http://127.0.0.1:8400"; exit 0
  fi
  echo -n .; sleep 1
done
echo; echo "Не поднялось. Журналы:"; docker logs ll-gateway 2>&1 | tail -20
exit 1
