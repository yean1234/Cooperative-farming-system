"""가상 읍면 농지 지도 생성기(후보단지 시뮬레이터 데모용).

실제 지형·소유 정보와 무관한 합성 데이터다. 팜맵을 받기 전 시연과 테스트에 쓴다.
- 지형: 언덕(가우시안 봉우리) 몇 개가 있는 2.4km × 1.6km 구역. 경사는 표고 기울기로 계산한다.
- 경사가 완만한 칸은 논이다. 일부는 경지정리된 직사각형 필지, 일부는 미정리 불규칙 필지다.
- 중간 경사는 밭, 과수·시설 필지가 섞인다. 가파른 칸은 산림이라 필지가 없다.
- 도로: 400m 간격 격자 농로. 도로에 맞닿은 필지만 '도로 접속'이다.
- 소유: 마을 4곳 주변으로 농가 150호가 필지를 흩어 갖는다.
- 가상 정답: '이미 조성된 공동영농 단지' 3곳을 규칙으로 심어 둔다. 재현 검증 파이프라인 시연용이다.
"""
from __future__ import annotations

import math

import numpy as np

from .geo import LocalProjection, adjacency, ring_area, ring_centroid, voronoi_cells

ORIGIN = (129.3000, 36.0600)  # 가상 기준점(포항 북부 가정). 실제 필지와 무관
TILE = 200.0
ROAD_EVERY = 400.0
INSET = 3.0


def _elevation(rng, width, height):
    peaks = []
    for _ in range(6):
        peaks.append(
            (
                rng.uniform(0, width),
                rng.uniform(0, height),
                rng.uniform(18, 55),
                rng.uniform(160, 340),
            )
        )

    def elev(x, y):
        z = 0.004 * x + 0.002 * y
        for px, py, amp, sig in peaks:
            z += amp * math.exp(-((x - px) ** 2 + (y - py) ** 2) / (2 * sig * sig))
        return z

    return elev


def _slope_pct(elev, x, y, h=10.0):
    dx = (elev(x + h, y) - elev(x - h, y)) / (2 * h)
    dy = (elev(x, y + h) - elev(x, y - h)) / (2 * h)
    return 100.0 * math.hypot(dx, dy)


def _paddy_rects(rng, x0, y0, x1, y1):
    """경지정리 칸: 두 열 × 여러 행 직사각형 필지(일부 분할·합필)."""
    shapes = []
    xm = (x0 + x1) / 2
    cols = [(x0, xm - 1.0), (xm + 1.0, x1)]
    n_rows = 6
    hgt = (y1 - y0) / n_rows
    for cx0, cx1 in cols:
        r = 0
        while r < n_rows:
            ya, yb = y0 + r * hgt, y0 + (r + 1) * hgt
            u = rng.random()
            if u < 0.12 and r + 1 < n_rows:
                yb = y0 + (r + 2) * hgt
                spans = [(ya, yb)]
                r += 2
            elif u < 0.30:
                ym = (ya + yb) / 2
                spans = [(ya, ym), (ym, yb)]
                r += 1
            else:
                spans = [(ya, yb)]
                r += 1
            for a, b in spans:
                shapes.append(np.array([[cx0, a], [cx1, a], [cx1, b], [cx0, b]]))
    return shapes


def _voronoi(rng, x0, y0, x1, y1, n):
    pts = np.column_stack([rng.uniform(x0, x1, n), rng.uniform(y0, y1, n)])
    for _ in range(2):
        cells = voronoi_cells(pts, (x0, y0, x1, y1))
        pts = np.array([ring_centroid(c) if len(c) >= 3 else p for c, p in zip(cells, pts)])
    return [c for c in voronoi_cells(pts, (x0, y0, x1, y1)) if len(c) >= 3 and ring_area(c) > 250]


def _dist_to_roads(pts: np.ndarray, width, height) -> np.ndarray:
    """격자 농로(ROAD_EVERY 간격)까지의 거리."""
    dx = np.abs(((pts[:, 0] + ROAD_EVERY / 2) % ROAD_EVERY) - ROAD_EVERY / 2)
    dy = np.abs(((pts[:, 1] + ROAD_EVERY / 2) % ROAD_EVERY) - ROAD_EVERY / 2)
    return np.minimum(dx, dy)


