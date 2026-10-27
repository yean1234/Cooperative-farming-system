"""경작권 교환 최적화.

소유권은 그대로 두고 경작권(임대차)만 맞바꾸는 방식을 기본으로 한다.

- 1:1 상호이익 교환(기준선): 두 경작자가 필지를 하나씩 맞바꾸되 양쪽 모두 나아질 때만 성사된다.
  농가끼리 자율 교환할 때 생기는 '이중 일치(double coincidence of wants)' 제약을 재현한다
  (Arimoto, Nakajima & Tomita, 2016). 쌍방 가액 차이가 큰 쪽의 1/4 이하라는 요건도 적용한다
  (농지 교환 양도세 비과세 요건, 소득세법 시행령 제153조).
- 다자간 교환(제안): 플랫폼이 여러 경작자의 교환을 한꺼번에 설계한다. 담금질 기법을 쓴다.
  제약: 경작자별 면적·가치 변화가 허용오차 이내, 고정 필지 제외,
  개별 농가의 분산 점수가 교환 전보다 나빠지지 않을 것(개별 합리성).

경작자 분산 점수 = w_block·(블록 수 - 1) + w_disp·(면적가중 평균 분산거리/100m) + w_base·(거점까지 평균거리/100m)
"""
from __future__ import annotations

import math
import time
from dataclasses import dataclass, replace

import numpy as np

from .params import COOP_ID


@dataclass
class ExchangeSettings:
    area_tol: float = 0.10       # 경작자별 면적 변화 허용(비율)
    value_tol: float = 0.10      # 경작자별 평가가치(공시지가×면적×토양계수) 변화 허용(비율)
    w_block: float = 1.0         # 블록 하나 늘 때마다의 점수
    w_disp: float = 0.5          # 분산거리 100m당 점수
    w_base: float = 0.2          # 거점(집·농기계 보관소)까지 평균거리 100m당 점수
    coop_weight: float = 2.0     # 법인 점수 가중치(대형 기계 작업이 많음)
    w_change: float = 0.15       # 경작자가 바뀐 필지 1개당 비용(합의 부담)
    ir_penalty: float = 5.0      # 개별 합리성 위반 벌점(점수 1당)
    iterations: int = 20000
    pair_value_rule: float = 0.25
    seed: int = 0


@dataclass
class ExchangeResult:
    method: str
    assign: list[str]
    changed: list[dict]
    cultivators: list[dict]
    stats: dict


