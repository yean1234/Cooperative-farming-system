"""작목 단지화 배치. 혼합정수계획(MILP)을 SciPy의 HiGHS 솔버로 푼다.

법인이 경작하는 필지마다 작목 하나를 고른다.

  최대화  Σ 필지 소득(토양·배수 반영) + 전략작물직불금
        + λ · Σ 같은 작목끼리 맞닿은 경계 길이        (단지화 보상: 기계 작업 연속성)
        − μ · Σ 담수 작목과 밭 작목이 논두렁을 맞댄 길이 (습해 위험)
  제약    필지당 작목 1개, 재배 적합성(지목·관개·배수), 작목별 면적 비율 [하한, 상한]

같은 작목 인접 보상은 z ≤ x_i, z ≤ x_j 로 선형화하고,
습해 위험은 u ≥ x_i(담수) + x_j(밭) − 1 로 선형화한다.
"""
from __future__ import annotations

import time

import numpy as np
from scipy.optimize import Bounds, LinearConstraint, milp
from scipy.sparse import coo_matrix

from .params import COOP_ID, CROP_ORDER, crop_feasible, crop_water, revenue_parts

NEAR_WEIGHT = 0.6  # 농로·수로를 사이에 둔 인접은 단지화 보상을 60%만 인정


def _pair_water(a: str, b: str) -> bool:
    return {a, b} == {"wet", "dry"}


