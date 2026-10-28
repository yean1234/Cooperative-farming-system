"""OpenStreetMap 도로(Overpass API, 키 불필요)로 필지의 도로 접속(C5)을 판정한다.

농로는 highway=track으로 많이 그려져 있다. 보행 전용 길은 농기계가 못 다니므로 뺀다.
판정: 필지 경계 표본점 중 하나라도 도로 중심선에서 ROAD_TOL m 이내면 접속.
OSM에 없는 농로도 있으므로, 경지정리 필지(팜맵 FL_ARMT_YN=Y)는 구획마다 농로에 닿게 설계되므로 접속으로 본다(build 단계).
데이터 © OpenStreetMap contributors, ODbL 1.0.
"""
from __future__ import annotations

import json

import numpy as np

from ..geo import LocalProjection, _nearest_on_segments, open_ring, sample_boundary
from . import http

OVERPASS = "https://overpass-api.de/api/interpreter"
NOT_FOR_MACHINES = {"footway", "path", "steps", "pedestrian", "cycleway", "bridleway", "corridor", "elevator", "proposed", "construction", "platform"}
ROAD_TOL = 7.0  # 차로 반폭(약 2~3m) + 논두렁·배수로 여유


def fetch_roads(bbox_lonlat, pad_deg: float = 0.002) -> tuple[list[dict], dict]:
    lon0, lat0, lon1, lat1 = bbox_lonlat
    q = f'[out:json][timeout:90];way["highway"]({lat0 - pad_deg},{lon0 - pad_deg},{lat1 + pad_deg},{lon1 + pad_deg});out geom;'
    data = json.loads(http.post_form(OVERPASS, {"data": q}, ns="osm").decode("utf-8"))
    box = (lon0 - pad_deg, lat0 - pad_deg, lon1 + pad_deg, lat1 + pad_deg)
    inside = lambda p: box[0] <= p[0] <= box[2] and box[1] <= p[1] <= box[3]  # noqa: E731
    roads = []
    for e in data.get("elements", []):
        hw = (e.get("tags") or {}).get("highway", "")
        if e.get("type") != "way" or hw in NOT_FOR_MACHINES or len(e.get("geometry") or []) < 2:
            continue
        pts = [[round(p["lon"], 7), round(p["lat"], 7)] for p in e["geometry"]]
        # Overpass는 길 전체를 돌려주므로 구역(+여유) 밖 구간을 잘라 낸다. 경계를 넘는 첫 점은 남긴다.
        run: list = []
        for k, p in enumerate(pts):
            near = inside(p) or (k > 0 and inside(pts[k - 1])) or (k + 1 < len(pts) and inside(pts[k + 1]))
            if near:
                run.append(p)
            elif run:
                if len(run) >= 2:
                    roads.append({"highway": hw, "coords": run})
                run = []
        if len(run) >= 2:
            roads.append({"highway": hw, "coords": run})
    kinds: dict[str, int] = {}
    for r in roads:
        kinds[r["highway"]] = kinds.get(r["highway"], 0) + 1
    return roads, {"n_ways": len(roads), "highway": dict(sorted(kinds.items(), key=lambda x: -x[1])), "timestamp": data.get("osm3s", {}).get("timestamp_osm_base")}


def road_access(features, roads, tol: float = ROAD_TOL) -> list[bool]:
    if not roads:
        return [False] * len(features)
    rings = [np.asarray(f["geometry"]["coordinates"][0], float) for f in features]
    allp = np.vstack(rings)
    proj = LocalProjection(float(allp[:, 0].min()), float(allp[:, 1].min()))
    A, B = [], []
    for r in roads:
        xy = proj.to_xy(r["coords"])
        A.append(xy[:-1])
        B.append(xy[1:])
    A, B = np.vstack(A), np.vstack(B)
    seg_lo, seg_hi = np.minimum(A, B), np.maximum(A, B)
    out = []
    for ring in rings:
        xy = open_ring(proj.to_xy(ring))
        lo, hi = xy.min(axis=0) - tol, xy.max(axis=0) + tol
        cand = np.flatnonzero((seg_hi[:, 0] >= lo[0]) & (seg_lo[:, 0] <= hi[0]) & (seg_hi[:, 1] >= lo[1]) & (seg_lo[:, 1] <= hi[1]))
        if len(cand) == 0:
            out.append(False)
            continue
        P, _ = sample_boundary(xy, step=3.0)
        d, _ = _nearest_on_segments(P, A[cand], B[cand])
        out.append(bool((d <= tol).any()))
    return out
