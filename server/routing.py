"""작업 동선 계산: 출발지에서 블록들을 돌고 돌아오는 순회 경로."""
from __future__ import annotations

import numpy as np


def _dist_matrix(pts: np.ndarray) -> np.ndarray:
    d = pts[:, None, :] - pts[None, :, :]
    return np.hypot(d[..., 0], d[..., 1])


def tour(base, pts) -> tuple[list[int], float]:
    """최근접 이웃으로 시작해 2-opt로 다듬은 순회 경로. (방문 순서, 총 직선거리 m)"""
    pts = np.asarray(pts, dtype=float).reshape(-1, 2)
    n = len(pts)
    if n == 0:
        return [], 0.0
    nodes = np.vstack([np.asarray(base, dtype=float).reshape(1, 2), pts])
    D = _dist_matrix(nodes)
    route = [0]
    left = set(range(1, n + 1))
    while left:
        last = route[-1]
        nxt = min(left, key=lambda j: D[last, j])
        route.append(nxt)
        left.remove(nxt)
    m = len(route)
    improved = True
    while improved and m > 3:
        improved = False
        for i in range(1, m - 1):
            for k in range(i + 1, m):
                a, b = route[i - 1], route[i]
                c, d = route[k], route[(k + 1) % m]
                if D[a, c] + D[b, d] < D[a, b] + D[c, d] - 1e-9:
                    route[i : k + 1] = route[i : k + 1][::-1]
                    improved = True
    length = float(sum(D[route[t], route[(t + 1) % m]] for t in range(m)))
    return [r - 1 for r in route[1:]], length


def mst_length(pts) -> float:
    """최소신장트리 길이(Prim). 순회 거리의 빠른 근사에 쓴다."""
    pts = np.asarray(pts, dtype=float).reshape(-1, 2)
    n = len(pts)
    if n <= 1:
        return 0.0
    D = _dist_matrix(pts)
    in_tree = np.zeros(n, dtype=bool)
    in_tree[0] = True
    best = D[0].copy()
    total = 0.0
    for _ in range(n - 1):
        cand = np.where(in_tree, np.inf, best)
        j = int(cand.argmin())
        total += float(cand[j])
        in_tree[j] = True
        best = np.minimum(best, D[j])
    return total
