from dataclasses import replace

import numpy as np
import pytest
from fastapi.testclient import TestClient

from server.main import app
from server.region_demo import generate_region
from server.selection import Complex, Region, SiteSettings, parcel_terms, solve
from server.selection_eval import _is_connected, exact_window, validate, window_around


@pytest.fixture(scope="module")
def region():
    return Region(generate_region(seed=11))


@pytest.fixture(scope="module")
def result(region):
    return solve(region, SiteSettings())


def test_every_complex_satisfies_all_constraints(region, result):
    s = SiteSettings()
    T = parcel_terms(region, s)
    assert result["complexes"], "가상 읍면에서 단지가 하나 이상 나와야 한다"
    for c in result["complexes"]:
        members = [region.ids.index(p) for p in c["parcels"]]
        C = Complex(region, T, s)
        for i in members:
            C.add(i)
        assert C.violations() == []
        assert _is_connected(region, members)  # C4
        assert all(T["eligible"][i] for i in members)  # C6·지목


def test_parcel_in_at_most_one_complex(region, result):
    seen = {}
    for c in result["complexes"]:
        for p in c["parcels"]:
            assert p not in seen  # C1
            seen[p] = c["id"]
    assign = result["assign"]
    for pid, k in seen.items():
        assert assign[region.ids.index(pid)] == k


def test_every_parcel_has_a_reason(result):
    assert len(result["reasons"]) == len(result["parcel_ids"])
    assert all(r for r in result["reasons"])
    assert set(result["status"]) <= {"in", "eligible", "ineligible"}


def test_income_is_a_range_not_a_point(result):
    for c in result["complexes"]:
        lo, mid, hi = c["income_musd"]
        assert lo < mid < hi


def test_stricter_slope_never_admits_steeper_parcels(region):
    res = solve(region, replace(SiteSettings(), slope_max=3.0))
    for c in res["complexes"]:
        assert c["max_slope"] <= 3.0 + 1e-9


def test_raising_min_area_never_creates_smaller_complexes(region):
    res = solve(region, replace(SiteSettings(), a_min_ha=35, a_max_ha=80))
    assert all(c["area_ha"] >= 35 - 1e-6 for c in res["complexes"])


def test_proxy_owner_mode_runs(region):
    res = solve(region, replace(SiteSettings(), owner_mode="proxy"))
    assert all(c["owner_mode"] == "proxy" for c in res["complexes"])


def test_validation_reports_baselines(region):
    v = validate(region, SiteSettings())
    names = [row["method"] for row in v["table"]]
    assert names[0].startswith("제안")
    assert len(names) == 3
    ours = v["table"][0]
    assert ours["feasible"] == ours["complexes"]  # 제안 엔진 단지는 모두 제약 충족
    assert 0 <= v["sensitivity"]["min"] <= v["sensitivity"]["mean"] <= 1


def test_small_window_milp_bounds_heuristic(region):
    s = replace(SiteSettings(), a_min_ha=8, a_max_ha=20)
    center = int(np.argmax(region.truth == 1))
    sub = window_around(region, s, center, 50)
    out = exact_window(region, s, sub, K=2, time_limit=20)
    assert out["milp_value"] is not None
    # MILP는 형상 제약(C7)을 빼고 풀므로 같은 선형 목적값에서 휴리스틱 이상이어야 한다
    assert out["milp_value"] >= out["heuristic_value"] - 1e-6


def test_sites_api_flow():
    client = TestClient(app)
    reg = client.post("/api/sites/demo", json={"seed": 11}).json()
    assert reg["region_id"] and reg["meta"]["n_parcels"] > 500
    res = client.post("/api/sites/solve", json={"region_id": reg["region_id"], "settings": {"a_min_ha": 15}})
    assert res.status_code == 200, res.text
    assert res.json()["summary"]["n_complexes"] >= 1
    bad = client.post("/api/sites/solve", json={"region_id": reg["region_id"], "settings": {"a_min_ha": 30, "a_max_ha": 20}})
    assert bad.status_code == 422
    gone = client.post("/api/sites/solve", json={"region_id": "nope", "settings": {}})
    assert gone.status_code == 404
    fc = reg["parcels"]
    for f in fc["features"]:
        f["properties"].pop("owner", None)
    imp = client.post("/api/sites/import", json=fc).json()
    assert any("대리 지표" in w for w in imp["meta"]["warnings"])
