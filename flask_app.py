"""
Простое Flask-приложение с одним эндпоинтом /process.
Эмулирует I/O-операцию синхронным sleep.
"""
import time
import uuid

from flask import Flask, jsonify, request

app = Flask(__name__)


@app.route("/process", methods=["GET", "POST"])
def process():
    if request.method == "POST":
        _ = request.get_data() # Читаем тело

    request_id = request.args.get("id") or str(uuid.uuid4())
    time.sleep(0.1)

    return jsonify({
        "status": "ok",
        "backend": "flask",
        "id": request_id,
    })


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "healthy", "backend": "flask"})


if __name__ == "__main__":
    # threaded=True — чтобы dev-сервер мог обрабатывать несколько запросов
    # одновременно (иначе sleep будет блокировать весь процесс)
    app.run(host="0.0.0.0", port=5000, threaded=True)