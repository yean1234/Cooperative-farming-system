"""조합원 수익 배분 시뮬레이션.

법인 순이익을 어떤 기준으로 나눌 때 조합원 각자가 개별영농보다 나아지는지 비교한다.

- 면적 비례: 출자 면적만 본다.
- 생산력 환산면적 비례: 면적 × 토양등급 계수.
- 토지·노동 혼합: 생산력 환산면적과 법인 작업 참여 일수를 섞는다.
- 기여도(샤플리) 비례: 조합원 부분집합이 자기 필지만으로 공동영농할 때의 순이익 v(S)로
  협력게임을 정의하고 샤플리 값(Shapley, 1953)을 몬테카를로로 근사한다.
  다른 조합원 필지를 '이어 주는' 필지를 가진 농가의 기여가 크게 잡힌다.
"""
from __future__ import annotations

from collections import defaultdict

import numpy as np

from .metrics import field_efficiency
from .params import crop_water, revenue_parts
from .routing import mst_length

M2_PER_PYEONG = 3.305785


class _UnionFind:
    def __init__(self):
        self.parent: dict[int, int] = {}

    def add(self, x: int) -> None:
        self.parent.setdefault(x, x)

    def find(self, x: int) -> int:
        root = x
        while self.parent[root] != root:
            root = self.parent[root]
        while self.parent[x] != root:
            self.parent[x], x = root, self.parent[x]
        return root

    def union(self, a: int, b: int) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[rb] = ra


class CoalitionModel:
    """조합원 부분집합 S가 자기 필지만으로 공동영농할 때의 순이익 v(S)를 빠르게 계산한다.

    포장 작업비(대구획화 시 맞닿은 같은 작목 필지를 한 포장으로 계산), 블록 진입·필지 이동비,
    도로 이동비, 순회 관리비가 S의 공간 배치에 따라 달라진다.
    순회 거리는 최소신장트리 길이에 TOUR_FACTOR를 곱해 근사한다.
    """

    TOUR_FACTOR = 1.25

    def __init__(self, field, member_parcels: dict[str, list[int]], crops, params, depot, merge_bunds: bool = True):
        self.f = field
        self.mp = member_parcels
        self.depot = np.asarray(depot, dtype=float)
        self.merge = merge_bunds
        self.ops = ops = params["ops"]
        machines = params["machines"]
        names = sorted(set(crops))
        self.cix = np.array([names.index(c) for c in crops])
        k = len(names)
        self.block_cost = np.zeros(k)
        self.trans_cost = np.zeros(k)
        self.km_cost = np.zeros(k)
        self.field_coef = np.zeros(k)  # 작업 횟수와 시간당 비용을 작업능률로 나눈 값의 합(원/ha, 포장효율 1 기준)
        for ci, c in enumerate(names):
            for op in params["crops"][c]["ops"]:
                per_h = machines[op["machine"]]["cost_per_h"] * op["passes"]
                self.block_cost[ci] += ops["setup_min_coop"] / 60.0 * per_h
                self.trans_cost[ci] += ops["transition_min"] / 60.0 * per_h
                self.km_cost[ci] += per_h / ops["road_speed_kmh"] * ops["detour"]
                self.field_coef[ci] += per_h / op["capacity_ha_h"]
        self.rev = np.zeros(field.n)
        for i, p in enumerate(field.parcels):
            gm, dp = revenue_parts(crops[i], p, params)
            self.rev[i] = gm + dp - ops["coop_overhead_per_ha"] * p.area / 1e4
        self.water = [crop_water(c, params) for c in crops]
        self.patrol = {}
        for w in ("wet", "dry"):
            visits = max(
                [params["crops"][c]["patrol_visits"] for c in set(crops) if crop_water(c, params) == w] or [0]
            )
            self.patrol[w] = (
                visits * ops["patrol_stop_min"] / 60.0 * ops["labor_cost_per_h"],
                visits * ops["labor_cost_per_h"] / ops["patrol_speed_kmh"] * ops["detour"],
            )

    @staticmethod
    def _new_ufs():
        return _UnionFind(), _UnionFind(), _UnionFind()

    def _add(self, p, ufs) -> None:
        uf_block, uf_plot, uf_water = ufs
        for uf in ufs:
            uf.add(p)
        for j, _, touch in self.f.nbrs[p]:
            if j not in uf_block.parent:
                continue
            if self.cix[j] == self.cix[p]:
                uf_block.union(p, j)
                if touch:
                    uf_plot.union(p, j)
            if self.water[j] == self.water[p]:
                uf_water.union(p, j)

    def _groups(self, added, uf, keep=None):
        area, cx, cy = self.f.area, self.f.cx, self.f.cy
        groups: dict[int, list[float]] = {}
        for p in added:
            if keep is not None and not keep(p):
                continue
            r = uf.find(p)
            g = groups.setdefault(r, [0.0, 0.0, 0.0])
            a = area[p]
            g[0] += a
            g[1] += a * cx[p]
            g[2] += a * cy[p]
        return groups

    def _tour_km(self, cents) -> float:
        pts = np.vstack([self.depot, np.array(cents)])
        return mst_length(pts) * self.TOUR_FACTOR / 1000.0

    def value_of(self, added: list[int], ufs) -> float:
        if not added:
            return 0.0
        uf_block, uf_plot, uf_water = ufs
        v = float(self.rev[added].sum())
        # 포장 작업비
        if self.merge:
            plot_groups = self._groups(added, uf_plot)
        else:
            plot_groups = {p: [self.f.area[p], 0.0, 0.0] for p in added}
        plots_per_crop: dict[int, int] = defaultdict(int)
        for r, g in plot_groups.items():
            k = int(self.cix[r])
            a_ha = g[0] / 1e4
            v -= self.field_coef[k] * a_ha / field_efficiency(a_ha, self.ops)
            plots_per_crop[k] += 1
        # 블록 진입과 블록 간 이동
        by_crop: dict[int, list] = defaultdict(list)
        for r, g in self._groups(added, uf_block).items():
            by_crop[int(self.cix[r])].append((g[1] / g[0], g[2] / g[0]))
        for k, cents in by_crop.items():
            nb = len(cents)
            v -= nb * self.block_cost[k] + (plots_per_crop[k] - nb) * self.trans_cost[k]
            if self.km_cost[k] > 0:
                v -= self._tour_km(cents) * self.km_cost[k]
        # 순회 관리
        by_water: dict[str, list] = defaultdict(list)
        for r, g in self._groups(added, uf_water, keep=lambda p: self.water[p] in self.patrol).items():
            by_water[self.water[r]].append((g[1] / g[0], g[2] / g[0]))
        for w, cents in by_water.items():
            stop_cost, km_cost = self.patrol[w]
            v -= len(cents) * stop_cost + self._tour_km(cents) * km_cost
        return v

    def value(self, members) -> float:
        ufs = self._new_ufs()
        added = []
        for m in members:
            for p in self.mp[m]:
                self._add(p, ufs)
                added.append(p)
        return self.value_of(added, ufs)

    def shapley(self, members: list[str], samples: int, seed: int = 0) -> dict[str, float]:
        rng = np.random.default_rng(seed)
        phi = {m: 0.0 for m in members}
        for _ in range(samples):
            ufs = self._new_ufs()
            added: list[int] = []
            prev = 0.0
            for k in rng.permutation(len(members)):
                m = members[k]
                for p in self.mp[m]:
                    self._add(p, ufs)
                    added.append(p)
                v = self.value_of(added, ufs)
                phi[m] += v - prev
                prev = v
        return {m: phi[m] / samples for m in members}


