"""최적화 파이프라인.

현황 진단(S0) → 공동영농·교환 없음(S1) → 1:1 교환(S2) → 다자간 교환(S3)
각 공동영농 시나리오마다 작목 단지화 배치를 풀고, 같은 평가 모델로 비교한 뒤
S3 기준으로 조합원 수익 배분을 계산한다.
"""
from __future__ import annotations

import time

from .exchange import ExchangeSettings, multiparty_exchange, pairwise_exchange
from .metrics import evaluate, subtotal
from .model import field_from_scenario
from .params import COOP_ID, COOP_NAME, DEFAULT_CROP_BOUNDS, default_params
from .sharing import distribute
from .zoning import zone_crops

SCENARIO_LABELS = {
    "S0": "개별영농 (현황)",
    "S1": "공동영농 · 교환 없음",
    "S2": "공동영농 + 1:1 교환",
    "S3": "공동영농 + 다자간 교환 (제안)",
}

DEFAULT_SETTINGS = {
    "area_tol": 0.10,
    "value_tol": 0.10,
    "iterations": 40000,
    "seed": 0,
    "crop_bounds": DEFAULT_CROP_BOUNDS,
    "contiguity_weight": 1000.0,
    "seepage_penalty": 1500.0,
    "time_limit_s": 10.0,
    "shapley_samples": 150,
    "labor_share": 0.2,
    "w_block": 1.0,
    "w_disp": 0.5,
    "w_base": 0.2,
    "coop_weight": 2.0,
    "w_change": 0.15,
    "merge_bunds": True,
}

# 공동영농 확산 지원사업(2026 시범) 요건: 농업인 5명 이상, 경작 면적 20ha 이상
PILOT_MIN_AREA_HA = 20.0
PILOT_MIN_MEMBERS = 5


def _round(obj, nd=3):
    if isinstance(obj, float):
        return round(obj, nd)
    if isinstance(obj, dict):
        return {k: _round(v, nd) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_round(v, nd) for v in obj]
    return obj


