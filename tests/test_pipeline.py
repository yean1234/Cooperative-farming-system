import pytest

from server.metrics import components
from server.model import field_from_scenario
from server.params import COOP_ID, CROP_ORDER, crop_feasible, default_params
from server.zoning import zone_crops


def test_scenarios_preserve_total_area(small_result):
    areas = [s["totals"]["area_ha"] for s in small_result["scenarios"]]
    assert max(areas) - min(areas) < 1e-6


def test_cooperative_scenarios_beat_individual_farming(small_result):
    s0, s1, s2, s3 = small_result["scenarios"]
    assert s1["totals"]["machine_hours"] < s0["totals"]["machine_hours"]
    assert s3["totals"]["blocks"] <= s1["totals"]["blocks"]
    assert s3["totals"]["travel_km"] <= s1["totals"]["travel_km"]


def test_exchange_respects_tolerance_and_individual_rationality(small_result):
    tol = small_result["settings"]["area_tol"]
    for s in small_result["scenarios"][2:]:
        ex = s["exchange"]
        assert ex["stats"]["ir_violations"] == []
        for c in ex["cultivators"]:
            assert abs(c["area_ha"] - c["area0_ha"]) <= tol * c["area0_ha"] + 1e-3
            assert c["ir_ok"]


def test_zoning_respects_feasibility_and_bounds(small_scenario, small_result):
    field = field_from_scenario(small_scenario)
    params = default_params()
    s3 = small_result["scenarios"][3]
    coop = [i for i, a in enumerate(s3["assign"]) if a == COOP_ID and not field.parcels[i].crop_locked]
    total = sum(field.area[i] for i in coop)
    for i in coop:
        assert crop_feasible(s3["crops"][i], field.parcels[i], params)
    if not s3["zoning"]["relaxed"]:
        for c, (lo, hi) in s3["zoning"]["bounds_used"].items():
            share = sum(field.area[i] for i in coop if s3["crops"][i] == c) / total
            assert lo - 1e-6 <= share <= hi + 1e-6


def test_zoning_contiguity_weight_reduces_work_blocks(small_scenario):
    field = field_from_scenario(small_scenario)
    params = default_params()
    members = set(field.members())
    assign = [COOP_ID if p.owner in members else p.owner for p in field.parcels]
    base = [p.current_crop for p in field.parcels]
    bounds = {c: (0.0, 1.0) for c in CROP_ORDER}
    coop_idx = [i for i, a in enumerate(assign) if a == COOP_ID]

    def work_blocks(crops):
        return sum(len(components([i for i in coop_idx if crops[i] == c], field)) for c in {crops[i] for i in coop_idx})

    loose = zone_crops(field, assign, base, params, bounds, contiguity_weight=0.0, seepage_penalty=0.0)
    tight = zone_crops(field, assign, base, params, bounds, contiguity_weight=5000.0, seepage_penalty=0.0)
    assert work_blocks(tight["crops"]) <= work_blocks(loose["crops"])


def test_sharing_covers_all_members(small_result):
    sh = small_result["sharing"]
    ids = {m["id"] for m in sh["members"]}
    assert ids == set(small_result["members"])
    for rule in ("area", "productivity", "mixed", "shapley"):
        total = sum(m["shares"][rule] for m in sh["members"])
        assert total == pytest.approx(sh["coop_net"], rel=1e-6)
