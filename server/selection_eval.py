"""후보단지 선정 검증(기획안 4.5 Stage 3).

- 재현 검증: 정답 단지 경계와 비교한 필지 단위 Precision·Recall·F1, 단지별 면적 IoU
- 베이스라인: (a) 공간 KMeans(인접·소유자·도로 제약 무시) (b) 면적순 greedy(최소 면적만 확인)
- 민감도: A_min·n_min·s_max를 흔들었을 때 선정 필지 집합의 Jaccard 안정도
- 정확해 비교: 소규모 구역에서 flow 기반 연결성 제약 MILP(HiGHS)와 휴리스틱의 선형화 목적값 비교
"""
from __future__ import annotations

import math
import time
from dataclasses import replace

import numpy as np
from scipy.optimize import Bounds, LinearConstraint, milp
from scipy.sparse import coo_matrix

from .selection import Region, SiteSettings, linear_value, parcel_terms, solve


# ------------------------------------------------------------------ 재현 지표
def reproduction_metrics(R: Region, assign) -> dict:
    assign = np.asarray(assign)
    truth = R.truth
    if truth.max() == 0:
        return {"available": False}
    pred_pos = assign > 0
    true_pos = truth > 0
    tp = int((pred_pos & true_pos).sum())
    fp = int((pred_pos & ~true_pos).sum())
    fn = int((~pred_pos & true_pos).sum())
    prec = tp / (tp + fp) if tp + fp else 0.0
    rec = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * prec * rec / (prec + rec) if prec + rec else 0.0
    ious = []
    for t in range(1, truth.max() + 1):
        tm = truth == t
        best = 0.0
        for k in set(assign[tm].tolist()) - {0}:
            pm = assign == k
            inter = R.area[tm & pm].sum()
            union = R.area[tm | pm].sum()
            best = max(best, inter / union if union else 0.0)
        ious.append(best)
    return {
        "available": True,
        "precision": round(prec, 3),
        "recall": round(rec, 3),
        "f1": round(f1, 3),
        "iou_mean": round(float(np.mean(ious)), 3),
        "iou_each": [round(x, 3) for x in ious],
    }


# ------------------------------------------------------------------ 베이스라인
def baseline_kmeans(R: Region, s: SiteSettings, k: int, seed: int = 0, iters: int = 40) -> np.ndarray:
    T = parcel_terms(R, s)
    idx = np.flatnonzero(T["eligible"])
    pts = np.column_stack([R.cx[idx], R.cy[idx]])
    w = R.area[idx]
    rng = np.random.default_rng(seed)
    k = max(1, min(k, len(idx)))
    cents = [pts[rng.choice(len(idx), p=w / w.sum())]]
    for _ in range(k - 1):  # k-means++
        d2 = np.min([((pts - c) ** 2).sum(1) for c in cents], axis=0)
        cents.append(pts[rng.choice(len(idx), p=d2 * w / (d2 * w).sum())])
    cents = np.array(cents)
    for _ in range(iters):
        lab = np.argmin(((pts[:, None, :] - cents[None]) ** 2).sum(2), axis=1)
        new = np.array([np.average(pts[lab == j], axis=0, weights=w[lab == j]) if (lab == j).any() else cents[j] for j in range(k)])
        if np.allclose(new, cents):
            break
        cents = new
    assign = np.zeros(R.n, dtype=int)
    kk = 0
    for j in range(k):
        mem = idx[lab == j]
        if R.area[mem].sum() >= s.a_min_ha * 1e4:
            kk += 1
            assign[mem] = kk
    return assign


def baseline_greedy_area(R: Region, s: SiteSettings) -> np.ndarray:
    T = parcel_terms(R, s)
    elig = T["eligible"]
    assign = np.zeros(R.n, dtype=int)
    k = 0
    for seed in np.argsort(-R.area):
        if not elig[seed] or assign[seed]:
            continue
        members, acc = [seed], R.area[seed]
        frontier = {j for j in R.nbrs[seed] if elig[j] and not assign[j]}
        while acc < s.a_min_ha * 1e4 and frontier:
            nxt = min(frontier, key=lambda j: math.hypot(R.cx[j] - R.cx[seed], R.cy[j] - R.cy[seed]))
            frontier.discard(nxt)
            members.append(nxt)
            acc += R.area[nxt]
            frontier.update(j for j in R.nbrs[nxt] if elig[j] and not assign[j] and j not in members)
        if acc >= s.a_min_ha * 1e4:
            k += 1
            assign[members] = k
    return assign


def _check_constraints(R: Region, s: SiteSettings, assign) -> dict:
    """베이스라인 단지가 기획안 제약을 얼마나 어기는지 센다."""
    from .selection import Complex

    T = parcel_terms(R, s)
    n_ok = n_all = 0
    for k in set(np.asarray(assign).tolist()) - {0}:
        C = Complex(R, T, s)
        for i in np.flatnonzero(np.asarray(assign) == k):
            C.add(int(i))
        n_all += 1
        connected = _is_connected(R, C.members)
        if not C.violations() and connected:
            n_ok += 1
    return {"complexes": n_all, "feasible": n_ok}


