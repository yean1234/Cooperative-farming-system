import struct
import zlib
from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient

from server.geo import UTMK, adjacency, ring_area
from server.main import app
from server.realdata import dem, farmmap, http, soil
from server.realdata.shapefile import PolygonShapefile

REAL = Path(__file__).resolve().parent.parent / "data" / "regions" / "heunghae.json"


def rect(x0, y0, x1, y1):
    return np.array([[x0, y0], [x1, y0], [x1, y1], [x0, y1]], dtype=float)


# ------------------------------------------------------------------ 좌표·인접
def test_utmk_origin_and_round_trip():
    u = UTMK()
    assert u.to_xy([[127.5, 38.0]])[0] == pytest.approx([1_000_000.0, 2_000_000.0], abs=1e-3)
    pts = np.array([[129.35, 36.12], [126.98, 37.57], [128.6, 35.87]])
    assert u.to_lonlat(u.to_xy(pts)) == pytest.approx(pts, abs=1e-8)
    # 팜맵 API 명세의 예시 좌표는 경기도 가평군 청평면이다
    lon, lat = u.to_lonlat([[993814.3568818928, 1970429.7755634887]])[0]
    assert 127.3 < lon < 127.5 and 37.65 < lat < 37.8


def test_boundary_is_shared_with_nearest_neighbour_only():
    # 폭 4m 띠 필지가 겹겹이 붙으면 가운데 띠의 경계는 8m 안에 이웃 둘이 다 있다
    strips = [rect(0, 4 * k, 100, 4 * k + 4) for k in range(5)]
    edges = adjacency(strips)
    shared = np.zeros(len(strips))
    for i, j, length, _ in edges:
        shared[i] += length
        shared[j] += length
    perim = np.array([2 * (100 + 4)] * len(strips))
    assert (shared <= perim * 1.02).all()
    assert {(i, j) for i, j, _, _ in edges} >= {(0, 1), (1, 2), (2, 3), (3, 4)}


