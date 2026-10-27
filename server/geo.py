"""좌표 변환과 필지 기하 계산. 외부 GIS 라이브러리 없이 numpy만 쓴다."""
from __future__ import annotations

import math

import numpy as np

M_PER_DEG_LAT = 110_574.0
M_PER_DEG_LON_EQ = 111_320.0


class LocalProjection:
    """마을 규모(수 km)용 등장방형 근사 투영. 이 범위에서 오차는 무시할 수준이다."""

    def __init__(self, lon0: float, lat0: float) -> None:
        self.lon0 = float(lon0)
        self.lat0 = float(lat0)
        self.kx = M_PER_DEG_LON_EQ * math.cos(math.radians(self.lat0))
        self.ky = M_PER_DEG_LAT

    def to_xy(self, lonlat) -> np.ndarray:
        a = np.asarray(lonlat, dtype=float).reshape(-1, 2)
        return np.column_stack(((a[:, 0] - self.lon0) * self.kx, (a[:, 1] - self.lat0) * self.ky))

    def to_lonlat(self, xy) -> np.ndarray:
        a = np.asarray(xy, dtype=float).reshape(-1, 2)
        return np.column_stack((a[:, 0] / self.kx + self.lon0, a[:, 1] / self.ky + self.lat0))


def open_ring(ring) -> np.ndarray:
    """닫힌 링이면 마지막 중복 점을 뺀다."""
    r = np.asarray(ring, dtype=float).reshape(-1, 2)
    if len(r) > 1 and np.allclose(r[0], r[-1]):
        r = r[:-1]
    return r


def ring_area(ring) -> float:
    r = open_ring(ring)
    x, y = r[:, 0], r[:, 1]
    return 0.5 * abs(float(np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1))))


def ring_centroid(ring) -> np.ndarray:
    r = open_ring(ring)
    x, y = r[:, 0], r[:, 1]
    xn, yn = np.roll(x, -1), np.roll(y, -1)
    cross = x * yn - xn * y
    a = cross.sum() / 2.0
    if abs(a) < 1e-9:
        return r.mean(axis=0)
    return np.array([((x + xn) * cross).sum() / (6.0 * a), ((y + yn) * cross).sum() / (6.0 * a)])


def sample_boundary(ring, step: float = 2.0) -> tuple[np.ndarray, np.ndarray]:
    """경계를 step(m) 이하 조각으로 나눠 각 조각의 중점과 길이를 돌려준다."""
    r = open_ring(ring)
    pts, wts = [], []
    for p, q in zip(r, np.roll(r, -1, axis=0)):
        length = float(np.hypot(*(q - p)))
        if length < 1e-9:
            continue
        k = max(1, int(math.ceil(length / step)))
        t = (np.arange(k) + 0.5) / k
        pts.append(p + t[:, None] * (q - p))
        wts.append(np.full(k, length / k))
    if not pts:
        return np.zeros((0, 2)), np.zeros(0)
    return np.vstack(pts), np.concatenate(wts)


def _nearest_on_segments(P: np.ndarray, A: np.ndarray, B: np.ndarray):
    """점들 P에서 선분 집합(A→B)까지 최소거리와, 그 선분 위 투영 매개변수(클리핑 전)."""
    AB = B - A
    L2 = np.maximum((AB**2).sum(axis=1), 1e-12)
    AP = P[:, None, :] - A[None, :, :]
    t = (AP * AB[None, :, :]).sum(axis=2) / L2[None, :]
    tc = np.clip(t, 0.0, 1.0)
    proj = A[None, :, :] + tc[:, :, None] * AB[None, :, :]
    d = np.hypot(P[:, None, 0] - proj[:, :, 0], P[:, None, 1] - proj[:, :, 1])
    j = d.argmin(axis=1)
    rows = np.arange(len(P))
    return d[rows, j], t[rows, j]