def _is_connected(R: Region, members) -> bool:
    members = set(members)
    if not members:
        return True
    start = next(iter(members))
    seen, stack = {start}, [start]
    while stack:
        u = stack.pop()
        for v in R.nbrs[u]:
            if v in members and v not in seen:
                seen.add(v)
                stack.append(v)
    return len(seen) == len(members)


# ------------------------------------------------------------------ 민감도
def sensitivity(R: Region, s: SiteSettings, base_assign) -> dict:
    base = set(np.flatnonzero(np.asarray(base_assign) > 0).tolist())
    cases = [
        ("A_min −20%", replace(s, a_min_ha=s.a_min_ha * 0.8)),
        ("A_min +20%", replace(s, a_min_ha=s.a_min_ha * 1.2)),
        ("n_min −2", replace(s, n_min=max(1, s.n_min - 2))),
        ("n_min +2", replace(s, n_min=s.n_min + 2)),
        ("s_max −2%p", replace(s, slope_max=max(0.5, s.slope_max - 2))),
        ("s_max +2%p", replace(s, slope_max=s.slope_max + 2)),
    ]
    rows = []
    for name, ss in cases:
        res = solve(R, ss)
        cur = set(np.flatnonzero(np.asarray(res["assign"]) > 0).tolist())
        union = base | cur
        jac = len(base & cur) / len(union) if union else 1.0
        rows.append({"case": name, "jaccard": round(jac, 3), "n_complexes": res["summary"]["n_complexes"], "area_ha": res["summary"]["complex_area_ha"]})
    vals = [r["jaccard"] for r in rows]
    return {"rows": rows, "mean": round(float(np.mean(vals)), 3), "min": round(float(np.min(vals)), 3)}


def validate(scn_or_region, s: SiteSettings | None = None) -> dict:
    t0 = time.perf_counter()
    s = s or SiteSettings()
    R = scn_or_region if isinstance(scn_or_region, Region) else Region(scn_or_region)
    ours = solve(R, s)
    k = max(1, ours["summary"]["n_complexes"], int(R.truth.max()))
    methods = {
        "제안(제약 최적화)": np.asarray(ours["assign"]),
        "공간 KMeans": baseline_kmeans(R, s, k),
        "면적순 greedy": baseline_greedy_area(R, s),
    }
    table = []
    for name, assign in methods.items():
        m = reproduction_metrics(R, assign)
        c = _check_constraints(R, s, assign)
        table.append({"method": name, **m, **c, "area_ha": round(float(R.area[np.asarray(assign) > 0].sum()) / 1e4, 1)})
    return {
        "truth_available": bool(R.truth.max() > 0),
        "truth_synthetic": bool((R.meta or {}).get("synthetic", False)),
        "table": table,
        "sensitivity": sensitivity(R, s, ours["assign"]),
        "seconds": round(time.perf_counter() - t0, 2),
    }


