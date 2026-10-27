"""공동영농 후보단지 선정 엔진(기획안 4.3 Stage 1).

읍면 필지 인접 그래프 위에서 아래 제약을 만족하는 필지 묶음(단지)을 찾는다.

  C1 필지는 최대 한 단지에만          C2 단지 면적 ≥ A_min (그리고 ≤ A_max)
  C3 참여 농가 수 ≥ n_min            C4 단지 필지들이 인접 그래프에서 연결
  C5 도로 접속 필지 ≥ 1              C6 필지 경사 ≤ s_max
  C7 형상지수(Polsby-Popper) ≥ c_min C8 최빈 배수등급 면적 비율 ≥ h_min

목적함수(단지 k):
  score = Σ_i 면적_i·(w1·소득지수_i + w2·기계화지수_i) + w3·면적·(1 − K_필지) − w4·(0.3·농가 수 + 0.05·필지 수)
  K_필지는 Januszewski 단편화 지수 √ΣA / Σ√A. 묶으면 한 경영 단위가 되므로 (1 − K)가 집단화 개선도다.

해법: 시드 선택 → 지역 성장(제약을 채울 때까지는 최선 후보를, 채운 뒤에는 점수가 오르는 후보만) → 국소 탐색(경계 필지 추가·제거·이동).
소유권과 필지 경계는 건드리지 않고 경영 단위만 묶는다.
"""
from __future__ import annotations

import math
import time
from collections import Counter
from dataclasses import asdict, dataclass

import numpy as np

from .geo import LocalProjection, adjacency, open_ring, ring_area, ring_centroid

SOIL_MULT = {1: 1.00, 2: 0.95, 3: 0.88, 4: 0.80}
# 공동영농 전환 시 ha당 연 소득 증분(만원) [보수, 기준, 낙관]. 초안 가정값 — KAMIS 가격·표준 단수로 교체 예정.
INCOME_PER_HA = {
    "paddy_double": (80, 200, 450),  # 논, 배수 양호·보통: 하계 콩·가루쌀 + 동계 밀 등 이모작 가능
    "paddy_single": (20, 50, 100),  # 논, 배수 불량: 벼 단작 유지, 기계 작업비 절감 위주
    "upland": (40, 120, 300),  # 밭
}
INCOME_REF = 200.0
SLOPE_HARD = 15.0
DRAIN_NAME = {1: "양호", 2: "보통", 3: "불량"}


@dataclass
class SiteSettings:
    a_min_ha: float = 20.0
    a_max_ha: float = 60.0
    n_min: int = 5
    slope_max: float = 7.0
    compact_min: float = 0.20
    homog_min: float = 0.60
    require_road: bool = True
    include_upland: bool = True
    w_income: float = 1.0
    w_mech: float = 1.0
    w_consol: float = 1.0
    w_friction: float = 1.0
    owner_mode: str = "data"  # data: 소유자 속성 사용, proxy: 필지 수로 추정
    parcels_per_owner: float = 2.5
    local_search: bool = True


