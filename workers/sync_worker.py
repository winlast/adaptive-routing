"""
Синхронный воркер: Flask на threaded-сервере, поток на запрос.

Сильная сторона — CPU-задачи не останавливают обработку остальных
запросов: GIL переключается между потоками, поэтому все соединения
продвигаются, пусть и с конкуренцией за процессор.

Слабая сторона — каждый одновременный запрос стоит отдельного потока ОС,
поэтому массовое I/O-ожидание обходится дороже, чем event loop.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from flask import Flask, jsonify, request

from core.workload import ensure_database, run_sync

app = Flask(__name__)
WORKER_NAME = "sync"


@app.route("/process", methods=["POST"])
def process():
    payload = request.get_json(silent=True) or {}
    endpoint = payload.get("endpoint")
    result = run_sync(endpoint, payload.get("param"))
    result["worker"] = WORKER_NAME
    return jsonify(result)


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "healthy", "worker": WORKER_NAME})


if __name__ == "__main__":
    ensure_database()
    app.run(host="127.0.0.1", port=8201, threaded=True)