def run_pipeline(scenario: dict, settings: dict | None = None) -> dict:
    t_all = time.perf_counter()
    st = dict(DEFAULT_SETTINGS)
    st.update({k: v for k, v in (settings or {}).items() if v is not None})
    params = default_params()
    timings: dict[str, float] = {}

    t = time.perf_counter()
    field = field_from_scenario(scenario)
    timings["load"] = time.perf_counter() - t

    members = field.members()
    member_set = set(members)
    owners = [p.owner for p in field.parcels]
    coop_assign = [COOP_ID if o in member_set else o for o in owners]
    base_crops = [p.current_crop for p in field.parcels]
    exchangeable = [p.exchangeable for p in field.parcels]
    bases = {fid: f.home for fid, f in field.farmers.items()}
    bases[COOP_ID] = field.depot

    ex_settings = ExchangeSettings(
        area_tol=float(st["area_tol"]),
        value_tol=float(st["value_tol"]),
        w_block=float(st["w_block"]),
        w_disp=float(st["w_disp"]),
        w_base=float(st["w_base"]),
        coop_weight=float(st["coop_weight"]),
        w_change=float(st["w_change"]),
        iterations=int(st["iterations"]),
        seed=int(st["seed"]),
    )
    zoning_kw = dict(
        bounds={k: tuple(v) for k, v in st["crop_bounds"].items()},
        contiguity_weight=float(st["contiguity_weight"]),
        seepage_penalty=float(st["seepage_penalty"]),
        time_limit=float(st["time_limit_s"]),
    )

    # 법인 경작지는 같은 작목으로 맞닿은 필지의 논두렁을 정비해 한 포장으로 작업한다고 가정(옵션)
    merge_for = {COOP_ID} if st.get("merge_bunds", True) else set()

    scenarios = {}

    # S0: 개별영농 현황
    t = time.perf_counter()
    e0 = evaluate(field, owners, base_crops, params, bases, coop_id=None, owner_entity=owners)
    scenarios["S0"] = {"assign": owners, "crops": base_crops, "eval": e0, "group": subtotal(e0["by_cultivator"], members)}
    timings["S0"] = time.perf_counter() - t

    def coop_scenario(key, assign, exchange=None):
        t1 = time.perf_counter()
        z = zone_crops(field, assign, base_crops, params, **zoning_kw)
        ev = evaluate(
            field, assign, z["crops"], params, bases, merge_bunds_for=merge_for, owner_entity=coop_assign
        )
        timings[key] = time.perf_counter() - t1
        rec = {"assign": assign, "crops": z["crops"], "eval": ev, "group": subtotal(ev["by_cultivator"], [COOP_ID])}
        rec["zoning"] = {k: v for k, v in z.items() if k != "crops"}
        if exchange is not None:
            rec["exchange"] = {
                "method": exchange.method,
                "changed": exchange.changed,
                "cultivators": exchange.cultivators,
                "stats": exchange.stats,
            }
        return rec

    scenarios["S1"] = coop_scenario("S1", coop_assign)

    t = time.perf_counter()
    pw = pairwise_exchange(field, coop_assign, exchangeable, bases, s=ex_settings)
    timings["exchange_pairwise"] = time.perf_counter() - t
    scenarios["S2"] = coop_scenario("S2", pw.assign, pw)

    t = time.perf_counter()
    # 1:1 교환으로 성사될 거래는 다자간 설계에도 담길 수 있으므로 그 결과도 시작점으로 쓴다.
    mp = multiparty_exchange(field, coop_assign, exchangeable, bases, s=ex_settings, starts=[None, pw.assign])
    timings["exchange_multiparty"] = time.perf_counter() - t
    scenarios["S3"] = coop_scenario("S3", mp.assign, mp)

    # 수익 배분: S3 법인 순이익을 조합원에게 나눈다. 개별영농 소득은 S0 기준.
    t = time.perf_counter()
    individual = {m: e0["by_cultivator"][m]["net_income"] for m in members if m in e0["by_cultivator"]}
    coop_net = scenarios["S3"]["eval"]["by_cultivator"].get(COOP_ID, {}).get("net_income", 0.0)
    sharing = (
        distribute(
            field,
            members,
            individual,
            coop_net,
            scenarios["S1"]["crops"],
            params,
            samples=int(st["shapley_samples"]),
            labor_share=float(st["labor_share"]),
            seed=int(st["seed"]),
            merge_bunds=bool(merge_for),
        )
        if members
        else None
    )
    timings["sharing"] = time.perf_counter() - t

    out_scen = []
    for key in ["S0", "S1", "S2", "S3"]:
        rec = scenarios[key]
        ev = rec["eval"]
        coop_area = rec["group"]["area_ha"]
        item = {
            "key": key,
            "label": SCENARIO_LABELS[key],
            "assign": rec["assign"],
            "crops": rec["crops"],
            "totals": _round(ev["totals"]),
            "group": _round(rec["group"]),
            "by_cultivator": _round(ev["by_cultivator"]),
            "blocks": ev["blocks"],
            "work_blocks": ev["work_blocks"],
            "routes": ev["routes"],
            "pilot_eligible": key != "S0" and coop_area >= PILOT_MIN_AREA_HA and len(members) >= PILOT_MIN_MEMBERS,
        }
        if "zoning" in rec:
            item["zoning"] = _round(rec["zoning"])
        if "exchange" in rec:
            item["exchange"] = _round(rec["exchange"], 4)
        out_scen.append(item)

    cultivators = {COOP_ID: {"name": COOP_NAME, "member": True, "coop": True}}
    for fid, f in field.farmers.items():
        cultivators[fid] = {"name": f.name, "member": f.member, "age": f.age, "labor_days": f.labor_days, "coop": False}

    timings["total"] = time.perf_counter() - t_all
    return {
        "parcel_ids": field.ids,
        "cultivators": cultivators,
        "members": members,
        "coop_id": COOP_ID,
        "scenarios": out_scen,
        "sharing": _round(sharing, 1) if sharing else None,
        "pilot_rule": {"min_area_ha": PILOT_MIN_AREA_HA, "min_members": PILOT_MIN_MEMBERS},
        "settings": st,
        "warnings": field.meta.get("warnings", []),
        "timings": {k: round(v, 3) for k, v in timings.items()},
    }