# ------------------------------------------------------------------ SHP 리더·팜맵 추출
def _write_shapefile(base: Path, polys, rows, fields):
    def header(length_words, bbox):
        return struct.pack(">7i", 9994, 0, 0, 0, 0, 0, length_words) + struct.pack("<2i", 1000, 5) + struct.pack("<4d", *bbox) + b"\0" * 32

    recs = []
    for poly in polys:
        pts = np.vstack([poly, poly[:1]])
        content = struct.pack("<i4d2i", 5, *pts.min(axis=0), *pts.max(axis=0), 1, len(pts)) + struct.pack("<i", 0) + pts.astype("<f8").tobytes()
        recs.append(content)
    allp = np.vstack(polys)
    bbox = (*allp.min(axis=0), *allp.max(axis=0))
    body, index, off = b"", b"", 50
    for k, c in enumerate(recs, start=1):
        body += struct.pack(">2i", k, len(c) // 2) + c
        index += struct.pack(">2i", off, len(c) // 2)
        off += 4 + len(c) // 2
    base.with_suffix(".shp").write_bytes(header(50 + len(body) // 2, bbox) + body)
    base.with_suffix(".shx").write_bytes(header(50 + len(index) // 2, bbox) + index)
    base.with_suffix(".cpg").write_text("EUC-KR", encoding="ascii")
    rlen = 1 + sum(n for _, n in fields)
    hlen = 32 + 32 * len(fields) + 1
    dbf = struct.pack("<B3BIHH20x", 3, 125, 1, 1, len(rows), hlen, rlen)
    for name, n in fields:
        dbf += name.encode("ascii").ljust(11, b"\0") + b"C" + b"\0" * 4 + bytes([n, 0]) + b"\0" * 14
    dbf += b"\r"
    for row in rows:
        dbf += b" " + b"".join(str(row[name]).encode("cp949").ljust(n, b" ") for name, n in fields)
    base.with_suffix(".dbf").write_bytes(dbf + b"\x1a")


def test_farmmap_extract_reads_utmk_shapefile(tmp_path):
    u = UTMK()
    x0, y0 = u.to_xy([[129.350, 36.120]])[0]
    # SHP 외곽 링은 시계 방향
    cw = lambda r: r[::-1]  # noqa: E731
    polys = [cw(rect(x0, y0, x0 + 50, y0 + 40)), cw(rect(x0 + 50, y0, x0 + 100, y0 + 40)), cw(rect(x0, y0 + 40, x0 + 50, y0 + 80))]
    fields = [("ID", 15), ("CLSF_NM", 6), ("LDCG_CD", 4), ("PNU", 19), ("FL_ARMT_YN", 1), ("AREA", 12), ("STDG_ADDR", 50), ("FLIGHT_YMD", 10)]
    rows = [
        {"ID": "A1", "CLSF_NM": "논", "LDCG_CD": "답", "PNU": "4711325021100010000", "FL_ARMT_YN": "Y", "AREA": "2000", "STDG_ADDR": "경상북도 포항시 북구 흥해읍 용천리", "FLIGHT_YMD": "2024-05-01"},
        {"ID": "A2", "CLSF_NM": "밭", "LDCG_CD": "전", "PNU": "4711325021100020000", "FL_ARMT_YN": "N", "AREA": "2000", "STDG_ADDR": "경상북도 포항시 북구 흥해읍 용천리", "FLIGHT_YMD": "2024-05-01"},
        {"ID": "A3", "CLSF_NM": "비경지", "LDCG_CD": "대", "PNU": "4711325021100030000", "FL_ARMT_YN": "", "AREA": "2000", "STDG_ADDR": "경상북도 포항시 북구 흥해읍 용천리", "FLIGHT_YMD": "2024-05-01"},
    ]
    base = tmp_path / "pohang"
    _write_shapefile(base, polys, rows, fields)

    shp = PolygonShapefile(base.with_suffix(".shp"))
    assert shp.n == 3 and shp.records([0])[0]["CLSF_NM"] == "논"
    assert abs(ring_area(shp.rings(1)[0][:-1])) == pytest.approx(2000.0)

    feats, info = farmmap.extract(base.with_suffix(".shp"), (129.34, 36.11, 129.36, 36.13))
    assert info["skipped"]["비경지"] == 1
    props = {f["properties"]["id"]: f["properties"] for f in feats}
    assert props["A1"]["land_type"] == "답" and props["A1"]["consolidated"] is True
    assert props["A2"]["land_type"] == "전" and props["A2"]["land_use"] == "밭"
    ring = np.array(feats[0]["geometry"]["coordinates"][0])
    assert ring[0].tolist() == ring[-1].tolist()  # 닫힌 링
    xy = UTMK().to_xy(ring[:-1])
    assert ring_area(xy) == pytest.approx(2000.0, rel=2e-3)  # 경위도 7자리 반올림 오차


# ------------------------------------------------------------------ 흙토람 코드 변환
def _soil_xml(**codes):
    items = "".join(f"<{k}>{v:02d}</{k}>" for k, v in codes.items())
    return f'<?xml version="1.0" encoding="UTF-8"?><response><header><Result_Code>200</Result_Code><Result_Msg>OK</Result_Msg></header><body><items><item>{items}</item></items></body></response>'.encode()


def test_soil_enrich_maps_codes_and_fills_neighbours(monkeypatch, tmp_path):
    monkeypatch.setattr(http, "CACHE", tmp_path)
    answers = {
        "1": (_soil_xml(Soildra_Cd=2, Rfld_Grd_Cd=1, Pfld_Grd_Cd=3), _soil_xml(Soilslope_Cd=1)),
        "2": (_soil_xml(Soildra_Cd=5, Rfld_Grd_Cd=5, Pfld_Grd_Cd=4), _soil_xml(Soilslope_Cd=3)),
    }
    calls = []

    def fake_get(url, params=None, *, secret=None, ns=None, timeout=60.0):
        calls.append((ns, params["PNU_CD"]))
        pnu = params["PNU_CD"]
        if pnu.endswith("30000"):
            return b'<response><header><Result_Code>301</Result_Code><Result_Msg>OK_NO_DATA</Result_Msg></header><body><items/></body></response>'
        a, b = answers[pnu[-5]]
        return a if "Sctnn" not in url else b

    monkeypatch.setattr(http, "get", fake_get)

    def feat(pnu, lon, land_type="답"):
        ring = [[lon, 36.12], [lon + 0.0005, 36.12], [lon + 0.0005, 36.1204], [lon, 36.1204], [lon, 36.12]]
        return {"geometry": {"type": "Polygon", "coordinates": [ring]}, "properties": {"pnu": pnu, "land_type": land_type}}

    feats = [feat("4711325021100010000", 129.35), feat("4711325021100020000", 129.36, "전"), feat("4711325021100030000", 129.3506)]
    info = soil.enrich(feats, "secret-key", budget=10)
    p0, p1, p2 = (f["properties"] for f in feats)
    assert (p0["drainage"], p0["slope_class"], p0["soil_grade"]) == (1, "0-2%", 1)
    assert (p1["drainage"], p1["drainage_class"], p1["slope"], p1["soil_grade"]) == (3, "불량", 11.0, 4)  # 밭은 밭적성등급
    # 자료 없는 필지는 가장 가까운 조회 필지(60m 옆 p0) 값을 빌린다
    assert p2["drainage"] == 1 and p2["drainage_source"] == "흙토람(인접 필지)"
    assert info["queried"] == 2 and all(ns.startswith("soil") for ns, _ in calls)


def test_soil_quota_error_is_not_cached(monkeypatch, tmp_path):
    monkeypatch.setattr(http, "CACHE", tmp_path)
    url = soil.URL_CHARAC
    body = b"<OpenAPI_ServiceResponse><cmmMsgHeader><returnReasonCode>22</returnReasonCode><returnAuthMsg>LIMITED_NUMBER_OF_SERVICE_REQUESTS_EXCEEDS_ERROR</returnAuthMsg></cmmMsgHeader></OpenAPI_ServiceResponse>"
    cp = http._cache_path("soil_charac", http._get_ident(url, {"PNU_CD": "1"}), ".bin")
    cp.parent.mkdir(parents=True)
    cp.write_bytes(body)
    with pytest.raises(soil.QuotaExceeded):
        soil._call(url, "1", "k", "soil_charac")
    assert not cp.exists()


# ------------------------------------------------------------------ DEM 타일 복호화
def test_dem_decodes_floating_point_predictor():
    rng = np.random.default_rng(0)
    th, tw = 4, 6
    z = (rng.normal(50, 10, (th, tw))).astype(np.float32)
    planes = z.astype(">f4").view(np.uint8).reshape(th, tw, 4).transpose(0, 2, 1).reshape(th, 4 * tw)
    diff = planes.copy()
    diff[:, 1:] = planes[:, 1:] - planes[:, :-1]  # uint8 랩어라운드 차분
    out = dem._decode(zlib.compress(diff.tobytes()), tw, th, 3)
    assert np.array_equal(out, z)


# ------------------------------------------------------------------ 실측 지도 API
@pytest.mark.skipif(not REAL.exists(), reason="data/regions/heunghae.json 없음")
def test_real_region_loads_and_solves():
    client = TestClient(app)
    names = [r["name"] for r in client.get("/api/sites/regions").json()]
    assert "heunghae" in names
    reg = client.post("/api/sites/region", json={"name": "heunghae"}).json()
    meta = reg["meta"]
    assert meta["synthetic"] is False and meta["has_owner"] is False and meta["n_parcels"] > 1000
    assert {s["name"] for s in meta["sources"] if s["status"].get("used")} >= {"팜맵 공간정보(농경지 전자지도)", "OpenStreetMap 도로"}
    res = client.post("/api/sites/solve", json={"region_id": reg["region_id"], "settings": {"owner_mode": "proxy"}}).json()
    assert res["summary"]["n_complexes"] >= 1
    assert all(0 < c["compactness"] <= 1.0 for c in res["complexes"])
    assert res["summary"]["owners_included"] == sum(c["n_owners"] for c in res["complexes"])
    assert client.post("/api/sites/region", json={"name": "../etc"}).status_code == 422
    assert client.post("/api/sites/region", json={"name": "nope"}).status_code == 404