RULE_LABELS = {
    "area": "면적 비례",
    "productivity": "생산력 환산면적 비례",
    "mixed": "토지·노동 혼합",
    "shapley": "기여도(샤플리) 비례",
}


def distribute(
    field,
    members: list[str],
    individual_income: dict[str, float],
    coop_net: float,
    crops_for_game,
    params,
    samples: int = 150,
    labor_share: float = 0.2,
    seed: int = 0,
    merge_bunds: bool = True,
) -> dict:
    member_parcels = {m: [i for i, p in enumerate(field.parcels) if p.owner == m] for m in members}
    soil = params["soil_mult"]
    area = {m: float(field.area[member_parcels[m]].sum()) for m in members}
    prod = {
        m: float(sum(field.parcels[i].area * soil.get(field.parcels[i].soil_grade, 0.85) for i in member_parcels[m]))
        for m in members
    }
    labor = {m: float(field.farmers[m].labor_days) for m in members}

    def norm(d):
        tot = sum(max(v, 0.0) for v in d.values())
        return {k: (max(v, 0.0) / tot if tot > 0 else 1.0 / len(d)) for k, v in d.items()}

    game = CoalitionModel(field, member_parcels, crops_for_game, params, field.depot, merge_bunds=merge_bunds)
    phi = game.shapley(members, samples, seed)
    weights = {
        "area": norm(area),
        "productivity": norm(prod),
        "mixed": {
            m: (1 - labor_share) * norm(prod)[m] + labor_share * norm(labor)[m] for m in members
        },
        "shapley": norm(phi),
    }
    rows = []
    for m in members:
        fm = field.farmers[m]
        ind = individual_income.get(m, 0.0)
        shares = {r: coop_net * weights[r][m] for r in weights}
        rows.append(
            {
                "id": m,
                "name": fm.name,
                "age": fm.age,
                "area_ha": round(area[m] / 1e4, 4),
                "prod_area_ha": round(prod[m] / 1e4, 4),
                "labor_days": labor[m],
                "individual": ind,
                "shapley_value": phi[m],
                "shares": shares,
                "gain": {r: shares[r] - ind for r in shares},
            }
        )
    total_area = sum(area.values())
    summary = {}
    for r in weights:
        gains = [row["gain"][r] for row in rows]
        summary[r] = {
            "label": RULE_LABELS[r] if r != "mixed" else f"토지 {round((1 - labor_share) * 100)}%·노동 {round(labor_share * 100)}%",
            "better_off": sum(1 for g in gains if g >= 0),
            "worse_off": sum(1 for g in gains if g < 0),
            "min_gain": min(gains) if gains else 0.0,
        }
    sum_individual = float(sum(individual_income.get(m, 0.0) for m in members))
    return {
        "members": rows,
        "rules": summary,
        "coop_net": coop_net,
        "sum_individual": sum_individual,
        "surplus": coop_net - sum_individual,
        "per_pyeong": coop_net / (total_area / M2_PER_PYEONG) if total_area else 0.0,
        "member_area_ha": total_area / 1e4,
        "grand_value_estimate": game.value(members),
        "samples": samples,
    }
