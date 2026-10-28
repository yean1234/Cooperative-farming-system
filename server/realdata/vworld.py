"""브이월드 연속지적도(LP_PA_CBND_BUBUN)로 지목 '도'(도로)·'구'(구거) 필지와 공시지가를 얻는다.

브이월드 인증키(VWORLD_KEY)와 키 발급 때 적은 서비스 URL(VWORLD_DOMAIN, 기본 http://localhost)이 필요하다.
요청 규칙(2D 데이터 API 2.0 레퍼런스): BOX 필터 면적 2km² 이하, 한 페이지 최대 1,000건.
jibun 속성 끝 글자가 지목이다(예: '123-4답', '56도').
"""
from __future__ import annotations

import math
import os

import numpy as np

from ..geo import LocalProjection, _nearest_on_segments, open_ring, sample_boundary
from . import http

URL = "https://api.vworld.kr/req/data"
TOL_ROAD_PARCEL = 3.0  # 도로 필지와 경계가 이만큼 붙어 있으면 접속


def fetch_parcels(bbox_lonlat, service_key: str, domain: str | None = None, tile_deg: float = 0.009) -> tuple[list[dict], dict]:
    domain = domain or os.environ.get("VWORLD_DOMAIN") or "http://localhost"
    lon0, lat0, lon1, lat1 = bbox_lonlat
    nx, ny = math.ceil((lon1 - lon0) / tile_deg), math.ceil((lat1 - lat0) / tile_deg)
    seen, out, pages = set(), [], 0
    for ix in range(nx):
        for iy in range(ny):
            box = (lon0 + ix * tile_deg, lat0 + iy * tile_deg, min(lon1, lon0 + (ix + 1) * tile_deg), min(lat1, lat0 + (iy + 1) * tile_deg))
            page = 1
            while True:
                params = {
                    "service": "data", "version": "2.0", "request": "GetFeature", "data": "LP_PA_CBND_BUBUN",
                    "format": "json", "size": 1000, "page": page, "crs": "EPSG:4326",
                    "geomFilter": "BOX({:.6f},{:.6f},{:.6f},{:.6f})".format(*box),
                    "columns": "pnu,jibun,jiga,gosi_year,ag_geom",
                }
                body = http.get_json(URL, params, secret={"key": service_key, "domain": domain}, ns="vworld")
                res = body.get("response", {})
                status = res.get("status")
                if status == "ERROR":
                    http.uncache(URL, params, "vworld")
                    err = res.get("error", {})
                    raise RuntimeError(f"브이월드 오류 {err.get('code')}: {err.get('text')} (키와 서비스 URL VWORLD_DOMAIN={domain} 확인)")
                pages += 1
                if status != "OK":
                    break
                for f in res["result"]["featureCollection"]["features"]:
                    pnu = f["properties"].get("pnu")
                    if pnu in seen:
                        continue
                    seen.add(pnu)
                    out.append(f)
                if page >= int(res.get("page", {}).get("total", 1)):
                    break
                page += 1
    return out, {"n_parcels": len(out), "requests": pages}


def jimok(feature) -> str:
    j = (feature["properties"].get("jibun") or "").strip()
    return j[-1] if j and not j[-1].isdigit() else ""


def _outer_rings(geom):
    if geom["type"] == "Polygon":
        return [geom["coordinates"][0]]
    return [p[0] for p in geom["coordinates"]]


def enrich(features, cad_parcels) -> dict:
    """도로 필지 인접 → road_access_cadastral, 대표 PNU 공시지가 → land_value(원/㎡)."""
    price = {}
    for f in cad_parcels:
        p = f["properties"]
        try:
            price[p.get("pnu")] = float(p.get("jiga") or 0) or None
        except ValueError:
            pass
    roads = [r for f in cad_parcels if jimok(f) == "도" for r in _outer_rings(f["geometry"])]
    rings = [np.asarray(f["geometry"]["coordinates"][0], float) for f in features]
    allp = np.vstack(rings)
    proj = LocalProjection(float(allp[:, 0].min()), float(allp[:, 1].min()))
    A, B = [], []
    for r in roads:
        xy = open_ring(proj.to_xy(r))
        A.append(xy)
        B.append(np.roll(xy, -1, axis=0))
    A = np.vstack(A) if A else np.zeros((0, 2))
    B = np.vstack(B) if B else np.zeros((0, 2))
    lo_s, hi_s = np.minimum(A, B), np.maximum(A, B)
    n_road = n_price = 0
    for f, ring in zip(features, rings):
        p = f["properties"]
        v = price.get(p.get("pnu"))
        if v:
            p["land_value"] = v
            n_price += 1
        if len(A) == 0:
            continue
        xy = open_ring(proj.to_xy(ring))
        lo, hi = xy.min(axis=0) - TOL_ROAD_PARCEL, xy.max(axis=0) + TOL_ROAD_PARCEL
        cand = np.flatnonzero((hi_s[:, 0] >= lo[0]) & (lo_s[:, 0] <= hi[0]) & (hi_s[:, 1] >= lo[1]) & (lo_s[:, 1] <= hi[1]))
        hit = False
        if len(cand):
            P, _ = sample_boundary(xy, step=3.0)
            d, _ = _nearest_on_segments(P, A[cand], B[cand])
            hit = bool((d <= TOL_ROAD_PARCEL).any())
        p["road_parcel_adjacent"] = hit
        n_road += hit
    return {"road_parcels": len(roads), "adjacent_to_road_parcel": n_road, "with_land_value": n_price}