# ------------------------------------------------------------------ 데이터 적재
class Region:
    def __init__(self, scn: dict):
        feats = scn["parcels"]["features"]
        if not feats:
            raise ValueError("필지가 없습니다.")
        rings_ll = []
        for f in feats:
            g = f["geometry"]
            if g["type"] == "Polygon":
                rings_ll.append(np.asarray(g["coordinates"][0], float))
            elif g["type"] == "MultiPolygon":
                rings_ll.append(np.asarray(max((p[0] for p in g["coordinates"]), key=ring_area), float))
            else:
                raise ValueError(f"지원하지 않는 geometry: {g['type']}")
        meta = scn.get("meta") or {}
        origin = meta.get("origin")
        if not origin:
            allp = np.vstack(rings_ll)
            origin = [float(allp[:, 0].min()), float(allp[:, 1].min())]
        self.proj = LocalProjection(*origin)
        self.meta = meta
        n = len(feats)
        self.n = n
        self.ids, xy = [], []
        self.area = np.zeros(n)
        self.perim = np.zeros(n)
        self.cx = np.zeros(n)
        self.cy = np.zeros(n)
        self.slope = np.zeros(n)
        self.drain = np.zeros(n, dtype=int)
        self.soil = np.zeros(n, dtype=int)
        self.road = np.zeros(n, dtype=bool)
        self.land_type, self.use, self.owner = [], [], []
        self.truth = np.zeros(n, dtype=int)
        for i, (f, rll) in enumerate(zip(feats, rings_ll)):
            p = f.get("properties") or {}
            self.ids.append(str(p.get("id") or f.get("id") or f"P{i + 1}"))
            r = open_ring(self.proj.to_xy(rll))
            xy.append(r)
            self.area[i] = ring_area(r)
            self.perim[i] = float(np.hypot(*(np.roll(r, -1, axis=0) - r).T).sum())
            c = ring_centroid(r)
            self.cx[i], self.cy[i] = c
            self.slope[i] = float(p.get("slope", 0.0))
            self.drain[i] = int(np.clip(int(p.get("drainage", 2)), 1, 3))
            self.soil[i] = int(np.clip(int(p.get("soil_grade", 2)), 1, 4))
            self.road[i] = bool(p.get("road_access", False))
            lt = str(p.get("land_type", "답"))
            self.land_type.append({"논": "답", "밭": "전"}.get(lt, lt))
            self.use.append(str(p.get("land_use") or {"답": "논", "전": "밭"}.get(self.land_type[-1], "기타")))
            self.owner.append(str(p.get("owner") or ""))
            self.truth[i] = int(p.get("truth_complex", 0) or 0)
        cached = scn.get("adjacency")
        if cached and all(0 <= int(e[0]) < n and 0 <= int(e[1]) < n for e in cached):
            self.edges = [(int(i), int(j), float(L), bool(t)) for i, j, L, t in cached]
        else:
            self.edges = adjacency(xy)
        self.nbrs: list[dict[int, float]] = [dict() for _ in range(n)]
        for i, j, L, _ in self.edges:
            self.nbrs[i][j] = L
            self.nbrs[j][i] = L
        self.has_owner = all(self.owner)
        self.owner_idx = {o: k for k, o in enumerate(sorted(set(self.owner)))}
        self.oid = np.array([self.owner_idx[o] for o in self.owner])

    def lonlat(self, x, y):
        return [round(v, 7) for v in self.proj.to_lonlat(np.array([[x, y]]))[0].tolist()]


# ------------------------------------------------------------------ 필지 지수
def parcel_terms(R: Region, s: SiteSettings):
    n = R.n
    a_ha = R.area / 1e4
    inc = np.zeros((n, 3))
    eligible = np.ones(n, dtype=bool)
    reason = [""] * n
    for i in range(n):
        use = R.use[i]
        if use in ("과수", "시설"):
            eligible[i] = False
            reason[i] = f"{use} 필지라 논·밭 이모작 단지 대상이 아닙니다"
        elif R.land_type[i] == "전" and not s.include_upland:
            eligible[i] = False
            reason[i] = "밭 제외 설정"
        elif R.land_type[i] not in ("답", "전"):
            eligible[i] = False
            reason[i] = f"지목 {R.land_type[i]}"
        if eligible[i] and R.slope[i] > s.slope_max:
            eligible[i] = False
            reason[i] = f"경사 {R.slope[i]:.1f}%가 상한 {s.slope_max:g}%를 넘습니다(C6)"
        if R.land_type[i] == "답":
            key = "paddy_double" if R.drain[i] <= 2 else "paddy_single"
        else:
            key = "upland"
        inc[i] = np.array(INCOME_PER_HA[key]) * SOIL_MULT.get(R.soil[i], 0.85)
    inc_idx = np.minimum(inc[:, 1] / INCOME_REF, 1.2)
    mech = (
        0.45 * (1 - np.minimum(R.slope / SLOPE_HARD, 1))
        + 0.30 * np.minimum(1.0, a_ha / 0.4)
        + 0.25 * np.where(R.road, 1.0, 0.5)
    )
    v = a_ha * (s.w_income * inc_idx + s.w_mech * mech)
    return {"a_ha": a_ha, "inc": inc, "inc_idx": inc_idx, "mech": mech, "v": v, "eligible": eligible, "reason": reason}


