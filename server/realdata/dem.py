"""Copernicus DEM GLO-30(30m 수치표면모델)에서 필지 평균 경사를 구한다. 흙토람 경사 등급이 없을 때 쓰는 대체값이다.

출처: AWS 공개 버킷 copernicus-dem-30m(키 불필요). 1°×1° Cloud Optimized GeoTIFF를 HTTP 범위 요청으로 필요한 타일만 받는다.
저작권 표기: Copernicus WorldDEM-30 © DLR e.V. 2010-2014 and © Airbus Defence and Space GmbH 2014-2018,
provided under COPERNICUS by the European Union and ESA.
한계: 30m 격자의 표면모델(건물·수목 포함)이라 작은 필지의 미세 경사나 계단식 논두렁은 못 잡는다.
"""
from __future__ import annotations

import math
import struct
import zlib

import numpy as np

from . import http

URL = "https://copernicus-dem-30m.s3.amazonaws.com/{n}/{n}.tif"
NAME = "Copernicus_DSM_COG_10_{ns}{lat:02d}_00_{ew}{lon:03d}_00_DEM"


def _tile_name(lat_floor: int, lon_floor: int) -> str:
    return NAME.format(ns="N" if lat_floor >= 0 else "S", lat=abs(lat_floor), ew="E" if lon_floor >= 0 else "W", lon=abs(lon_floor))


def _ifd0(url: str) -> dict:
    head = http.get_range(url, 0, 65535)
    if head[:2] != b"II":
        raise ValueError("리틀엔디언 GeoTIFF가 아닙니다.")
    off = struct.unpack_from("<I", head, 4)[0]
    n = struct.unpack_from("<H", head, off)[0]
    size = {3: 2, 4: 4, 12: 8}
    fmt = {3: "H", 4: "I", 12: "d"}
    tags = {}
    for i in range(n):
        tag, typ, cnt = struct.unpack_from("<HHI", head, off + 2 + 12 * i)
        if typ not in fmt:
            continue
        nbytes = size[typ] * cnt
        pos = off + 2 + 12 * i + 8 if nbytes <= 4 else struct.unpack_from("<I", head, off + 2 + 12 * i + 8)[0]
        tags[tag] = struct.unpack_from("<" + fmt[typ] * cnt, head, pos)
    if tags[259][0] != 8 or tags[258][0] != 32:
        raise ValueError("DEFLATE·float32 COG가 아닙니다.")
    return {
        "width": tags[256][0], "height": tags[257][0], "tw": tags[322][0], "th": tags[323][0],
        "offsets": tags[324], "counts": tags[325], "predictor": tags.get(317, (1,))[0],
        "res": tags[33550][:2], "origin": tags[33922][3:5],
    }


def _decode(buf: bytes, tw: int, th: int, predictor: int) -> np.ndarray:
    raw = np.frombuffer(zlib.decompress(buf), dtype=np.uint8)
    if predictor == 3:  # 부동소수 예측자: 행마다 바이트 차분을 되돌린 뒤 바이트 평면(MSB부터)을 다시 엮는다
        rows = np.cumsum(raw.reshape(th, 4 * tw), axis=1, dtype=np.uint8)
        return np.ascontiguousarray(rows.reshape(th, 4, tw).transpose(0, 2, 1)).view(">f4").reshape(th, tw).astype(np.float32)
    if predictor == 1:
        return raw.view("<f4").reshape(th, tw)
    raise ValueError(f"지원하지 않는 예측자 {predictor}")


def fetch_grid(bbox_lonlat, margin_px: int = 3):
    """bbox를 덮는 표고 격자(행: 북→남)와 좌상단 경위도·픽셀 크기(도)를 돌려준다."""
    lon0, lat0, lon1, lat1 = bbox_lonlat
    if math.floor(lon0) != math.floor(lon1) or math.floor(lat0) != math.floor(lat1):
        raise ValueError("구역이 1°×1° DEM 타일 경계를 넘습니다. 구역을 나눠 주세요.")
    url = URL.format(n=_tile_name(math.floor(lat0), math.floor(lon0)))
    meta = _ifd0(url)
    rx, ry = meta["res"]
    ox, oy = meta["origin"]
    c0 = max(0, int((lon0 - ox) / rx) - margin_px)
    c1 = min(meta["width"] - 1, int((lon1 - ox) / rx) + margin_px)
    r0 = max(0, int((oy - lat1) / ry) - margin_px)
    r1 = min(meta["height"] - 1, int((oy - lat0) / ry) + margin_px)
    tw, th = meta["tw"], meta["th"]
    across = math.ceil(meta["width"] / tw)
    grid = np.zeros((r1 - r0 + 1, c1 - c0 + 1), dtype=np.float32)
    for tr in range(r0 // th, r1 // th + 1):
        for tc in range(c0 // tw, c1 // tw + 1):
            k = tr * across + tc
            tile = _decode(http.get_range(url, meta["offsets"][k], meta["offsets"][k] + meta["counts"][k] - 1), tw, th, meta["predictor"])
            ya, yb = max(r0, tr * th), min(r1, tr * th + th - 1)
            xa, xb = max(c0, tc * tw), min(c1, tc * tw + tw - 1)
            grid[ya - r0 : yb - r0 + 1, xa - c0 : xb - c0 + 1] = tile[ya - tr * th : yb - tr * th + 1, xa - tc * tw : xb - tc * tw + 1]
    # 픽셀 중심 좌표 기준
    return grid, (ox + (c0 + 0.5) * rx, oy - (r0 + 0.5) * ry), (rx, ry), url


def slope_grid(grid: np.ndarray, lat: float, res_deg) -> np.ndarray:
    """경사(%) 격자. 픽셀 크기를 위도에 맞춰 m로 바꾼다."""
    dx = res_deg[0] * 111_320.0 * math.cos(math.radians(lat))
    dy = res_deg[1] * 110_574.0
    gy, gx = np.gradient(grid.astype(float), dy, dx)
    return 100.0 * np.hypot(gx, gy)


def parcel_slopes(features, bbox_lonlat) -> tuple[list[float], dict]:
    """필지마다 경계 안쪽 표본점(중심 + 꼭짓점을 중심 쪽으로 당긴 점)의 경사 평균(%)."""
    grid, (x0, y0), res, url = fetch_grid(bbox_lonlat)
    lat_mid = 0.5 * (bbox_lonlat[1] + bbox_lonlat[3])
    S = slope_grid(grid, lat_mid, res)
    h, w = S.shape

    def sample(lon, lat):
        fx = np.clip((lon - x0) / res[0], 0, w - 1.001)
        fy = np.clip((y0 - lat) / res[1], 0, h - 1.001)
        ix, iy = fx.astype(int), fy.astype(int)
        ax, ay = fx - ix, fy - iy
        return (
            S[iy, ix] * (1 - ax) * (1 - ay) + S[iy, ix + 1] * ax * (1 - ay)
            + S[iy + 1, ix] * (1 - ax) * ay + S[iy + 1, ix + 1] * ax * ay
        )

    out = []
    for f in features:
        ring = np.asarray(f["geometry"]["coordinates"][0], float)[:-1]
        c = ring.mean(axis=0)
        pts = np.vstack([c[None, :], c + 0.6 * (ring - c)])
        out.append(round(float(sample(pts[:, 0], pts[:, 1]).mean()), 2))
    info = {"url": url, "grid": [int(h), int(w)], "elev_range_m": [round(float(grid.min()), 1), round(float(grid.max()), 1)]}
    return out, info
