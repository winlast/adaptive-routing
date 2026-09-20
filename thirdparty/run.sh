#!/usr/bin/env bash
# Поднимает стенд для проверки на чужом ПО.
#
# Плагина docker compose в этой среде нет, поэтому контейнеры
# запускаются по отдельности в общей сети. Смысл тот же, что в
# compose.yml: PostgreSQL с данными, PostgREST поверх него и nginx
# впереди, ведущий журнал обращений со временем ответа.
set -e
DIR="$(cd "$(dirname "$0")" && pwd)"
NET=loadlens-thirdparty
# Сетевые псевдонимы db и api заданы намеренно: так те же имена работают
# и здесь, и в compose.yml, и файл nginx.conf остаётся общим.

# Убрать за собой — отдельной командой: стенд держит память и порт,
# и оставлять его работающим после проверки незачем.
if [[ "${1:-}" == "stop" ]]; then
  docker rm -f ll-db ll-api ll-proxy >/dev/null 2>&1 || true
  docker network rm "$NET" >/dev/null 2>&1 || true
  echo "Стенд PostgREST остановлен."
  exit 0
fi

docker rm -f ll-db ll-api ll-proxy 2>/dev/null || true
docker network rm "$NET" 2>/dev/null || true
docker network create "$NET" >/dev/null

echo "1/3 база данных и наполнение (300 000 строк)…"
docker run -d --name ll-db --network "$NET" --network-alias db \
  -e POSTGRES_PASSWORD=demo -e POSTGRES_DB=demo \
  -v "$DIR/seed.sql:/docker-entrypoint-initdb.d/seed.sql:ro" \
  postgres:16-alpine >/dev/null
for i in $(seq 1 90); do
  docker exec ll-db pg_isready -U postgres -d demo >/dev/null 2>&1 && break
  sleep 2
done
# наполнение идёт уже после того, как база начала отвечать
for i in $(seq 1 90); do
  n=$(docker exec ll-db psql -U postgres -d demo -tAc \
      "select count(*) from orders" 2>/dev/null || echo 0)
  [ "$n" -ge 300000 ] 2>/dev/null && break
  sleep 2
done
echo "    строк в таблице: $n"

echo "2/3 PostgREST…"
docker run -d --name ll-api --network "$NET" --network-alias api \
  -e PGRST_DB_URI=postgres://postgres:demo@db:5432/demo \
  -e PGRST_DB_SCHEMAS=public -e PGRST_DB_ANON_ROLE=web_anon \
  -e PGRST_SERVER_PORT=3000 -e PGRST_DB_MAX_ROWS=100000 \
  postgrest/postgrest:latest >/dev/null
sleep 6

echo "3/3 nginx…"
mkdir -p "$DIR/logs" && : > "$DIR/logs/access.log"
docker run -d --name ll-proxy --network "$NET" -p 127.0.0.1:8500:8500 \
  -v "$DIR/nginx.conf:/usr/local/openresty/nginx/conf/nginx.conf:ro" \
  -v "$DIR/logs:/var/log/nginx" \
  openresty/openresty:alpine >/dev/null
sleep 3

echo -n "проверка: "
curl -s -m 10 "http://127.0.0.1:8500/orders?limit=1" | head -c 90
echo