class Book:
    """경작자별 필지 집합, 면적·가치 합계, 분산 점수를 관리한다."""

    def __init__(self, field, assign_ids, exchangeable, bases, coop_id, s: ExchangeSettings):
        self.f = field
        self.s = s
        self.coop_id = coop_id
        self.cults = list(dict.fromkeys(assign_ids))
        self.k_of = {c: k for k, c in enumerate(self.cults)}
        self.orig = np.array([self.k_of[c] for c in assign_ids], dtype=int)
        self.exch = np.asarray(exchangeable, dtype=bool)
        self.base = np.array([bases[c] for c in self.cults], dtype=float)
        self.weight = np.array([s.coop_weight if c == coop_id else 1.0 for c in self.cults])
        self.indiv = np.array([c != coop_id for c in self.cults])
        self.reset_to(self.orig)
        self.A0 = self.A.copy()
        self.V0 = self.V.copy()
        self.score0 = self.score.copy()
        self.blocks0 = [self.n_blocks(st) for st in self.sets]

    # ---- 상태 관리 -------------------------------------------------------
    def reset_to(self, assign) -> None:
        self.assign = np.array(assign, dtype=int)
        m = len(self.cults)
        self.sets = [set() for _ in range(m)]
        for i, k in enumerate(self.assign):
            self.sets[k].add(int(i))
        self.A = np.array([self.f.area[list(st)].sum() if st else 0.0 for st in self.sets])
        self.V = np.array([self.f.qvalue[list(st)].sum() if st else 0.0 for st in self.sets])
        self.score = np.array([self.calc(k, self.sets[k]) for k in range(m)])

    def n_blocks(self, pset) -> int:
        seen: set[int] = set()
        count = 0
        nbr = self.f.nbr_idx
        for s in pset:
            if s in seen:
                continue
            count += 1
            seen.add(s)
            stack = [s]
            while stack:
                u = stack.pop()
                for v in nbr[u]:
                    if v in pset and v not in seen:
                        seen.add(v)
                        stack.append(v)
        return count

    def calc(self, k: int, pset) -> float:
        if not pset:
            return 1e6
        idx = np.fromiter(pset, dtype=int, count=len(pset))
        a = self.f.area[idx]
        total = a.sum()
        x = self.f.cx[idx]
        y = self.f.cy[idx]
        mx = (a * x).sum() / total
        my = (a * y).sum() / total
        disp = (a * np.hypot(x - mx, y - my)).sum() / total
        bx, by = self.base[k]
        dbase = (a * np.hypot(x - bx, y - by)).sum() / total
        s = self.s
        return s.w_block * (self.n_blocks(pset) - 1) + s.w_disp * disp / 100.0 + s.w_base * dbase / 100.0

    def within(self, k: int, area: float, value: float) -> bool:
        s = self.s
        return (
            abs(area - self.A0[k]) <= s.area_tol * self.A0[k] + 1e-6
            and abs(value - self.V0[k]) <= s.value_tol * self.V0[k] + 1e-6
        )

    def penalty(self, k: int, sc: float) -> float:
        if not self.indiv[k]:
            return 0.0
        return self.s.ir_penalty * max(0.0, sc - self.score0[k])

    def objective(self) -> float:
        j = float((self.weight * self.score).sum())
        j += self.s.w_change * int((self.assign != self.orig).sum())
        j += sum(self.penalty(k, self.score[k]) for k in range(len(self.cults)))
        return j

    # ---- 이동(교환·이전) 평가 ----------------------------------------------
    def _delta(self, f, h, sf, sh, dchg) -> float:
        return (
            self.weight[f] * (sf - self.score[f])
            + self.weight[h] * (sh - self.score[h])
            + self.s.w_change * dchg
            + self.penalty(f, sf)
            - self.penalty(f, self.score[f])
            + self.penalty(h, sh)
            - self.penalty(h, self.score[h])
        )

    def eval_swap(self, p: int, q: int):
        f, h = int(self.assign[p]), int(self.assign[q])
        if f == h or not (self.exch[p] and self.exch[q]):
            return None
        ar, val = self.f.area, self.f.qvalue
        af = self.A[f] - ar[p] + ar[q]
        ah = self.A[h] - ar[q] + ar[p]
        vf = self.V[f] - val[p] + val[q]
        vh = self.V[h] - val[q] + val[p]
        if not (self.within(f, af, vf) and self.within(h, ah, vh)):
            return None
        sf_set = set(self.sets[f])
        sf_set.discard(p)
        sf_set.add(q)
        sh_set = set(self.sets[h])
        sh_set.discard(q)
        sh_set.add(p)
        sf, sh = self.calc(f, sf_set), self.calc(h, sh_set)
        o = self.orig
        dchg = int(h != o[p]) - int(f != o[p]) + int(f != o[q]) - int(h != o[q])
        delta = self._delta(f, h, sf, sh, dchg)
        return delta, (p, q, f, h, sf_set, sh_set, sf, sh, af, ah, vf, vh)

    def eval_transfer(self, p: int, h: int):
        f = int(self.assign[p])
        if f == h or not self.exch[p] or len(self.sets[f]) <= 1:
            return None
        ar, val = self.f.area, self.f.qvalue
        af, ah = self.A[f] - ar[p], self.A[h] + ar[p]
        vf, vh = self.V[f] - val[p], self.V[h] + val[p]
        if not (self.within(f, af, vf) and self.within(h, ah, vh)):
            return None
        sf_set = set(self.sets[f])
        sf_set.discard(p)
        sh_set = set(self.sets[h])
        sh_set.add(p)
        sf, sh = self.calc(f, sf_set), self.calc(h, sh_set)
        dchg = int(h != self.orig[p]) - int(f != self.orig[p])
        delta = self._delta(f, h, sf, sh, dchg)
        return delta, (p, None, f, h, sf_set, sh_set, sf, sh, af, ah, vf, vh)

    def apply(self, upd) -> None:
        p, q, f, h, sf_set, sh_set, sf, sh, af, ah, vf, vh = upd
        self.assign[p] = h
        if q is not None:
            self.assign[q] = f
        self.sets[f], self.sets[h] = sf_set, sh_set
        self.score[f], self.score[h] = sf, sh
        self.A[f], self.A[h] = af, ah
        self.V[f], self.V[h] = vf, vh

    def boundary_candidates(self, h: int, f: int) -> list[int]:
        """h의 교환 가능 필지 중 f의 필지와 맞닿은 것."""
        a = self.assign
        nbr = self.f.nbr_idx
        return [q for q in self.sets[h] if self.exch[q] and any(a[j] == f for j in nbr[q])]

    def violators(self) -> list[int]:
        return [k for k in range(len(self.cults)) if self.indiv[k] and self.score[k] > self.score0[k] + 1e-9]

    def ir_violations(self) -> list[str]:
        return [self.cults[k] for k in self.violators()]

    def repair(self, max_rounds: int = 500) -> bool:
        """개별 합리성 위반 농가의 교환을 되돌리는 방향으로 보정한다. 위반이 모두 풀리면 True."""
        saved = self.s
        self.s = replace(saved, ir_penalty=1000.0)
        stuck: set[int] = set()
        try:
            for _ in range(max_rounds):
                viol = [k for k in self.violators() if k not in stuck]
                if not viol:
                    break
                k = viol[0]
                received = [p for p in self.sets[k] if self.orig[p] != k]
                given = [q for q in range(self.f.n) if self.orig[q] == k and self.assign[q] != k]
                best = None
                for p in received:
                    cands = [self.eval_transfer(p, int(self.orig[p]))]
                    cands += [self.eval_swap(p, q) for q in given]
                    for r in cands:
                        if r is not None and (best is None or r[0] < best[0]):
                            best = r
                for q in given:
                    r = self.eval_transfer(q, k)
                    if r is not None and (best is None or r[0] < best[0]):
                        best = r
                if best is None or best[0] >= -1e-12:
                    stuck.add(k)
                    continue
                self.apply(best[1])
                stuck.clear()
        finally:
            self.s = saved
        return not self.violators()

    # ---- 결과 정리 ---------------------------------------------------------
    def result(self, method: str, stats: dict) -> ExchangeResult:
        ids = self.f.ids
        changed = [
            {
                "id": ids[i],
                "from": self.cults[self.orig[i]],
                "to": self.cults[self.assign[i]],
                "area_ha": round(float(self.f.area[i]) / 1e4, 4),
            }
            for i in range(self.f.n)
            if self.assign[i] != self.orig[i]
        ]
        cults = []
        for k, c in enumerate(self.cults):
            cults.append(
                {
                    "id": c,
                    "area0_ha": round(float(self.A0[k]) / 1e4, 4),
                    "area_ha": round(float(self.A[k]) / 1e4, 4),
                    "value0": float(self.V0[k]),
                    "value": float(self.V[k]),
                    "blocks0": int(self.blocks0[k]),
                    "blocks": int(self.n_blocks(self.sets[k])),
                    "score0": round(float(self.score0[k]), 4),
                    "score": round(float(self.score[k]), 4),
                    "ir_ok": bool((not self.indiv[k]) or self.score[k] <= self.score0[k] + 1e-9),
                    "given": int(sum(1 for i in range(self.f.n) if self.orig[i] == k and self.assign[i] != k)),
                    "received": int(sum(1 for i in range(self.f.n) if self.assign[i] == k and self.orig[i] != k)),
                }
            )
        stats = dict(stats)
        stats["changed_parcels"] = len(changed)
        stats["changed_area_ha"] = round(sum(c["area_ha"] for c in changed), 3)
        stats["ir_violations"] = self.ir_violations()
        return ExchangeResult(
            method=method,
            assign=[self.cults[k] for k in self.assign],
            changed=changed,
            cultivators=cults,
            stats=stats,
        )


