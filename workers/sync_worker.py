"""
Синхронный воркер: Flask на threaded-сервере, поток на запрос.

Сильная сторона — CPU-задачи не останавливают обработку остальных
запросов: GIL переключается между потоками, поэтому все соединения
продвигаются, пусть и с конкуренцией за процессор.

Слабая сторона — каждый одновременный запрос стоит отдельного потока ОС,
поэтому массовое I/O-ожидание обходится дороже, чем event loop.
"""
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from flask import Flask, jsonify, request

from core.workload import ensure_database, run_sync

app = Flask(__name__)
WORKER_NAME = "sync"

# Фоновая нагрузка, отбирающая у движка процессорное время. Нужна, чтобы
# воспроизвести обычную для реальных развёртываний ситуацию: на той же
# машине появился сосед, и ёмкость движка упала, хотя код его не
# менялся. Ухудшение затрагивает только этот движок, поэтому меняется
# относительный порядок движков, и однажды снятая таблица стоимостей
# перестаёт быть верной.
noise = {"threads": 0, "stop": None}


def _burn(stop_event: threading.Event) -> None:
    value = 0
    while not stop_event.is_set():
        for i in range(20_000):
            value = (value * 31 + i) % 1_000_003
        time.sleep(0)


@app.route("/process", methods=["POST"])
def process():
    payload = request.get_json(silent=True) or {}
    endpoint = payload.get("endpoint")
    result = run_sync(endpoint, payload.get("param"))
    result["worker"] = WORKER_NAME
    return jsonify(result)


@app.route("/noise", methods=["POST"])
def set_noise():
    """Включает или выключает фоновую нагрузку на этот движок."""
    threads = int(request.args.get("threads", 0))
    if noise["stop"] is not None:
        noise["stop"].set()
        noise["stop"] = None
    noise["threads"] = threads
    if threads > 0:
        stop_event = threading.Event()
        noise["stop"] = stop_event
        for _ in range(threads):
            threading.Thread(target=_burn, args=(stop_event,),
                             daemon=True).start()
    return jsonify({"noise_threads": threads})


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "healthy", "worker": WORKER_NAME,
                    "noise_threads": noise["threads"]})


if __name__ == "__main__":
    ensure_database()
    app.run(host="127.0.0.1", port=8201, threaded=True)
