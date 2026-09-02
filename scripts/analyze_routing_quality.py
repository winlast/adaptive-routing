# scripts/analyze_routing_quality.py
"""
Изолированная проверка: даёт ли выбор роутера более быстрый backend-ответ
для каждого конкретного запроса, если исключить накладные расходы шлюза
(двойной сетевой хоп, однопоточное решение). Не заменяет основной вывод
о throughput всей системы — это дополнительный, узкий вопрос о качестве
самого решения классификатора.
"""
import requests
import statistics
import random

N_REQUESTS = 300
GATEWAY_URL = "http://127.0.0.1:9000/route"

LIGHT = {"load": 20, "io_intensity": 0.05, "cpu_usage": 30}
HEAVY = {"load": 150, "io_intensity": 0.25, "cpu_usage": 70}

backend_times = {"flask": [], "fastapi": []}

for _ in range(N_REQUESTS):
    payload = random.choice([LIGHT, HEAVY])
    resp = requests.post(GATEWAY_URL, json=payload, timeout=15)
    data = resp.json()
    backend_times[data["backend"]].append(data["backend_call_time_ms"])

for backend, times in backend_times.items():
    if times:
        print(f"{backend}: n={len(times)}, avg={statistics.mean(times):.2f} ms, "
              f"median={statistics.median(times):.2f} ms")