# ------------------------------------------------------------------ 단지 상태
class Complex:
    __slots__ = ("members", "A", "V", "sqrt_sum", "owners", "P", "road", "drain_area", "R", "T", "s")

    def __init__(self, R: Region, T: dict, s: SiteSettings):
        self.R, self.T, self.s = R, T, s
        self.members: set[int] = set()
        self.A = self.V = self.sqrt_sum = self.P = 0.0
        self.owners: Counter = Counter()
        self.road = 0
        self.drain_area = np.zeros(4)

    def shared(self, i: int) -> float:
        return sum(L for j, L in self.R.nbrs[i].items() if j in self.members)

    def add(self, i: int) -> None:
        R = self.R
        self.P += R.perim[i] - 2 * self.shared(i)
        self.members.add(i)
        self.A += R.area[i]
        self.V += self.T["v"][i]
        self.sqrt_sum += math.sqrt(R.area[i])
        self.owners[R.oid[i]] += 1
        self.road += int(R.road[i])
        self.drain_area[R.drain[i]] += R.area[i]

    def remove(self, i: int) -> None:
        R = self.R
        self.members.discard(i)
        self.P -= R.perim[i] - 2 * self.shared(i)
        self.A -= R.area[i]
        self.V -= self.T["v"][i]
        self.sqrt_sum -= math.sqrt(R.area[i])
        self.owners[R.oid[i]] -= 1
        if self.owners[R.oid[i]] <= 0:
            del self.owners[R.oid[i]]
        self.road -= int(R.road[i])
        self.drain_area[R.drain[i]] -= R.area[i]

    # 파생 지표
    def n_owners(self) -> float:
        if self.s.owner_mode == "proxy" or not self.R.has_owner:
            return math.ceil(len(self.members) / max(self.s.parcels_per_owner, 1e-9))
        return len(self.owners)

    def compactness(self) -> float:
        return 4 * math.pi * self.A / (self.P * self.P) if self.P > 0 else 0.0

    def homog(self) -> tuple[float, int]:
        c = int(np.argmax(self.drain_area[1:]) + 1)
        return (self.drain_area[c] / self.A if self.A > 0 else 1.0), c

    def januszewski(self) -> float:
        return math.sqrt(self.A) / self.sqrt_sum if self.sqrt_sum > 0 else 1.0

    def score(self) -> float:
        s = self.s
        a_ha = self.A / 1e4
        consol = a_ha * (1 - self.januszewski())
        fric = 0.3 * self.n_owners() + 0.05 * len(self.members)
        return self.V + s.w_consol * consol - s.w_friction * fric

    def violations(self) -> list[str]:
        s = self.s
        out = []
        a_ha = self.A / 1e4
        if a_ha < s.a_min_ha:
            out.append(f"면적 {a_ha:.1f}ha < 최소 {s.a_min_ha:g}ha(C2)")
        if a_ha > s.a_max_ha + 1e-9:
            out.append(f"면적 {a_ha:.1f}ha > 최대 {s.a_max_ha:g}ha(C2)")
        if self.n_owners() < s.n_min:
            out.append(f"참여 농가 {self.n_owners()}명 < 최소 {s.n_min}명(C3)")
        if s.require_road and self.road < 1:
            out.append("도로 접속 필지 없음(C5)")
        if self.compactness() < s.compact_min:
            out.append(f"형상지수 {self.compactness():.2f} < {s.compact_min:g}(C7)")
        h, _ = self.homog()
        if h < s.homog_min:
            out.append(f"배수 동질성 {h:.2f} < {s.homog_min:g}(C8)")
        return out

    def growth_ready(self) -> bool:
        s = self.s
        return (
            self.A / 1e4 >= s.a_min_ha
            and self.n_owners() >= s.n_min
            and (self.road >= 1 or not s.require_road)
        )


def _connected_without(R: Region, members: set[int], drop: int) -> bool:
    rest = members - {drop}
    if not rest:
        return False
    start = next(iter(rest))
    seen = {start}
    stack = [start]
    while stack:
        u = stack.pop()
        for v in R.nbrs[u]:
            if v in rest and v not in seen:
                seen.add(v)
                stack.append(v)
    return len(seen) == len(rest)


def _delta_add(C: Complex, i: int) -> float:
    before = C.score()
    C.add(i)
    after = C.score()
    C.remove(i)
    return after - before


def _pp_after_add(C: Complex, i: int) -> float:
    A = C.A + C.R.area[i]
    P = C.P + C.R.perim[i] - 2 * C.shared(i)
    return 4 * math.pi * A / (P * P) if P > 0 else 0.0


