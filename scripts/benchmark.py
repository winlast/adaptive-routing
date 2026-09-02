"""
Нагрузочное тестирование через `hey`.

ВАЖНО: диапазон нагрузки ограничен уровнями, при которых текущая
реализация gateway работает стабильно (подтверждено экспериментально —
при concurrency > 50 gateway упирается в однопоточное выполнение
decide_backend() и деградирует нелинейно). Это задокументированное
архитектурное ограничение, описанное в разделе "Ограничения" отчёта.
"""
import csv
import json
import re
import shutil
import subprocess
import time
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
BASE_DIR = SCRIPTS_DIR.parent
DATA_DIR = BASE_DIR / "data"

TARGETS = {
    "flask": "http://127.0.0.1:5000/process",
    "fastapi": "http://127.0.0.1:8000/process",
    "adaptive": "http://127.0.0.1:9000/route",
}

CONCURRENCY_LEVELS = [10, 25, 40, 50]

TOTAL_REQUESTS = {
    10: 1500,
    25: 3000,
    40: 4000,
    50: 5000,
}

PAYLOAD = {"load": 80, "io_intensity": 0.2, "cpu_usage": 60}
OUTPUT_FILE = DATA_DIR / "benchmark_results.csv"

SUMMARY_RE = re.compile(
    r"Total:\s+([\d.]+) secs.*?"
    r"Slowest:\s+([\d.]+) secs.*?"
    r"Fastest:\s+([\d.]+) secs.*?"
    r"Average:\s+([\d.]+) secs.*?"
    r"Requests/sec:\s+([\d.]+)",
    re.DOTALL,
)
# ИСПРАВЛЕНО: `%+` вместо `%` — некоторые сборки hey печатают "10%%"
# (двойной знак процента), из-за чего percentile раньше не парсился
# и всегда сохранялся как None.
PERCENTILE_RE = re.compile(r"(\d+)%+\s+in\s+([\d.]+)\s+secs")
STATUS_RE = re.compile(r"\[(\d+)\]\s+(\d+)\s+responses")


def check_hey_installed():
    if shutil.which("hey") is None:
        raise RuntimeError("`hey` не найден в PATH.")


def run_hey(url: str, concurrency: int, total_requests: int) -> dict:
    cmd = [
        "hey",
        "-n", str(total_requests),
        "-c", str(concurrency),
        "-t", "15",
        "-m", "POST",
        "-H", "Content-Type: application/json",
        "-d", json.dumps(PAYLOAD),
        url,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
    output = result.stdout

    summary_match = SUMMARY_RE.search(output)
    if not summary_match:
        print(output)
        raise RuntimeError(f"Не удалось распарсить вывод hey для {url} (concurrency={concurrency})")

    total_sec, slowest, fastest, avg, rps = summary_match.groups()
    percentiles = {p: float(v) for p, v in PERCENTILE_RE.findall(output)}
    statuses = dict(STATUS_RE.findall(output))
    successes = int(statuses.get("200", 0))
    failures = total_requests - successes

    return {
        "concurrency": concurrency,
        "total_requests": total_requests,
        "successes": successes,
        "failures": failures,
        "throughput_rps": round(float(rps), 2),
        "avg_latency_ms": round(float(avg) * 1000, 2),
        "p95_latency_ms": round(percentiles.get("95", 0) * 1000, 2) if "95" in percentiles else None,
        "p99_latency_ms": round(percentiles.get("99", 0) * 1000, 2) if "99" in percentiles else None,
    }


def main(engines=None):
    check_hey_installed()
    engines = engines or list(TARGETS.keys())

    rows = []
    for engine in engines:
        url = TARGETS[engine]
        print(f"\n=== {engine} ({url}) ===")
        for concurrency in CONCURRENCY_LEVELS:
            total_requests = TOTAL_REQUESTS[concurrency]
            print(f"  concurrency={concurrency}, requests={total_requests} ...")
            result = run_hey(url, concurrency, total_requests)
            result["engine"] = engine
            rows.append(result)
            print(f"    throughput={result['throughput_rps']} rps | "
                  f"avg_latency={result['avg_latency_ms']} ms | "
                  f"p95={result['p95_latency_ms']} ms | "
                  f"failures={result['failures']}")
            time.sleep(2)

    DATA_DIR.mkdir(exist_ok=True)
    fieldnames = ["engine", "concurrency", "total_requests", "successes", "failures",
                  "throughput_rps", "avg_latency_ms", "p95_latency_ms", "p99_latency_ms"]
    with open(OUTPUT_FILE, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    print(f"\nСохранено: {OUTPUT_FILE}")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--engines", nargs="+", default=list(TARGETS.keys()),
                         choices=list(TARGETS.keys()))
    args = parser.parse_args()
    main(args.engines)