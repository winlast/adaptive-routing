"""
Проверка гипотезы на СМЕШАННОЙ нагрузке — в отличие от benchmark.py,
который отправляет одинаковый payload всем трём целям (и потому не
показывает эффекта адаптивной маршрутизации), этот скрипт генерирует
поток из разнородных запросов (часть — "лёгкие", часть — "тяжёлые")
и сравнивает три сценария обработки ОДНОГО И ТОГО ЖЕ смешанного потока.
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

CONCURRENCY_LEVELS = [10, 25, 40]
TOTAL_REQUESTS = {10: 1500, 25: 3000, 40: 4000}

LIGHT_PAYLOAD = {"load": 20, "io_intensity": 0.05, "cpu_usage": 30}
HEAVY_PAYLOAD = {"load": 150, "io_intensity": 0.25, "cpu_usage": 70}

OUTPUT_FILE = DATA_DIR / "benchmark_mixed_results.csv"

SUMMARY_RE = re.compile(
    r"Total:\s+([\d.]+) secs.*?Slowest:\s+([\d.]+) secs.*?"
    r"Fastest:\s+([\d.]+) secs.*?Average:\s+([\d.]+) secs.*?"
    r"Requests/sec:\s+([\d.]+)", re.DOTALL,
)
PERCENTILE_RE = re.compile(r"(\d+)%+\s+in\s+([\d.]+)\s+secs")
STATUS_RE = re.compile(r"\[(\d+)\]\s+(\d+)\s+responses")


def run_hey(url: str, concurrency: int, n_requests: int, payload: dict) -> dict:
    cmd = [
        "hey", "-n", str(n_requests), "-c", str(concurrency), "-t", "15",
        "-m", "POST", "-H", "Content-Type: application/json",
        "-d", json.dumps(payload), url,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
    output = result.stdout
    m = SUMMARY_RE.search(output)
    if not m:
        print(output)
        raise RuntimeError(f"Не удалось распарсить вывод hey для {url}")
    _, _, _, avg, rps = m.groups()
    percentiles = {p: float(v) for p, v in PERCENTILE_RE.findall(output)}
    statuses = dict(STATUS_RE.findall(output))
    successes = int(statuses.get("200", 0))
    return {
        "successes": successes, "failures": n_requests - successes,
        "throughput_rps": float(rps), "avg_latency_ms": float(avg) * 1000,
        "p95_latency_ms": percentiles.get("95", 0) * 1000,
    }


def run_scenario(url: str, concurrency: int, total: int) -> dict:
    half = total // 2
    light = run_hey(url, concurrency, half, LIGHT_PAYLOAD)
    time.sleep(1)
    heavy = run_hey(url, concurrency, half, HEAVY_PAYLOAD)

    total_successes = light["successes"] + heavy["successes"]
    total_failures = light["failures"] + heavy["failures"]
    avg_latency = (light["avg_latency_ms"] + heavy["avg_latency_ms"]) / 2
    p95_latency = max(light["p95_latency_ms"], heavy["p95_latency_ms"])
    throughput = 2 / (1 / light["throughput_rps"] + 1 / heavy["throughput_rps"])

    return {
        "concurrency": concurrency, "total_requests": total,
        "successes": total_successes, "failures": total_failures,
        "throughput_rps": round(throughput, 2),
        "avg_latency_ms": round(avg_latency, 2),
        "p95_latency_ms": round(p95_latency, 2),
    }


def main():
    if shutil.which("hey") is None:
        raise RuntimeError("`hey` не найден в PATH.")

    scenarios = {
        "adaptive": "http://127.0.0.1:9000/route",
        "flask_only": "http://127.0.0.1:5000/process",
        "fastapi_only": "http://127.0.0.1:8000/process",
    }

    rows = []
    for name, url in scenarios.items():
        print(f"\n=== {name} ({url}) ===")
        for c in CONCURRENCY_LEVELS:
            total = TOTAL_REQUESTS[c]
            print(f"  concurrency={c}, requests={total} (смесь light+heavy)...")
            result = run_scenario(url, c, total)
            result["scenario"] = name
            rows.append(result)
            print(f"    throughput={result['throughput_rps']} rps | "
                  f"avg_latency={result['avg_latency_ms']} ms | "
                  f"p95={result['p95_latency_ms']} ms | "
                  f"failures={result['failures']}")
            time.sleep(2)

    DATA_DIR.mkdir(exist_ok=True)
    fieldnames = ["scenario", "concurrency", "total_requests", "successes",
                  "failures", "throughput_rps", "avg_latency_ms", "p95_latency_ms"]
    with open(OUTPUT_FILE, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    print(f"\nСохранено: {OUTPUT_FILE}")


if __name__ == "__main__":
    main()