def _trim_for_shape(R: Region, s: SiteSettings, C: Complex, max_steps: int = 200) -> None:
    """형상지수가 기준 미달이면, 다른 제약을 지키는 범위에서 튀어나온 필지부터 떼어 낸다."""
    steps = 0
    while C.compactness() < s.compact_min and steps < max_steps and len(C.members) > 1:
        steps += 1
        best, best_ratio = None, 1e9
        for p in C.members:
            ratio = C.shared(p) / max(R.perim[p], 1e-9)
            if ratio < best_ratio:
                best, best_ratio = p, ratio
        cands = sorted(C.members, key=lambda p: C.shared(p) / max(R.perim[p], 1e-9))[:12]
        done = False
        for p in cands:
            if not _connected_without(R, C.members, p):
                continue
            C.remove(p)
            if C.growth_ready():
                done = True
                break
            C.add(p)
        if not done:
            break


def _homog_after_add(C: Complex, i: int) -> float:
    d = C.drain_area.copy()
    d[C.R.drain[i]] += C.R.area[i]
    return d[1:].max() / (C.A + C.R.area[i])


# ------------------------------------------------------------------ 해법
def solve(scn_or_region, settings: SiteSettings | None = None, subset=None) -> dict:
    t0 = time.perf_counter()
    s = settings or SiteSettings()
    R = scn_or_region if isinstance(scn_or_region, Region) else Region(scn_or_region)
    T = parcel_terms(R, s)
    eligible = T["eligible"].copy()
    if subset is not None:
        mask = np.zeros(R.n, dtype=bool)
        mask[list(subset)] = True
        eligible &= mask
    assign = np.zeros(R.n, dtype=int)  # 0 = 미배정, k = 단지 번호(임시)
    fail_reason: dict[int, str] = {}
    complexes: list[Complex] = []
    tried = np.zeros(R.n, dtype=bool)

    density = np.where(R.area > 0, T["v"] / (R.area / 1e4), 0) * np.where(R.road, 1.15, 1.0)
    order = [i for i in np.argsort(-density) if eligible[i]]
    a_max = s.a_max_ha * 1e4

    for seed in order:
        if assign[seed] or tried[seed]:
            continue
        C = Complex(R, T, s)
        C.add(seed)
        frontier = {j for j in R.nbrs[seed] if eligible[j] and not assign[j]}
        sx, sy = R.cx[seed] * R.area[seed], R.cy[seed] * R.area[seed]
        while frontier:
            ready = C.growth_ready()
            best, best_key, best_d = None, -1e18, 0.0
            gx, gy = sx / C.A, sy / C.A
            for f in frontier:
                if C.A + R.area[f] > a_max:
                    continue
                if _homog_after_add(C, f) < s.homog_min:
                    continue
                d = _delta_add(C, f)
                if ready and d <= 1e-9:
                    continue
                if ready and _pp_after_add(C, f) < s.compact_min + 0.02:
                    continue
                # 점수 증분 + 경계 공유(형상) 보너스 − 단지 중심에서의 거리 벌점 → 둥글게 자라게 한다
                share = C.shared(f) / max(R.perim[f], 1e-9)
                dist = math.hypot(R.cx[f] - gx, R.cy[f] - gy)
                key = 0.5 * d + 1.5 * share - 0.002 * dist
                if key > best_key:
                    best, best_key, best_d = f, key, d
            if best is None:
                break
            C.add(best)
            sx += R.cx[best] * R.area[best]
            sy += R.cy[best] * R.area[best]
            frontier.discard(best)
            frontier.update(j for j in R.nbrs[best] if eligible[j] and not assign[j] and j not in C.members)
        _trim_for_shape(R, s, C)
        viol = C.violations()
        if viol:
            for m in C.members:
                tried[m] = True
                fail_reason.setdefault(m, viol[0])
            continue
        complexes.append(C)
        for m in C.members:
            assign[m] = len(complexes)

    if s.local_search and complexes:
        _local_search(R, T, s, complexes, assign, eligible)

    return _report(R, T, s, complexes, assign, eligible, fail_reason, time.perf_counter() - t0)