def pairwise_exchange(field, assign_ids, exchangeable, bases, coop_id=COOP_ID, s=None, max_passes=30) -> ExchangeResult:
    """1:1 상호이익 교환을 더 이상 성사될 거래가 없을 때까지 반복한다."""
    s = s or ExchangeSettings()
    t0 = time.perf_counter()
    book = Book(field, assign_ids, exchangeable, bases, coop_id, s)
    rng = np.random.default_rng(s.seed)
    ex = np.flatnonzero(book.exch)
    j_start = book.objective()
    swaps = passes = 0
    improved = True
    while improved and passes < max_passes:
        improved = False
        passes += 1
        for p in rng.permutation(ex):
            p = int(p)
            f = int(book.assign[p])
            for h in sorted({int(book.assign[j]) for j in field.nbr_idx[p]} - {f}):
                best = None
                for q in book.boundary_candidates(h, f):
                    vp, vq = field.value[p], field.value[q]
                    if abs(vp - vq) > s.pair_value_rule * max(vp, vq):
                        continue
                    r = book.eval_swap(p, q)
                    if r is None:
                        continue
                    upd = r[1]
                    gain_f = book.score[f] - upd[6]
                    gain_h = book.score[h] - upd[7]
                    if gain_f > 1e-9 and gain_h > 1e-9 and (best is None or gain_f + gain_h > best[0]):
                        best = (gain_f + gain_h, upd)
                if best is not None:
                    book.apply(best[1])
                    swaps += 1
                    improved = True
                    break
    stats = {
        "swaps": swaps,
        "passes": passes,
        "objective0": round(j_start, 4),
        "objective": round(book.objective(), 4),
        "seconds": round(time.perf_counter() - t0, 3),
    }
    return book.result("pairwise", stats)


