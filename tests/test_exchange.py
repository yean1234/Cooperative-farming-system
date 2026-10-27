import numpy as np
import pytest

from server.exchange import ExchangeSettings, multiparty_exchange, pairwise_exchange
from server.geo import LocalProjection, adjacency
from server.model import Farmer, Field, Parcel


def checkerboard_field(n_rows=6, n_cols=2):
    """두 농가(A, B)의 필지가 바둑판처럼 엇갈린 작은 들녘."""
    proj = LocalProjection(129.0, 36.0)
    parcels, rings = [], []
    k = 0
    for c in range(n_cols):
        for r in range(n_rows):
            x0, y0 = c * 100.0, r * 30.0
            ring = np.array([[x0, y0], [x0 + 100, y0], [x0 + 100, y0 + 30], [x0, y0 + 30]])
            owner = "A" if (r + c) % 2 == 0 else "B"
            parcels.append(
                Parcel(
                    id=f"P{k}", xy=ring, ring_lonlat=[], area=3000.0, cx=x0 + 50, cy=y0 + 15,
                    land_type="답", soil_grade=2, drainage=2, irrigated=True, land_value=30000.0,
                    owner=owner, exchangeable=True, crop_locked=False, current_crop="벼",
                )
            )
            rings.append(ring)
            k += 1
    farmers = {
        "A": Farmer("A", "A", 60, False, np.array([-50.0, 90.0]), 50),
        "B": Farmer("B", "B", 60, False, np.array([250.0, 90.0]), 50),
    }
    return Field(parcels, farmers, np.array([100.0, -50.0]), proj, adjacency(rings), {})


@pytest.fixture()
def field():
    return checkerboard_field()


def bases(field):
    return {fid: f.home for fid, f in field.farmers.items()}


def test_multiparty_consolidates_checkerboard(field):
    owners = [p.owner for p in field.parcels]
    s = ExchangeSettings(iterations=3000, seed=1)
    res = multiparty_exchange(field, owners, [True] * field.n, bases(field), coop_id="COOP", s=s)
    by_id = {c["id"]: c for c in res.cultivators}
    for c in by_id.values():
        assert c["blocks"] <= c["blocks0"]
        assert c["ir_ok"]
        assert abs(c["area_ha"] - c["area0_ha"]) <= 0.10 * c["area0_ha"] + 1e-9
    assert sum(c["blocks"] for c in by_id.values()) < sum(c["blocks0"] for c in by_id.values())
    assert res.stats["ir_violations"] == []


def test_pairwise_only_makes_mutually_beneficial_swaps(field):
    owners = [p.owner for p in field.parcels]
    res = pairwise_exchange(field, owners, [True] * field.n, bases(field), coop_id="COOP", s=ExchangeSettings(seed=0))
    for c in res.cultivators:
        assert c["score"] <= c["score0"] + 1e-9
    # 1:1 교환은 맞바꾸기만 하므로 면적이 정확히 보존된다(필지 면적이 모두 같음).
    for c in res.cultivators:
        assert c["area_ha"] == pytest.approx(c["area0_ha"])


def test_fixed_parcels_never_move(field):
    owners = [p.owner for p in field.parcels]
    exch = [i % 3 != 0 for i in range(field.n)]
    res = multiparty_exchange(field, owners, exch, bases(field), coop_id="COOP", s=ExchangeSettings(iterations=2000))
    for i, (before, after) in enumerate(zip(owners, res.assign)):
        if not exch[i]:
            assert before == after