def zone_crops(
    field,
    cultivator,
    base_crops,
    params,
    bounds: dict,
    contiguity_weight: float = 1000.0,
    seepage_penalty: float = 1500.0,
    time_limit: float = 10.0,
    mip_gap: float = 0.005,
    coop_id: str = COOP_ID,
) -> dict:
    t0 = time.perf_counter()
    crops = list(base_crops)
    flex = [i for i in range(field.n) if cultivator[i] == coop_id and not field.parcels[i].crop_locked]
    allowed = {i: [c for c in CROP_ORDER if crop_feasible(c, field.parcels[i], params)] for i in flex}
    flex = [i for i in flex if allowed[i]]
    if not flex:
        return {"crops": crops, "status": "배치할 법인 필지가 없습니다.", "seconds": 0.0, "relaxed": False}
    flex_set = set(flex)

    obj: list[float] = []
    var: dict[tuple[int, str], int] = {}
    for i in flex:
        for c in allowed[i]:
            gm, dp = revenue_parts(c, field.parcels[i], params)
            var[(i, c)] = len(obj)
            obj.append(gm + dp)
    n_x = len(obj)

    # 배치 대상이 아닌 이웃(법인의 고정 작목 필지, 다른 경작자 필지)은 상수로 보고 선형항에 반영
    for i in flex:
        for j, length, touch in field.nbrs[i]:
            if j in flex_set:
                continue
            cj = crops[j]
            if cultivator[j] == coop_id and (i, cj) in var:
                obj[var[(i, cj)]] += contiguity_weight * length * (1.0 if touch else NEAR_WEIGHT)
            if touch:
                wj = crop_water(cj, params)
                for c in allowed[i]:
                    if _pair_water(crop_water(c, params), wj):
                        obj[var[(i, c)]] -= seepage_penalty * length

    rows: list[int] = []
    cols: list[int] = []
    vals: list[float] = []
    lb: list[float] = []
    ub: list[float] = []

    def add_row(entries, lo, hi):
        r = len(lb)
        for col, v in entries:
            rows.append(r)
            cols.append(col)
            vals.append(v)
        lb.append(lo)
        ub.append(hi)

    for i in flex:
        add_row([(var[(i, c)], 1.0) for c in allowed[i]], 1.0, 1.0)

    for i, j, length, touch in field.edges:
        if i not in flex_set or j not in flex_set:
            continue
        w = length * (1.0 if touch else NEAR_WEIGHT)
        for c in CROP_ORDER:
            if (i, c) in var and (j, c) in var:
                z = len(obj)
                obj.append(contiguity_weight * w)
                add_row([(z, 1.0), (var[(i, c)], -1.0)], -np.inf, 0.0)
                add_row([(z, 1.0), (var[(j, c)], -1.0)], -np.inf, 0.0)
        if touch:
            wet_i = [var[(i, c)] for c in allowed[i] if crop_water(c, params) == "wet"]
            dry_i = [var[(i, c)] for c in allowed[i] if crop_water(c, params) == "dry"]
            wet_j = [var[(j, c)] for c in allowed[j] if crop_water(c, params) == "wet"]
            dry_j = [var[(j, c)] for c in allowed[j] if crop_water(c, params) == "dry"]
            pairs = [(wet_i, dry_j), (dry_i, wet_j)]
            if any(a and b for a, b in pairs):
                u = len(obj)
                obj.append(-seepage_penalty * length)
                for a, b in pairs:
                    if a and b:
                        add_row([(k, 1.0) for k in a + b] + [(u, -1.0)], -np.inf, 1.0)

    total_area = float(sum(field.area[i] for i in flex))
    bounds_used = {}
    area_rows = []
    for c in CROP_ORDER:
        entries = [(var[(i, c)], float(field.area[i])) for i in flex if (i, c) in var]
        if not entries:
            continue
        lo, hi = bounds.get(c, (0.0, 1.0))
        avail = sum(v for _, v in entries) / total_area
        lo_eff = min(float(lo), avail)
        bounds_used[c] = [round(lo_eff, 4), float(hi)]
        area_rows.append(len(lb))
        add_row(entries, lo_eff * total_area, float(hi) * total_area)

    def solve(lb_, ub_):
        n = len(obj)
        a_mat = coo_matrix((vals, (rows, cols)), shape=(len(lb_), n)).tocsr()
        integrality = np.zeros(n)
        integrality[:n_x] = 1
        return milp(
            -np.asarray(obj),
            constraints=LinearConstraint(a_mat, np.asarray(lb_), np.asarray(ub_)),
            integrality=integrality,
            bounds=Bounds(0.0, 1.0),
            options={"time_limit": float(time_limit), "mip_rel_gap": float(mip_gap), "disp": False},
        )

    res = solve(lb, ub)
    relaxed = False
    if res.x is None:
        # 면적 비율 조건을 동시에 만족할 수 없으면 비율 제약을 풀고 다시 푼다.
        lb2, ub2 = list(lb), list(ub)
        for r in area_rows:
            lb2[r], ub2[r] = -np.inf, np.inf
        res = solve(lb2, ub2)
        relaxed = True

    if res.x is not None:
        x = res.x
        for i in flex:
            crops[i] = max(allowed[i], key=lambda c: x[var[(i, c)]])
        status = "최적해" if res.status == 0 else "시간 제한 내 최선해"
    else:
        for i in flex:
            crops[i] = max(allowed[i], key=lambda c: sum(revenue_parts(c, field.parcels[i], params)))
        status = "솔버 실패: 필지별 최고 소득 작목으로 대체"

    # 결과 분해
    revenue = sum(sum(revenue_parts(crops[i], field.parcels[i], params)) for i in flex)
    contiguity = seepage = 0.0
    for i, j, length, touch in field.edges:
        ci, cj = cultivator[i] == coop_id, cultivator[j] == coop_id
        if not (ci or cj):
            continue
        if ci and cj and crops[i] == crops[j] and crops[i] in CROP_ORDER and (i in flex_set or j in flex_set):
            contiguity += contiguity_weight * length * (1.0 if touch else NEAR_WEIGHT)
        if touch and (i in flex_set or j in flex_set) and _pair_water(crop_water(crops[i], params), crop_water(crops[j], params)):
            seepage += seepage_penalty * length
    crop_area = {c: round(float(sum(field.area[i] for i in flex if crops[i] == c)) / 1e4, 3) for c in CROP_ORDER}
    return {
        "crops": crops,
        "status": status,
        "relaxed": relaxed,
        "mip_gap": None if res.x is None else (None if getattr(res, "mip_gap", None) is None else float(res.mip_gap)),
        "objective": None if res.x is None else float(-res.fun),
        "breakdown": {
            "revenue": revenue,
            "contiguity_bonus": contiguity,
            "seepage_penalty": seepage,
        },
        "flex_area_ha": round(total_area / 1e4, 3),
        "crop_area_ha": crop_area,
        "bounds_used": bounds_used,
        "n_vars": len(obj),
        "n_binary": n_x,
        "n_constraints": len(lb),
        "seconds": round(time.perf_counter() - t0, 3),
    }
