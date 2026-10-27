"""시나리오 평가 모델.

경작 블록, 농기계 작업시간·비용, 물꼬·생육 순회 관리, 소득을 계산한다.
작업시간 = 필지 작업(포장효율 반영) + 블록 진입·세팅 + 블록 내 필지 이동 + 블록 간 도로 이동.
필지의 크기·형태·분산이 작업시간을 좌우한다는 Gonzalez et al.(2007)의 구조를 단순화했다.
"""
from __future__ import annotations

import math
from collections import defaultdict

import numpy as np

from .params import COOP_ID, crop_water, revenue_parts
from .routing import tour


def components(idxs, field, same=None, touch_only=False) -> list[list[int]]:
    """idxs 안에서 인접 그래프의 연결 요소(=블록).

    same이 있으면 same 값이 같은 간선만 쓴다. touch_only면 논두렁을 직접 맞댄 간선만 쓴다.
    """
    pool = set(idxs)
    nbr = field.nbr_touch if touch_only else field.nbr_idx
    seen: set[int] = set()
    comps = []
    for s in idxs:
        if s in seen:
            continue
        seen.add(s)
        stack, comp = [s], []
        while stack:
            u = stack.pop()
            comp.append(u)
            for v in nbr[u]:
                if v in pool and v not in seen and (same is None or same[v] == same[u]):
                    seen.add(v)
                    stack.append(v)
        comps.append(comp)
    return comps


def field_efficiency(a_ha, ops: dict):
    """필지 면적에 따른 포장효율. 작은 필지일수록 회전·두렁 작업 손실이 커진다."""
    return ops["eta_max"] * a_ha / (a_ha + ops["eta_half_ha"])


def januszewski(areas) -> float:
    """집단화 지수 K = sqrt(합계 면적) / (각 블록 면적 제곱근의 합). 한 덩어리면 1, 흩어질수록 0에 가깝다."""
    a = np.asarray(areas, dtype=float)
    a = a[a > 0]
    if len(a) == 0:
        return 0.0
    return float(math.sqrt(a.sum()) / np.sqrt(a).sum())


def area_centroid(field, idxs) -> np.ndarray:
    a = field.area[idxs]
    return np.array([np.average(field.cx[idxs], weights=a), np.average(field.cy[idxs], weights=a)])


SUM_KEYS = [
    "area_ha",
    "parcels",
    "blocks",
    "work_blocks",
    "plots",
    "machine_hours",
    "machine_cost",
    "travel_km",
    "setups",
    "patrol_hours",
    "patrol_cost",
    "gross_margin",
    "direct_payment",
    "overhead",
    "rent_net",
    "net_income",
]