def multiparty_exchange(
    field, assign_ids, exchangeable, bases, coop_id=COOP_ID, s=None, starts=None
) -> ExchangeResult:
    """플랫폼이 설계하는 다자간 교환. starts(시작 배정 목록)마다 담금질을 돌려 가장 나은 해를 고른다."""
    s = s or ExchangeSettings()
    t0 = time.perf_counter()
    best = None
    for k, init in enumerate(starts or [None]):
        res = _anneal(field, assign_ids, exchangeable, bases, coop_id, replace(s, seed=s.seed + k), init)
        if best is None or res.stats["objective"] < best.stats["objective"] - 1e-9:
            best = res
    best.stats["starts"] = len(starts or [None])
    best.stats["seconds"] = round(time.perf_counter() - t0, 3)
    return best


def _anneal(field, assign_ids, exchangeable, bases, coop_id, s, init_assign) -> ExchangeResult:
    t0 = time.perf_counter()
    book = Book(field, assign_ids, exchangeable, bases, coop_id, s)
    if init_assign is not None:
        start = np.array([book.k_of[c] for c in init_assign], dtype=int)
        book.reset_to(start)
        if book.violators():
            book.reset_to(book.orig)
    rng = np.random.default_rng(s.seed)
    ex = np.flatnonzero(book.exch)
    m = len(book.cults)
    j_start = book.objective()
    if len(ex) < 2 or m < 2:
        return book.result("multiparty", {"iterations": 0, "objective0": j_start, "objective": j_start, "seconds": 0.0})
    j_orig = float((book.weight * book.score0).sum())

    def propose():
        p = int(ex[rng.integers(len(ex))])
        f = int(book.assign[p])
        hs = sorted({int(book.assign[j]) for j in field.nbr_idx[p]} - {f})
        if hs and rng.random() < 0.85:
            h = hs[int(rng.integers(len(hs)))]
        else:
            h = int(rng.integers(m - 1))
            h = h + 1 if h >= f else h
        if rng.random() < 0.2:
            return book.eval_transfer(p, h)
        cand = book.boundary_candidates(h, f)
        if not cand or rng.random() > 0.75:
            cand = [q for q in book.sets[h] if book.exch[q]]
            if not cand:
                return None
        q = cand[int(rng.integers(len(cand)))]
        return book.eval_swap(p, q)

    worse = []
    for _ in range(300):
        r = propose()
        if r is not None and r[0] > 1e-9:
            worse.append(r[0])
    t_hi = float(np.median(worse)) / math.log(2) if worse else 1.0
    t_lo = t_hi * 1e-3

    j = j_start
    best_j, best_assign = j, book.assign.copy()       # 위반 여부와 무관한 최선해
    feas_j, feas_assign = j, book.assign.copy()       # 개별 합리성을 지킨 최선해(시작점은 항상 만족)
    accepted = 0
    n_iter = max(1, int(s.iterations))
    for it in range(n_iter):
        temp = t_hi * (t_lo / t_hi) ** (it / max(1, n_iter - 1))
        r = propose()
        if r is None:
            continue
        delta, upd = r
        if delta <= 0 or rng.random() < math.exp(-delta / temp):
            book.apply(upd)
            j += delta
            accepted += 1
            if j < best_j - 1e-12:
                best_j, best_assign = j, book.assign.copy()
            if j < feas_j - 1e-12 and not book.violators():
                feas_j, feas_assign = j, book.assign.copy()

    # 위반이 남은 최선해를 보정해 보고, 위반 없는 최선해보다 나쁘면 후자를 쓴다.
    book.reset_to(best_assign)
    repaired = False
    if book.violators():
        repaired = book.repair()
    if book.violators() or book.objective() > feas_j + 1e-9:
        book.reset_to(feas_assign)

    stats = {
        "iterations": n_iter,
        "accepted": accepted,
        "objective0": round(j_orig, 4),
        "objective": round(book.objective(), 4),
        "temperature": [round(t_hi, 4), round(t_lo, 6)],
        "ir_repair": repaired,
        "seconds": round(time.perf_counter() - t0, 3),
    }
    return book.result("multiparty", stats)
