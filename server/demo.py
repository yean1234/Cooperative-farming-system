"""가상 들녘 생성기(데모용).

실제 지형·소유 정보와 무관한 합성 데이터다. 구조만 한국 평야 들녘을 흉내 낸다.
- 경지정리된 논: 100m×30m(0.3ha) 필지 격자, 농로(6m)·용배수로(2m), 일부 분할·합필 필지
- 서쪽 하천 쪽은 경계가 불규칙하고 배수가 나쁘며, 동쪽 구릉지는 밭(보로노이 필지)이다.
- 농가 소유지는 '상속 핵' 주변과 무작위 위치에 섞여 흩어지도록 배정한다(분산도 조절).
"""
from __future__ import annotations

import math

import numpy as np

from .geo import LocalProjection, adjacency, ring_area, ring_centroid, voronoi_cells

# 포항 흥해 인근을 가정한 가상 기준점. 실제 필지와 겹치지 않는다는 점에 유의.
DEFAULT_ORIGIN = (129.3390, 36.0950)

ROAD, CANAL, PLOT_L, PLOT_W = 6.0, 2.0, 100.0, 30.0


def _paddy_shapes(rng, col_pairs: int, rows: int):
    cols = []
    x = 0.0
    for _ in range(col_pairs):
        x += ROAD
        cols.append((x, x + PLOT_L))
        x += PLOT_L + CANAL
        cols.append((x, x + PLOT_L))
        x += PLOT_L
    width = x + ROAD
    spans_y = []
    y = 0.0
    for r in range(rows):
        if r > 0 and r % 8 == 0:
            y += ROAD
        spans_y.append((y, y + PLOT_W))
        y += PLOT_W
    height = y

    def west_cut(yv: float) -> float:
        return 9.0 + 7.0 * math.sin(yv / 55.0) + 4.0 * math.sin(yv / 17.0 + 1.3)

    shapes = []
    for ci, (x0, x1) in enumerate(cols):
        r = 0
        while r < rows:
            y0, y1 = spans_y[r]
            u = rng.random()
            same_block_next = r + 1 < rows and (r + 1) % 8 != 0
            if u < 0.12 and same_block_next:
                spans = [(y0, spans_y[r + 1][1])]
                r += 2
            elif u < 0.30:
                ym = (y0 + y1) / 2.0
                spans = [(y0, ym), (ym, y1)]
                r += 1
            else:
                spans = [(y0, y1)]
                r += 1
            for a, b in spans:
                if ci == 0:
                    ring = np.array([[x0 + west_cut(a), a], [x1, a], [x1, b], [x0 + west_cut(b), b]])
                else:
                    ring = np.array([[x0, a], [x1, a], [x1, b], [x0, b]])
                shapes.append((ring, "답"))
    return shapes, width, height


def _upland_shapes(rng, x0: float, x1: float, y0: float, y1: float, n_pts: int):
    pts = np.column_stack([rng.uniform(x0, x1, n_pts), rng.uniform(y0, y1, n_pts)])
    for _ in range(2):  # Lloyd 완화로 셀 크기를 고르게
        cells = voronoi_cells(pts, (x0, y0, x1, y1))
        pts = np.array([ring_centroid(c) if len(c) >= 3 else p for c, p in zip(cells, pts)])
    cells = voronoi_cells(pts, (x0, y0, x1, y1))
    return [(c, "전") for c in cells if len(c) >= 3 and ring_area(c) > 300.0]