def _local_search(R, T, s, complexes, assign, eligible, passes: int = 3) -> None:
    a_max = s.a_max_ha * 1e4
    for _ in range(passes):
        changed = False
        # 1) 경계 미배정 필지 추가
        for k, C in enumerate(complexes, start=1):
            border = {j for i in C.members for j in R.nbrs[i] if eligible[j] and assign[j] == 0}
            for f in border:
                if C.A + R.area[f] > a_max or _homog_after_add(C, f) < s.homog_min:
                    continue
                if _delta_add(C, f) > 1e-9:
                    C.add(f)
                    if C.violations():
                        C.remove(f)
                        continue
                    assign[f] = k
                    changed = True
        # 2) 점수를 깎는 경계 필지 제거
        for k, C in enumerate(complexes, start=1):
            for p in list(C.members):
                if len(C.members) <= 1:
                    break
                before = C.score()
                C.remove(p)
                gain = C.score() - before
                ok = gain > 1e-9 and not C.violations() and _connected_without(R, C.members | {p}, p)
                if ok:
                    assign[p] = 0
                    changed = True
                else:
                    C.add(p)
        # 3) 이웃 단지로 이동
        for k, C in enumerate(complexes, start=1):
            for p in list(C.members):
                for j in R.nbrs[p]:
                    k2 = assign[j]
                    if k2 in (0, k):
                        continue
                    D = complexes[k2 - 1]
                    if D.A + R.area[p] > a_max or _homog_after_add(D, p) < s.homog_min:
                        continue
                    if not _connected_without(R, C.members, p):
                        continue
                    b = C.score() + D.score()
                    C.remove(p)
                    D.add(p)
                    if C.score() + D.score() - b > 1e-9 and not C.violations() and not D.violations():
                        assign[p] = k2
                        changed = True
                        break
                    D.remove(p)
                    C.add(p)
        if not changed:
            break


# ------------------------------------------------------------------ 결과·근거
def _components_area(R, eligible):
    comp = np.full(R.n, -1)
    areas = []
    for s0 in range(R.n):
        if not eligible[s0] or comp[s0] >= 0:
            continue
        cid = len(areas)
        stack = [s0]
        comp[s0] = cid
        tot = 0.0
        while stack:
            u = stack.pop()
            tot += R.area[u]
            for v in R.nbrs[u]:
                if eligible[v] and comp[v] < 0:
                    comp[v] = cid
                    stack.append(v)
        areas.append(tot)
    return comp, areas