# ------------------------------------------------------------------ 소규모 정확해(MILP)
def exact_window(R: Region, s: SiteSettings, subset, K: int = 2, time_limit: float = 30.0) -> dict:
    """subset 필지만으로 C1–C6·C8을 지키는 단지 최대 K개를 MILP로 찾는다(C7 형상은 비선형이라 제외).

    연결성은 단일 상품 흐름(flow)으로 표현한다: 단지마다 뿌리 1개가 흐름을 내보내고,
    선택된 다른 필지는 같은 단지의 선택 필지를 거쳐 흐름 1을 받아야 한다.
    """
    t0 = time.perf_counter()
    T = parcel_terms(R, s)
    S = [i for i in subset if T["eligible"][i]]
    n = len(S)
    loc = {g: l for l, g in enumerate(S)}
    arcs = [(loc[i], loc[j]) for i in S for j in R.nbrs[i] if j in loc]
    owners = sorted({R.oid[i] for i in S})
    oloc = {o: l for l, o in enumerate(owners)}
    proxy = s.owner_mode == "proxy" or not R.has_owner
    a_ha = R.area[S] / 1e4
    val = T["v"][S] + s.w_consol * 0.9 * a_ha - s.w_friction * 0.05
    cls = [1, 2, 3]

    nv = 0

    def block(m):
        nonlocal nv
        st = nv
        nv += m
        return st

    X = block(n * K)  # x[i,k]
    Y = block(K)
    RT = block(n * K)  # root
    F = block(len(arcs) * K)
    O = block(len(owners) * K) if not proxy else None
    Z = block(len(cls) * K)
    x = lambda i, k: X + k * n + i  # noqa: E731
    r = lambda i, k: RT + k * n + i  # noqa: E731
    f = lambda a, k: F + k * len(arcs) + a  # noqa: E731
    o = lambda j, k: O + k * len(owners) + j  # noqa: E731
    z = lambda c, k: Z + k * len(cls) + c  # noqa: E731

    c = np.zeros(nv)
    for k in range(K):
        for i in range(n):
            c[x(i, k)] = -val[i]
        if not proxy:
            for j in range(len(owners)):
                c[o(j, k)] = s.w_friction * 0.3
    rows, cols, vals, lb, ub = [], [], [], [], []

    def add(entries, lo, hi):
        rr = len(lb)
        for cc, vv in entries:
            rows.append(rr)
            cols.append(cc)
            vals.append(vv)
        lb.append(lo)
        ub.append(hi)

    M = n
    for i in range(n):
        add([(x(i, k), 1) for k in range(K)], -np.inf, 1)  # C1
    for k in range(K):
        for i in range(n):
            add([(x(i, k), 1), (Y + k, -1)], -np.inf, 0)
            add([(r(i, k), 1), (x(i, k), -1)], -np.inf, 0)
        add([(r(i, k), 1) for i in range(n)] + [(Y + k, -1)], 0, 0)  # 뿌리 1개
        add([(x(i, k), a_ha[i]) for i in range(n)] + [(Y + k, -s.a_min_ha)], 0, np.inf)  # C2
        add([(x(i, k), a_ha[i]) for i in range(n)] + [(Y + k, -s.a_max_ha)], -np.inf, 0)
        if s.require_road:  # C5
            add([(x(i, k), 1) for i in range(n) if R.road[S[i]]] + [(Y + k, -1)], 0, np.inf)
        # C3
        if proxy:
            add([(x(i, k), 1) for i in range(n)] + [(Y + k, -s.n_min * s.parcels_per_owner)], 0, np.inf)
        else:
            for i in range(n):
                add([(o(oloc[R.oid[S[i]]], k), 1), (x(i, k), -1)], 0, np.inf)
            for j, ow in enumerate(owners):
                mem = [i for i in range(n) if R.oid[S[i]] == ow]
                add([(o(j, k), 1)] + [(x(i, k), -1) for i in mem], -np.inf, 0)
            add([(o(j, k), 1) for j in range(len(owners))] + [(Y + k, -s.n_min)], 0, np.inf)
        # C4 흐름: 유입 − 유출 ≥ x − M·root,  흐름은 선택 필지 사이에서만
        for i in range(n):
            ent = [(f(a, k), 1) for a, (u, v) in enumerate(arcs) if v == i] + [(f(a, k), -1) for a, (u, v) in enumerate(arcs) if u == i]
            add(ent + [(x(i, k), -1), (r(i, k), M)], 0, np.inf)
        for a, (u, v) in enumerate(arcs):
            add([(f(a, k), 1), (x(u, k), -(M - 1))], -np.inf, 0)
            add([(f(a, k), 1), (x(v, k), -(M - 1))], -np.inf, 0)
        # C8 배수 동질성: 고른 등급의 면적 ≥ h·전체 면적
        add([(z(ci, k), 1) for ci in range(3)] + [(Y + k, -1)], 0, 0)
        for ci, cl in enumerate(cls):
            ent = [(x(i, k), a_ha[i] * ((1 if R.drain[S[i]] == cl else 0) - s.homog_min)) for i in range(n)]
            add(ent + [(z(ci, k), -s.a_max_ha)], -s.a_max_ha, np.inf)

    integrality = np.zeros(nv)
    integrality[X : X + n * K] = 1
    integrality[Y : Y + K] = 1
    integrality[RT : RT + n * K] = 1
    integrality[Z : Z + 3 * K] = 1
    upper = np.ones(nv)
    upper[F : F + len(arcs) * K] = M - 1
    A = coo_matrix((vals, (rows, cols)), shape=(len(lb), nv)).tocsr()
    res = milp(
        c,
        constraints=LinearConstraint(A, np.array(lb), np.array(ub)),
        integrality=integrality,
        bounds=Bounds(np.zeros(nv), upper),
        options={"time_limit": time_limit, "disp": False},
    )
    exact_val = None if res.x is None else float(-res.fun)
    bound = getattr(res, "mip_dual_bound", None)
    chosen = []
    if res.x is not None:
        for k in range(K):
            mem = [S[i] for i in range(n) if res.x[x(i, k)] > 0.5]
            if mem:
                chosen.append(mem)
    heur = solve(R, s, subset=S)
    heur_sets = [[R.ids.index(pid) for pid in cx["parcels"]] for cx in heur["complexes"]]
    heur_val = sum(linear_value(R, s, m) for m in heur_sets)
    return {
        "n_parcels": n,
        "K": K,
        "status": res.message,
        "milp_value": None if exact_val is None else round(exact_val, 3),
        "milp_bound": None if bound is None else round(-float(bound), 3),
        "milp_complexes": [len(m) for m in chosen],
        "heuristic_value": round(heur_val, 3),
        "heuristic_complexes": [len(m) for m in heur_sets],
        "gap_vs_milp": None if not exact_val else round((exact_val - heur_val) / abs(exact_val), 4),
        "seconds": round(time.perf_counter() - t0, 2),
    }


def window_around(R: Region, s: SiteSettings, center_idx: int, n_target: int = 90) -> list[int]:
    """중심 필지에서 가까운 적격 필지 n_target개."""
    T = parcel_terms(R, s)
    d = np.hypot(R.cx - R.cx[center_idx], R.cy - R.cy[center_idx])
    order = [i for i in np.argsort(d) if T["eligible"][i]]
    return order[:n_target]