def generate_demo(
    seed: int = 7,
    n_farmers: int = 30,
    participation: float = 0.6,
    fragmentation: float = 0.75,
    col_pairs: int = 4,
    rows: int = 16,
    origin=DEFAULT_ORIGIN,
) -> dict:
    rng = np.random.default_rng(seed)
    shapes, width, height = _paddy_shapes(rng, col_pairs, rows)
    ux0, ux1 = width + 15.0, width + 15.0 + 240.0
    shapes += _upland_shapes(rng, ux0, ux1, 40.0, height - 30.0, n_pts=max(12, int(rows * 2.2)))
    village = np.array([ux1 + 110.0, height * 0.55])
    depot = np.array([ux1 + 45.0, height * 0.42])

    n = len(shapes)
    cent = np.array([ring_centroid(s) for s, _ in shapes])
    area = np.array([ring_area(s) for s, _ in shapes])
    paddy = np.array([lt == "답" for _, lt in shapes])

    # 배수: 서쪽(하천 쪽 저지대) 불량 → 동쪽 양호. 밭은 양호.
    dscore = cent[:, 0] / width + rng.normal(0.0, 0.12, n)
    drainage = np.where(dscore < 0.30, 3, np.where(dscore < 0.65, 2, 1))
    drainage[~paddy] = 1

    # 토양 적성등급: 완만하게 변하는 공간장 + 잡음을 분위수로 끊는다.
    k = 5
    bumps = rng.uniform([0.0, 0.0], [ux1, height], size=(k, 2))
    amps = rng.uniform(-1.0, 1.0, k)
    sig = rng.uniform(120.0, 260.0, k)
    fld = sum(
        amps[t] * np.exp(-((cent[:, 0] - bumps[t, 0]) ** 2 + (cent[:, 1] - bumps[t, 1]) ** 2) / (2 * sig[t] ** 2))
        for t in range(k)
    ) + rng.normal(0.0, 0.15, n)
    q1, q2, q3 = np.quantile(fld, [0.15, 0.50, 0.82])
    soil = np.where(fld >= q3, 1, np.where(fld >= q2, 2, np.where(fld >= q1, 3, 4)))

    irrigated = paddy & (rng.random(n) > 0.04)

    # 공시지가(원/㎡): 마을에 가까울수록 조금 높다.
    dist_v = np.hypot(cent[:, 0] - village[0], cent[:, 1] - village[1])
    base_val = np.where(paddy, 33_000.0, 27_000.0)
    land_value = np.round(base_val * (1 + 0.15 * (1 - dist_v / dist_v.max())) * rng.normal(1.0, 0.06, n), -2)

    # 농가: 고령 농가 비중이 높다(2025 농림어업총조사 기준 농가인구 65세 이상 51.3%).
    u = rng.random(n_farmers)
    age = np.where(
        u < 0.20,
        rng.integers(45, 60, n_farmers),
        np.where(u < 0.55, rng.integers(60, 70, n_farmers), rng.integers(70, 86, n_farmers)),
    )
    labor = np.where(age < 60, 100, np.where(age < 70, 60, 15)) + rng.integers(-5, 6, n_farmers)
    size = rng.lognormal(0.0, 0.55, n_farmers) * np.where(age < 60, 1.7, np.where(age < 70, 1.1, 0.8))
    target = size / size.sum() * area.sum()

    # 소유지 배정: 상속 핵 근처(집중) + 무작위(분산)를 fragmentation 비율로 섞는다.
    nuclei = [cent[rng.choice(n, size=1 + int(rng.poisson(1.0)), replace=False)] for _ in range(n_farmers)]
    remaining = target.copy()
    owner = np.full(n, -1)
    for p in rng.permutation(n):
        aff = np.array([math.exp(-np.min(np.hypot(nu[:, 0] - cent[p, 0], nu[:, 1] - cent[p, 1])) / 150.0) for nu in nuclei])
        mix = (1.0 - fragmentation) * aff / aff.sum() + fragmentation / n_farmers
        w = mix * np.clip(remaining, 0.0, None)
        if w.sum() <= 0:
            w = mix * target
        f = int(rng.choice(n_farmers, p=w / w.sum()))
        owner[p] = f
        remaining[f] -= area[p]
    for f in range(n_farmers):
        if not (owner == f).any():
            donor = int(np.bincount(owner, minlength=n_farmers).argmax())
            owner[int(rng.choice(np.flatnonzero(owner == donor)))] = f

    # 조합원: 고령 농가일수록 참여 확률이 높다고 가정
    n_members = int(round(n_farmers * participation))
    wt = np.where(age >= 70, 3.0, np.where(age >= 60, 2.0, 1.0))
    members = set(rng.choice(n_farmers, size=n_members, replace=False, p=wt / wt.sum()).tolist()) if n_members else set()

    # 현재 작목
    crops = []
    for i in range(n):
        r = rng.random()
        if paddy[i]:
            if drainage[i] == 1 and r < 0.15:
                crops.append("콩")
            elif r < 0.03:
                crops.append("조사료")
            else:
                crops.append("벼" if irrigated[i] else ("콩" if drainage[i] <= 2 else "조사료"))
        else:
            crops.append("시설채소" if r < 0.15 else ("콩" if r < 0.70 else "조사료"))
    locked = np.array([c == "시설채소" for c in crops])
    exchangeable = ~locked & ~(paddy & (rng.random(n) < 0.05))

    proj = LocalProjection(*origin)
    features = []
    for i, (ring, lt) in enumerate(shapes):
        ll = np.round(proj.to_lonlat(ring), 7).tolist()
        ll.append(ll[0])
        features.append(
            {
                "type": "Feature",
                "id": f"P{i + 1:03d}",
                "geometry": {"type": "Polygon", "coordinates": [ll]},
                "properties": {
                    "id": f"P{i + 1:03d}",
                    "land_type": lt,
                    "soil_grade": int(soil[i]),
                    "drainage": int(drainage[i]),
                    "irrigated": bool(irrigated[i]),
                    "land_value": float(land_value[i]),
                    "owner": f"F{owner[i] + 1:02d}",
                    "exchangeable": bool(exchangeable[i]),
                    "crop_locked": bool(locked[i]),
                    "current_crop": crops[i],
                },
            }
        )

    farmers = []
    for f in range(n_farmers):
        ang = rng.uniform(0, 2 * math.pi)
        rad = rng.uniform(10.0, 80.0)
        home = village + rad * np.array([math.cos(ang), math.sin(ang)])
        farmers.append(
            {
                "id": f"F{f + 1:02d}",
                "name": f"농가 {f + 1:02d}",
                "age": int(age[f]),
                "member": f in members,
                "home": np.round(proj.to_lonlat(home)[0], 7).tolist(),
                "labor_days": float(max(5, labor[f])),
            }
        )

    edges = adjacency([s for s, _ in shapes])
    member_area = float(sum(area[i] for i in range(n) if owner[i] in members))
    return {
        "meta": {
            "name": "가상 들녘 (데모)",
            "synthetic": True,
            "description": "해커톤 데모용 합성 데이터입니다. 실제 필지·소유자와 무관합니다.",
            "origin": list(origin),
            "seed": seed,
            "n_farmers": n_farmers,
            "participation": participation,
            "fragmentation": fragmentation,
            "village": np.round(proj.to_lonlat(village)[0], 7).tolist(),
            "total_area_ha": round(float(area.sum()) / 1e4, 2),
            "member_area_ha": round(member_area / 1e4, 2),
            "n_parcels": n,
            "n_members": len(members),
        },
        "parcels": {"type": "FeatureCollection", "features": features},
        "farmers": farmers,
        "depot": np.round(proj.to_lonlat(depot)[0], 7).tolist(),
        "adjacency": [[i, j, length, touch] for i, j, length, touch in edges],
    }