def _report(R, T, s, complexes, assign, eligible, fail_reason, secs) -> dict:
    # 점수 순으로 단지 번호를 다시 매긴다
    order = sorted(range(len(complexes)), key=lambda k: -complexes[k].score())
    remap = {old + 1: new + 1 for new, old in enumerate(order)}
    assign = np.array([remap.get(int(a), 0) for a in assign])
    complexes = [complexes[k] for k in order]

    comp, comp_area = _components_area(R, eligible)
    status, reasons, contrib = [], [], [0.0] * R.n
    out_cx = []
    for k, C in enumerate(complexes, start=1):
        idx = sorted(C.members)
        a_ha = C.A / 1e4
        h, hc = C.homog()
        inc = T["inc"][idx].T @ (R.area[idx] / 1e4) / 100.0  # 백만원/년
        mech = float(np.average(T["mech"][idx], weights=R.area[idx]))
        sc = C.score()
        for p in idx:
            C.remove(p)
            contrib[p] = sc - C.score()
            C.add(p)
        cxy = (np.average(R.cx[idx], weights=R.area[idx]), np.average(R.cy[idx], weights=R.area[idx]))
        out_cx.append(
            {
                "id": k,
                "parcels": [R.ids[i] for i in idx],
                "area_ha": round(a_ha, 2),
                "n_parcels": len(idx),
                "n_owners": int(C.n_owners()),
                "owner_mode": "proxy" if (s.owner_mode == "proxy" or not R.has_owner) else "data",
                "mean_slope": round(float(np.average(R.slope[idx], weights=R.area[idx])), 2),
                "max_slope": round(float(R.slope[idx].max()), 2),
                "road_parcels": int(C.road),
                "homog": round(h, 3),
                "drain_class": DRAIN_NAME[hc],
                "compactness": round(C.compactness(), 3),
                "mech_index": round(mech, 3),
                "consolidation": round(1 - C.januszewski(), 3),
                "januszewski_before": round(C.januszewski(), 3),
                "income_musd": [round(float(x), 1) for x in inc],
                "score": round(sc, 2),
                "centroid": R.lonlat(*cxy),
                "checks": {
                    "C2 면적": [round(a_ha, 1), f"{s.a_min_ha:g}~{s.a_max_ha:g}ha"],
                    "C3 참여 농가": [int(C.n_owners()), f"≥{s.n_min}"],
                    "C4 연결": ["연결", "연결"],
                    "C5 도로 접속": [int(C.road), "≥1" if s.require_road else "선택"],
                    "C6 최대 경사": [round(float(R.slope[idx].max()), 1), f"≤{s.slope_max:g}%"],
                    "C7 형상지수": [round(C.compactness(), 2), f"≥{s.compact_min:g}"],
                    "C8 배수 동질성": [round(h, 2), f"≥{s.homog_min:g}"],
                },
            }
        )
    for i in range(R.n):
        k = int(assign[i])
        if k:
            status.append("in")
            reasons.append(
                f"단지 {k}에 포함. 면적 {R.area[i] / 1e4:.2f}ha, 경사 {R.slope[i]:.1f}%, 배수 {DRAIN_NAME[R.drain[i]]}, "
                f"도로 접속 {'있음' if R.road[i] else '없음'}. 단지 점수 기여 {contrib[i]:+.2f}"
            )
        elif not eligible[i]:
            status.append("ineligible")
            reasons.append(T["reason"][i] or "선정 대상 구역 밖")
        else:
            status.append("eligible")
            if comp[i] >= 0 and comp_area[comp[i]] < s.a_min_ha * 1e4:
                reasons.append(
                    f"이어진 적격 필지 묶음이 {comp_area[comp[i]] / 1e4:.1f}ha라 최소 단지 면적 {s.a_min_ha:g}ha에 못 미칩니다(C2)"
                )
                continue
            near = sorted({int(assign[j]) for j in R.nbrs[i] if assign[j]})
            msg = None
            for k in near:
                C = complexes[k - 1]
                if C.A + R.area[i] > s.a_max_ha * 1e4:
                    msg = f"맞닿은 단지 {k}가 최대 면적 {s.a_max_ha:g}ha에 가깝습니다(C2)"
                    continue
                hh = _homog_after_add(C, i)
                if hh < s.homog_min:
                    msg = f"단지 {k}에 넣으면 배수 동질성이 {hh:.2f}로 기준 {s.homog_min:g} 아래가 됩니다(C8)"
                    continue
                pp = _pp_after_add(C, i)
                if pp < s.compact_min:
                    msg = f"단지 {k}에 넣으면 형상지수가 {pp:.2f}로 기준 {s.compact_min:g} 아래가 됩니다(C7)"
                    continue
                d = _delta_add(C, i)
                if d <= 1e-9:
                    msg = f"단지 {k}에 넣으면 단지 점수가 {d:+.2f}로 낮아집니다(목적함수)"
                else:
                    C.add(i)
                    v = C.violations()
                    C.remove(i)
                    msg = f"단지 {k}에 넣으면 제약을 어깁니다: {v[0]}" if v else f"단지 {k}에 넣으면 점수가 {d:+.2f} 오르지만 국소 탐색 순서상 빠졌습니다"
                break
            if msg is None:
                msg = fail_reason.get(i, "주변에서 제약을 모두 만족하는 단지를 만들지 못했습니다")
                msg = "이 필지를 포함해 만든 단지가 조건을 못 채웠습니다: " + msg if i in fail_reason else msg
            reasons.append(msg)

    elig_area = float(R.area[eligible].sum())
    in_area = float(sum(C.A for C in complexes))
    return {
        "parcel_ids": R.ids,
        "assign": assign.tolist(),
        "status": status,
        "reasons": reasons,
        "complexes": out_cx,
        "summary": {
            "n_complexes": len(complexes),
            "complex_area_ha": round(in_area / 1e4, 1),
            "eligible_area_ha": round(elig_area / 1e4, 1),
            "total_area_ha": round(float(R.area.sum()) / 1e4, 1),
            "coverage": round(in_area / elig_area, 3) if elig_area else 0.0,
            "owners_included": int(len({R.oid[i] for C in complexes for i in C.members})),
            "income_musd": [round(sum(c["income_musd"][j] for c in out_cx), 1) for j in range(3)],
            "total_score": round(sum(C.score() for C in complexes), 2),
            "seconds": round(secs, 3),
        },
        "settings": asdict(s),
    }


def linear_value(R: Region, s: SiteSettings, members) -> float:
    """MILP 비교용 선형화 목적값: 집단화 개선도를 면적×0.9로 근사한다."""
    T = parcel_terms(R, s)
    idx = list(members)
    if not idx:
        return 0.0
    owners = math.ceil(len(idx) / s.parcels_per_owner) if (s.owner_mode == "proxy" or not R.has_owner) else len({R.oid[i] for i in idx})
    return float(T["v"][idx].sum() + s.w_consol * 0.9 * R.area[idx].sum() / 1e4 - s.w_friction * (0.3 * owners + 0.05 * len(idx)))