def adjacency(
    rings,
    tol: float = 8.0,
    touch_tol: float = 1.5,
    step: float = 2.0,
    min_len: float = 5.0,
    end_eps: float = 0.02,
) -> list[tuple[int, int, float, bool]]:
    """필지 인접 관계를 계산한다.

    두 경계가 tol(m) 이내에서 서로 '마주 보는' 구간 길이를 공유 경계 길이로 본다.
    폭 tol 이하의 농로·수로를 사이에 둔 필지도 농기계가 곧바로 건너갈 수 있으므로 인접으로 친다.
    모서리만 맞닿은 대각선 필지는 투영점이 꼭짓점에 걸려 제외된다.

    반환값: (i, j, 공유경계 길이 m, 논두렁을 직접 맞대는지 여부) 목록, i < j
    """
    rs = [open_ring(r) for r in rings]
    n = len(rs)
    if n == 0:
        return []
    bb = np.array([[r[:, 0].min(), r[:, 1].min(), r[:, 0].max(), r[:, 1].max()] for r in rs])
    samples = [sample_boundary(r, step) for r in rs]
    segs = [(r, np.roll(r, -1, axis=0)) for r in rs]
    near: dict[tuple[int, int], tuple[float, float]] = {}
    for i in range(n):
        P, W = samples[i]
        if len(P) == 0:
            continue
        cand = np.flatnonzero(
            (bb[:, 0] <= bb[i, 2] + tol)
            & (bb[:, 2] >= bb[i, 0] - tol)
            & (bb[:, 1] <= bb[i, 3] + tol)
            & (bb[:, 3] >= bb[i, 1] - tol)
        )
        for j in cand:
            if j == i:
                continue
            d, t = _nearest_on_segments(P, *segs[j])
            # 투영점이 상대 변의 안쪽에 있어야 '마주 본다'고 본다. 경계가 사실상 겹치는 점(5cm 이내)은 예외.
            facing = ((t > end_eps) & (t < 1.0 - end_eps)) | (d <= 0.05)
            m = (d <= tol) & facing
            if m.any():
                near[(i, int(j))] = (float(W[m].sum()), float(W[m & (d <= touch_tol)].sum()))
    out = []
    for i, j in sorted({(min(a, b), max(a, b)) for a, b in near}):
        l_ij, t_ij = near.get((i, j), (0.0, 0.0))
        l_ji, t_ji = near.get((j, i), (0.0, 0.0))
        length = 0.5 * (l_ij + l_ji)
        if length < min_len:
            continue
        touch = 0.5 * (t_ij + t_ji) >= 0.5 * length
        out.append((i, j, round(length, 2), bool(touch)))
    return out


def clip_halfplane(poly: np.ndarray, p: np.ndarray, q: np.ndarray) -> np.ndarray:
    """볼록다각형 poly에서 점 p 쪽 반평면(p와 q의 수직이등분선 기준)만 남긴다."""
    if len(poly) == 0:
        return poly
    m = (p + q) / 2.0
    nvec = q - p
    s = (poly - m) @ nvec  # <= 0 이면 p 쪽
    out = []
    k = len(poly)
    for a in range(k):
        b = (a + 1) % k
        pa, pb, sa, sb = poly[a], poly[b], s[a], s[b]
        if sa <= 0:
            out.append(pa)
        if (sa <= 0) != (sb <= 0):
            t = sa / (sa - sb)
            out.append(pa + t * (pb - pa))
    return np.array(out) if out else np.zeros((0, 2))


def voronoi_cells(points: np.ndarray, rect: tuple[float, float, float, float]) -> list[np.ndarray]:
    """사각형 안으로 잘린 보로노이 셀(반평면 교집합 방식, 점 수가 적을 때 충분히 빠르다)."""
    x0, y0, x1, y1 = rect
    box = np.array([[x0, y0], [x1, y0], [x1, y1], [x0, y1]], dtype=float)
    cells = []
    for i, p in enumerate(points):
        poly = box.copy()
        for j, q in enumerate(points):
            if i != j:
                poly = clip_halfplane(poly, p, q)
                if len(poly) == 0:
                    break
        cells.append(poly)
    return cells