def evaluate(
    field,
    cultivator,
    crops,
    params,
    bases,
    coop_id=COOP_ID,
    with_detail=True,
    merge_bunds_for=(),
    owner_entity=None,
) -> dict:
    """경작자 배정(cultivator)과 작목(crops)으로 들녘 전체 지표를 계산한다.

    merge_bunds_for에 든 경작자는 같은 작목으로 논두렁을 맞댄 필지를 한 포장으로 정비해
    작업한다고 본다(공동영농 기반정비·대구획화 가정). 포장효율이 큰 면적 기준으로 올라간다.
    owner_entity는 필지별 '권리 주체'(조합원 필지는 법인)다. 경작자와 다르면 임차료를 정산한다.
    """
    if owner_entity is None:
        owner_entity = cultivator
    rent = field.qvalue * ops_rent(params)
    rent_paid: dict[str, float] = defaultdict(float)
    rent_recv: dict[str, float] = defaultdict(float)
    for i, (c, o) in enumerate(zip(cultivator, owner_entity)):
        if c != o:
            rent_paid[c] += rent[i]
            rent_recv[o] += rent[i]
    ops = params["ops"]
    machines = params["machines"]
    detour = ops["detour"]
    rev_gm = np.zeros(field.n)
    rev_dp = np.zeros(field.n)
    for i, p in enumerate(field.parcels):
        rev_gm[i], rev_dp[i] = revenue_parts(crops[i], p, params)

    groups: dict[str, list[int]] = defaultdict(list)
    for i, c in enumerate(cultivator):
        groups[c].append(i)

    per: dict[str, dict] = {}
    blocks_out, routes_out, work_out = [], [], []
    for cid, idxs in groups.items():
        is_coop = cid == coop_id
        base = bases.get(cid)
        if base is None:
            base = area_centroid(field, idxs)
        setup_min = ops["setup_min_coop"] if is_coop else ops["setup_min_individual"]

        comps = components(idxs, field)
        comp_areas = [float(field.area[c].sum()) for c in comps]
        if with_detail:
            for c, a in zip(comps, comp_areas):
                blocks_out.append(
                    {"cultivator": cid, "parcels": [field.ids[i] for i in c], "area_ha": round(a / 1e4, 3)}
                )

        by_crop: dict[str, list[int]] = defaultdict(list)
        for i in idxs:
            by_crop[crops[i]].append(i)

        merge = cid in merge_bunds_for
        m_hours = m_cost = travel_km = 0.0
        setups = work_blocks = n_plots = 0
        for crop, cidxs in by_crop.items():
            cdef = params["crops"][crop]
            if not cdef["ops"]:
                continue
            wcomps = components(cidxs, field)
            work_blocks += len(wcomps)
            plots = components(cidxs, field, touch_only=True) if merge else [[i] for i in cidxs]
            n_plots += len(plots)
            cents = np.array([area_centroid(field, c) for c in wcomps])
            if with_detail:
                cents_ll = field.to_lonlat(cents)
                for c, ll in zip(wcomps, cents_ll):
                    work_out.append(
                        {
                            "cultivator": cid,
                            "crop": crop,
                            "parcels": [field.ids[i] for i in c],
                            "area_ha": round(float(field.area[c].sum()) / 1e4, 3),
                            "centroid": ll,
                        }
                    )
            order, length = tour(base, cents)
            road_km = length * detour / 1000.0
            a_ha = np.array([field.area[pl].sum() for pl in plots]) / 1e4
            eff = field_efficiency(a_ha, ops)
            for op in cdef["ops"]:
                passes = op["passes"]
                field_h = float((a_ha / (op["capacity_ha_h"] * eff)).sum())
                setup_h = len(wcomps) * setup_min / 60.0
                trans_h = (len(plots) - len(wcomps)) * ops["transition_min"] / 60.0
                travel_h = road_km / ops["road_speed_kmh"]
                t = passes * (field_h + setup_h + trans_h + travel_h)
                m_hours += t
                m_cost += t * machines[op["machine"]]["cost_per_h"]
                travel_km += passes * road_km
                setups += passes * len(wcomps)
            if with_detail:
                path = np.vstack([base, cents[order], base]) if len(order) else np.vstack([base, base])
                routes_out.append(
                    {
                        "cultivator": cid,
                        "crop": crop,
                        "path": field.to_lonlat(path),
                        "length_km": round(road_km, 3),
                        "blocks": len(wcomps),
                    }
                )

        patrol_h = 0.0
        for water in ("wet", "dry"):
            widx = [
                i
                for i in idxs
                if crop_water(crops[i], params) == water and params["crops"][crops[i]]["patrol_visits"] > 0
            ]
            if not widx:
                continue
            visits = max(params["crops"][crops[i]]["patrol_visits"] for i in widx)
            pcomps = components(widx, field)
            cents = np.array([area_centroid(field, c) for c in pcomps])
            _, plen = tour(base, cents)
            per_visit = plen * detour / 1000.0 / ops["patrol_speed_kmh"] + len(pcomps) * ops["patrol_stop_min"] / 60.0
            patrol_h += visits * per_visit
        patrol_cost = patrol_h * ops["labor_cost_per_h"]

        area = float(field.area[idxs].sum())
        overhead = ops["coop_overhead_per_ha"] * area / 1e4 if is_coop else 0.0
        gm = float(rev_gm[idxs].sum())
        dp = float(rev_dp[idxs].sum())
        rent_net = rent_recv.get(cid, 0.0) - rent_paid.get(cid, 0.0)
        crop_area: dict[str, float] = defaultdict(float)
        for i in idxs:
            crop_area[crops[i]] += field.area[i] / 1e4
        per[cid] = {
            "area_ha": area / 1e4,
            "value": float(field.value[idxs].sum()),
            "parcels": len(idxs),
            "blocks": len(comps),
            "work_blocks": work_blocks,
            "plots": n_plots,
            "frag_index": januszewski(comp_areas),
            "machine_hours": m_hours,
            "machine_cost": m_cost,
            "travel_km": travel_km,
            "setups": setups,
            "patrol_hours": patrol_h,
            "patrol_cost": patrol_cost,
            "gross_margin": gm,
            "direct_payment": dp,
            "overhead": overhead,
            "rent_net": rent_net,
            "net_income": gm + dp - m_cost - patrol_cost - overhead + rent_net,
            "crop_area_ha": dict(crop_area),
        }

    mixed = seep = 0.0
    for i, j, length, touch in field.edges:
        if cultivator[i] != cultivator[j]:
            mixed += length
        if touch and {crop_water(crops[i], params), crop_water(crops[j], params)} == {"wet", "dry"}:
            seep += length

    totals = subtotal(per, list(per))
    totals["mixed_boundary_m"] = mixed
    totals["seepage_m"] = seep
    out = {"totals": totals, "by_cultivator": per}
    if with_detail:
        out["blocks"] = blocks_out
        out["work_blocks"] = work_out
        out["routes"] = routes_out
    return out


def ops_rent(params) -> float:
    return float(params["ops"].get("rent_rate", 0.0))


def subtotal(per: dict, ids) -> dict:
    """경작자 묶음의 합계. 집단화 지수는 면적 가중 평균."""
    tot = {k: 0.0 for k in SUM_KEYS}
    crop_area: dict[str, float] = defaultdict(float)
    w = fi = 0.0
    for cid in ids:
        rec = per.get(cid)
        if rec is None:
            continue
        for k in SUM_KEYS:
            tot[k] += rec[k]
        for c, a in rec["crop_area_ha"].items():
            crop_area[c] += a
        w += rec["area_ha"]
        fi += rec["frag_index"] * rec["area_ha"]
    tot["frag_index"] = fi / w if w else 0.0
    tot["crop_area_ha"] = dict(crop_area)
    tot["mean_block_ha"] = tot["area_ha"] / tot["blocks"] if tot["blocks"] else 0.0
    return tot
