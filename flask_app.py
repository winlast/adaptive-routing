"""
Flask-приложение с эндпоинтом /process.

В отличие от исходной версии, где оба бэкенда делали идентичный
time.sleep(), здесь тип работы зависит от io_intensity запроса:
  - io_intensity >= IO_INTENSITY_THRESHOLD  -> I/O-ожидание (time.sleep)
  - иначе                                   -> CPU-bound вычисление (busy-loop)

Flask (threaded dev-сервер) обрабатывает каждый запрос в отдельном
OS-потоке. CPU-bound busy-loop в одном потоке не блокирует остальные
запросы: GIL переключается между потоками каждые ~5 мс (sys.getswitchinterval),
поэтому все потоки продвигаются, пусть и с конкуренцией за CPU.
"""
import time

from flask import Flask, jsonify, request

from workload import cpu_bound_work, io_bound_duration, is_io_bound

app = Flask(__name__)


@app.route("/process", methods=["GET", "POST"])
def process():
    if request.method == "POST":
        payload = request.get_json(silent=True) or {}
    else:
        payload = request.args

    io_intensity = float(payload.get("io_intensity", 0.1))
    cpu_usage = float(payload.get("cpu_usage", 50.0))

    if is_io_bound(io_intensity):
        task_type = "io_bound"
        time.sleep(io_bound_duration(io_intensity))
    else:
        task_type = "cpu_bound"
        cpu_bound_work(cpu_usage)

    return jsonify({
        "status": "ok",
        "backend": "flask",
        "task_type": task_type,
    })


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "healthy", "backend": "flask"})


if __name__ == "__main__":
    # threaded=True — чтобы dev-сервер мог обрабатывать несколько запросов
    # одновременно (иначе busy-loop/sleep блокировал бы весь процесс)
    app.run(host="0.0.0.0", port=5000, threaded=True)