def generate_region(seed: int = 11, n_farmers: int = 150, cols: int = 12, rows: int = 8) -> dict:
    rng = np.random.default_rng(seed)
    width, height = cols * TILE, rows * TILE
    elev = _elevation(rng, width, height)

    shapes: list[tuple[np.ndarray, str, str]] = []  # (ring, 지목, 이용)
    for ci in range(cols):
        for ri in range(rows):
            x0, y0 = ci * TILE + INSET, ri * TILE + INSET
            x1, y1 = (ci + 1) * TILE - INSET, (ri + 1) * TILE - INSET
            s = _slope_pct(elev, (x0 + x1) / 2, (y0 + y1) / 2)
            if s < 2.6:
                if rng.random() < 0.72:
                    for ring in _paddy_rects(rng, x0, y0, x1, y1):
                        shapes.append((ring, "답", "논"))
                else:
                    for ring in _voronoi(rng, x0, y0, x1, y1, int(rng.integers(9, 15))):
                        shapes.append((ring, "답", "논"))
            elif s < 11.0:
                for ring in _voronoi(rng, x0, y0, x1, y1, int(rng.integers(6, 11))):
                    u = rng.random()
                    use = "과수" if u < 0.18 else ("시설" if u < 0.25 else "밭")
                    shapes.append((ring, "전" if use != "과수" else "과", use))
            elif rng.random() < 0.25:
                for ring in _voronoi(rng, x0, y0, x1, y1, 3):
                    shapes.append((ring, "과", "과수"))

    n = len(shapes)
    cent = np.array([ring_centroid(r) for r, _, _ in shapes])
    area = np.array([ring_area(r) for r, _, _ in shapes])
    slope = np.array([max(0.0, _slope_pct(elev, x, y) + rng.normal(0, 0.4)) for x, y in cent])
    z = np.array([elev(x, y) for x, y in cent])
    zr = (z - z.min()) / (np.ptp(z) + 1e-9)
    paddy = np.array([lt == "답" for _, lt, _ in shapes])
    dscore = zr + rng.normal(0, 0.08, n)
    drainage = np.where(dscore < 0.18, 3, np.where(dscore < 0.42, 2, 1))
    drainage[~paddy] = np.where(rng.random((~paddy).sum()) < 0.8, 1, 2)
    soil_field = np.sin(cent[:, 0] / 310 + rng.uniform(0, 6)) + np.cos(cent[:, 1] / 260 + rng.uniform(0, 6)) + rng.normal(0, 0.5, n)
    q = np.quantile(soil_field, [0.15, 0.5, 0.82])
    soil = np.where(soil_field >= q[2], 1, np.where(soil_field >= q[1], 2, np.where(soil_field >= q[0], 3, 4)))

    # 도로 접속: 필지 경계 표본점 중 하나라도 농로 4m 이내
    road = np.zeros(n, dtype=bool)
    for i, (ring, _, _) in enumerate(shapes):
        r = np.asarray(ring)
        seg = np.vstack([r, r[:1]])
        pts = np.vstack([seg[:-1] + t * (seg[1:] - seg[:-1]) for t in np.linspace(0, 1, 6)])
        road[i] = bool((_dist_to_roads(pts, width, height) <= INSET + 1.5).any())

    villages = np.array([[width * 0.18, height * 0.25], [width * 0.55, height * 0.78], [width * 0.82, height * 0.30], [width * 0.40, height * 0.45]])
    price = np.where(paddy, 33_000.0, 28_000.0) * rng.normal(1.0, 0.07, n)
    price *= 1 + 0.12 * (1 - np.min(np.hypot(cent[:, None, 0] - villages[None, :, 0], cent[:, None, 1] - villages[None, :, 1]), axis=1) / 1500).clip(0, 1)

    # 소유: 마을 근처 상속 핵 + 무작위
    home_v = rng.integers(0, len(villages), n_farmers)
    homes = villages[home_v] + rng.normal(0, 40, (n_farmers, 2))
    nuclei = homes + rng.normal(0, 350, (n_farmers, 2))
    target = rng.lognormal(0, 0.6, n_farmers)
    target = target / target.sum() * area.sum()
    remaining = target.copy()
    owner = np.zeros(n, dtype=int)
    d_nuc = np.hypot(cent[:, None, 0] - nuclei[None, :, 0], cent[:, None, 1] - nuclei[None, :, 1])
    for p in rng.permutation(n):
        aff = np.exp(-d_nuc[p] / 300.0)
        w = (0.3 * aff / aff.sum() + 0.7 / n_farmers) * np.clip(remaining, 1.0, None)
        f = int(rng.choice(n_farmers, p=w / w.sum()))
        owner[p] = f
        remaining[f] -= area[p]

    proj = LocalProjection(*ORIGIN)
    edges = adjacency([r for r, _, _ in shapes])
    nbrs: list[list[int]] = [[] for _ in range(n)]
    for i, j, _, _ in edges:
        nbrs[i].append(j)
        nbrs[j].append(i)

    # 가상 정답 단지: 평탄한 논에서 시작해 가까운 순서로 넓힌 덩어리(우리 알고리즘과 다른 규칙)
    truth = np.zeros(n, dtype=int)
    ok = (paddy | (np.array([u for _, _, u in shapes]) == "밭")) & (slope <= 6.0)
    cand_seeds = [i for i in np.argsort(slope) if paddy[i] and road[i]]
    made = 0
    for s0 in cand_seeds:
        if made >= 3:
            break
        if truth[s0] or any(truth[j] for j in nbrs[s0]):
            continue
        tgt = rng.uniform(25e4, 45e4)
        members, acc = [s0], area[s0]
        frontier = set(nbrs[s0])
        while acc < tgt and frontier:
            nxt = min(frontier, key=lambda j: math.hypot(*(cent[j] - cent[s0])))
            frontier.discard(nxt)
            if truth[nxt] or not ok[nxt]:
                continue
            members.append(nxt)
            acc += area[nxt]
            frontier.update(k for k in nbrs[nxt] if k not in members)
        if acc >= 20e4:
            made += 1
            for m in members:
                truth[m] = made

    features = []
    for i, (ring, lt, use) in enumerate(shapes):
        ll = np.round(proj.to_lonlat(ring), 7).tolist()
        ll.append(ll[0])
        features.append(
            {
                "type": "Feature",
                "id": f"R{i + 1:04d}",
                "geometry": {"type": "Polygon", "coordinates": [ll]},
                "properties": {
                    "id": f"R{i + 1:04d}",
                    "land_type": lt,
                    "land_use": use,
                    "slope": round(float(slope[i]), 2),
                    "drainage": int(drainage[i]),
                    "soil_grade": int(soil[i]),
                    "road_access": bool(road[i]),
                    "land_value": float(round(price[i], -2)),
                    "owner": f"H{owner[i] + 1:03d}",
                    "truth_complex": int(truth[i]),
                },
            }
        )
    return {
        "meta": {
            "name": "가상 읍면 (데모)",
            "synthetic": True,
            "description": "후보단지 시뮬레이터 시연용 합성 데이터입니다. 실제 필지·소유자·단지와 무관합니다.",
            "origin": list(ORIGIN),
            "seed": seed,
            "n_parcels": n,
            "n_farmers": n_farmers,
            "total_area_ha": round(float(area.sum()) / 1e4, 1),
            "truth_complexes": int(truth.max()),
            "villages": [np.round(proj.to_lonlat(v.reshape(1, 2))[0], 7).tolist() for v in villages],
            "roads": _road_lines(proj, width, height),
        },
        "parcels": {"type": "FeatureCollection", "features": features},
        "adjacency": [[i, j, length, touch] for i, j, length, touch in edges],
    }


def _road_lines(proj, width, height):
    lines = []
    x = 0.0
    while x <= width + 1:
        lines.append(np.round(proj.to_lonlat(np.array([[x, 0], [x, height]])), 7).tolist())
        x += ROAD_EVERY
    y = 0.0
    while y <= height + 1:
        lines.append(np.round(proj.to_lonlat(np.array([[0, y], [width, y]])), 7).tolist())
        y += ROAD_EVERY
    return lines
