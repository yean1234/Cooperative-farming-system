from fastapi.testclient import TestClient

from server.main import app
from tests.conftest import FAST_SETTINGS, SMALL_DEMO

client = TestClient(app)


def test_health_and_defaults():
    assert client.get("/api/health").json() == {"status": "ok"}
    d = client.get("/api/defaults").json()
    assert d["crop_order"] == ["벼", "가루쌀", "콩", "조사료"]
    assert d["settings"]["merge_bunds"] is True


def test_demo_then_optimize():
    scn = client.post("/api/scenario/demo", json=SMALL_DEMO).json()
    assert scn["meta"]["synthetic"] is True
    assert len(scn["parcels"]["features"]) == scn["meta"]["n_parcels"]
    res = client.post("/api/optimize", json={"scenario": scn, "settings": FAST_SETTINGS})
    assert res.status_code == 200, res.text
    body = res.json()
    assert [s["key"] for s in body["scenarios"]] == ["S0", "S1", "S2", "S3"]
    assert len(body["scenarios"][3]["assign"]) == len(body["parcel_ids"])


def test_import_feature_collection_without_farmers():
    scn = client.post("/api/scenario/demo", json=SMALL_DEMO).json()
    fc = scn["parcels"]
    res = client.post("/api/scenario/import", json=fc)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["meta"]["warnings"]  # 농가 정보가 없다는 경고
    assert len(body["adjacency"]) == len(scn["adjacency"])


def test_import_rejects_bad_crop():
    scn = client.post("/api/scenario/demo", json=SMALL_DEMO).json()
    scn["parcels"]["features"][0]["properties"]["current_crop"] = "바나나"
    res = client.post("/api/scenario/import", json=scn)
    assert res.status_code == 422
    assert "바나나" in res.json()["detail"]


def test_invalid_crop_bounds_rejected():
    scn = client.post("/api/scenario/demo", json=SMALL_DEMO).json()
    res = client.post(
        "/api/optimize",
        json={"scenario": scn, "settings": {**FAST_SETTINGS, "crop_bounds": {"벼": [0.8, 0.2]}}},
    )
    assert res.status_code == 422
