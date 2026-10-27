import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from server.demo import generate_demo  # noqa: E402

# 테스트는 작은 들녘으로 빠르게 돈다.
SMALL_DEMO = dict(seed=3, n_farmers=12, participation=0.6, fragmentation=0.8, col_pairs=2, rows=8)
FAST_SETTINGS = {"iterations": 4000, "shapley_samples": 30, "time_limit_s": 5.0}


@pytest.fixture(scope="session")
def small_scenario():
    return generate_demo(**SMALL_DEMO)


@pytest.fixture(scope="session")
def small_result(small_scenario):
    from server.pipeline import run_pipeline

    return run_pipeline(small_scenario, FAST_SETTINGS)
