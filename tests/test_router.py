import sys
from pathlib import Path

import pytest

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
sys.path.insert(0, str(BASE_DIR))

from router import AdaptiveRouter  # noqa: E402


@pytest.fixture(scope="module")
def router():
    return AdaptiveRouter(
        model_path=str(DATA_DIR / "router_model.pth"),
        scaler_path=str(DATA_DIR / "scaler.pkl"),
        mode="neural",
        log_path=str(DATA_DIR / "test_decisions_log.csv"),  # отдельный лог для тестов
    )


@pytest.mark.parametrize("data,expected", [
    # io_intensity >= 0.15 -> задача I/O-bound -> оптимален FastAPI (см. workload.py)
    ({"load": 120, "io_intensity": 0.25, "cpu_usage": 45.0}, "fastapi"),
    ({"load": 30, "io_intensity": 0.20, "cpu_usage": 80.0}, "fastapi"),
    # io_intensity < 0.15 -> задача CPU-bound -> оптимален Flask
    ({"load": 150, "io_intensity": 0.05, "cpu_usage": 10.0}, "flask"),
])
def test_known_cases(router, data, expected):
    result = router.decide_backend(data)
    assert result["backend"] == expected


def test_defaults_on_missing_fields(router):
    result = router.decide_backend({})
    assert result["backend"] in ("flask", "fastapi")


def test_cpu_usage_clipping(router):
    features = router._extract_features({"cpu_usage": 500})
    assert features["cpu_usage"] == 100.0
    features = router._extract_features({"cpu_usage": -50})
    assert features["cpu_usage"] == 0.0


def test_mode_override(router):
    result = router.decide_backend({"load": 200}, mode_override="heuristic")
    assert result["mode"] == "heuristic"
    assert result["probability"] is None