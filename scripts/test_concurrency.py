import time
import threading
from router import AdaptiveRouter

router = AdaptiveRouter(
    model_path="data/router_model.pth",
    scaler_path="data/scaler.pkl",
    mode="neural",
    log_path="data/concurrency_test_log.csv",
)

N_THREADS = 50
N_CALLS_PER_THREAD = 50
payload = {"load": 80, "io_intensity": 0.2, "cpu_usage": 60}

def worker():
    for _ in range(N_CALLS_PER_THREAD):
        router.decide_backend(payload)

start = time.perf_counter()
threads = [threading.Thread(target=worker) for _ in range(N_THREADS)]
for t in threads:
    t.start()
for t in threads:
    t.join()
elapsed = time.perf_counter() - start

total_calls = N_THREADS * N_CALLS_PER_THREAD
print(f"Всего вызовов: {total_calls}")
print(f"Время: {elapsed:.3f} сек")
print(f"Решений/сек: {total_calls / elapsed:.1f}")