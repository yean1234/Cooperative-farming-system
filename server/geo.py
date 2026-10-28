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


class UTMK:
    """UTM-K(EPSG:5179, GRS80 횡메르카토르) ↔ 경위도. 팜맵·연속지적도 SHP가 이 좌표계다.

    Snyder(1987) 급수식이다. 중앙경선(127.5°)에서 3° 안쪽이면 오차가 cm 이하다.
    GRS80과 WGS84 차이(수 mm)는 무시한다.
    """

    a = 6378137.0
    f = 1 / 298.257222101
    lon0, lat0, k0, fe, fn = 127.5, 38.0, 0.9996, 1_000_000.0, 2_000_000.0

    def __init__(self) -> None:
        e2 = self.f * (2 - self.f)
        self.e2, self.ep2 = e2, e2 / (1 - e2)
        self.m_coef = (
            1 - e2 / 4 - 3 * e2**2 / 64 - 5 * e2**3 / 256,
            3 * e2 / 8 + 3 * e2**2 / 32 + 45 * e2**3 / 1024,
            15 * e2**2 / 256 + 45 * e2**3 / 1024,
            35 * e2**3 / 3072,
        )
        self.m0 = self._arc(math.radians(self.lat0))

    def _arc(self, phi):
        c0, c2, c4, c6 = self.m_coef
        return self.a * (c0 * phi - c2 * np.sin(2 * phi) + c4 * np.sin(4 * phi) - c6 * np.sin(6 * phi))

    def to_xy(self, lonlat) -> np.ndarray:
        ll = np.asarray(lonlat, dtype=float).reshape(-1, 2)
        phi, lam = np.radians(ll[:, 1]), np.radians(ll[:, 0] - self.lon0)
        s, c, t = np.sin(phi), np.cos(phi), np.tan(phi)
        N = self.a / np.sqrt(1 - self.e2 * s * s)
        T, C, A = t * t, self.ep2 * c * c, lam * c
        x = self.fe + self.k0 * N * (A + (1 - T + C) * A**3 / 6 + (5 - 18 * T + T * T + 72 * C - 58 * self.ep2) * A**5 / 120)
        y = self.fn + self.k0 * (
            self._arc(phi) - self.m0
            + N * t * (A**2 / 2 + (5 - T + 9 * C + 4 * C * C) * A**4 / 24 + (61 - 58 * T + T * T + 600 * C - 330 * self.ep2) * A**6 / 720)
        )
        return np.column_stack((x, y))

    def to_lonlat(self, xy) -> np.ndarray:
        p = np.asarray(xy, dtype=float).reshape(-1, 2)
        e2, ep2 = self.e2, self.ep2
        mu = (self.m0 + (p[:, 1] - self.fn) / self.k0) / (self.a * self.m_coef[0])
        e1 = (1 - math.sqrt(1 - e2)) / (1 + math.sqrt(1 - e2))
        phi1 = (
            mu
            + (3 * e1 / 2 - 27 * e1**3 / 32) * np.sin(2 * mu)
            + (21 * e1**2 / 16 - 55 * e1**4 / 32) * np.sin(4 * mu)
            + (151 * e1**3 / 96) * np.sin(6 * mu)
            + (1097 * e1**4 / 512) * np.sin(8 * mu)
        )
        s, c, t = np.sin(phi1), np.cos(phi1), np.tan(phi1)
        C1, T1 = ep2 * c * c, t * t
        N1 = self.a / np.sqrt(1 - e2 * s * s)
        R1 = self.a * (1 - e2) / (1 - e2 * s * s) ** 1.5
        D = (p[:, 0] - self.fe) / (N1 * self.k0)
        lat = phi1 - (N1 * t / R1) * (
            D**2 / 2
            - (5 + 3 * T1 + 10 * C1 - 4 * C1**2 - 9 * ep2) * D**4 / 24
            + (61 + 90 * T1 + 298 * C1 + 45 * T1**2 - 252 * ep2 - 3 * C1**2) * D**6 / 720
        )
        lon = (D - (1 + 2 * T1 + C1) * D**3 / 6 + (5 - 2 * C1 + 28 * T1 - 3 * C1**2 + 8 * ep2 + 24 * T1**2) * D**5 / 120) / c
        return np.column_stack((self.lon0 + np.degrees(lon), np.degrees(lat)))


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


def sample_boundary(ring, step: float = 2.0, normals: bool = False):
    """경계를 step(m) 이하 조각으로 나눠 각 조각의 중점과 길이를 돌려준다. normals면 바깥쪽 단위 법선도."""
    r = open_ring(ring)
    ccw = (np.dot(r[:, 0], np.roll(r[:, 1], -1)) - np.dot(r[:, 1], np.roll(r[:, 0], -1))) > 0
    sign = 1.0 if ccw else -1.0
    pts, wts, nrm = [], [], []
    for p, q in zip(r, np.roll(r, -1, axis=0)):
        length = float(np.hypot(*(q - p)))
        if length < 1e-9:
            continue
        k = max(1, int(math.ceil(length / step)))
        t = (np.arange(k) + 0.5) / k
        pts.append(p + t[:, None] * (q - p))
        wts.append(np.full(k, length / k))
        nrm.append(np.repeat([[sign * (q - p)[1] / length, -sign * (q - p)[0] / length]], k, axis=0))
    if not pts:
        empty = (np.zeros((0, 2)), np.zeros(0))
        return (*empty, np.zeros((0, 2))) if normals else empty
    if normals:
        return np.vstack(pts), np.concatenate(wts), np.vstack(nrm)
    return np.vstack(pts), np.concatenate(wts)


def _nearest_on_segments(P: np.ndarray, A: np.ndarray, B: np.ndarray, with_points: bool = False):
    """점들 P에서 선분 집합(A→B)까지 최소거리와, 그 선분 위 투영 매개변수(클리핑 전). with_points면 최근접점도."""
    AB = B - A
    L2 = np.maximum((AB**2).sum(axis=1), 1e-12)
    AP = P[:, None, :] - A[None, :, :]
    t = (AP * AB[None, :, :]).sum(axis=2) / L2[None, :]
    tc = np.clip(t, 0.0, 1.0)
    proj = A[None, :, :] + tc[:, :, None] * AB[None, :, :]
    d = np.hypot(P[:, None, 0] - proj[:, :, 0], P[:, None, 1] - proj[:, :, 1])
    j = d.argmin(axis=1)
    rows = np.arange(len(P))
    if with_points:
        return d[rows, j], t[rows, j], proj[rows, j]
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
    경계 표본점 하나는 바깥쪽에 있는 가장 가까운 이웃 하나에만 센다. 좁은 필지가 겹겹이 붙은 실제 지도에서
    같은 경계를 여러 이웃에 중복으로 세거나 자기 필지를 가로질러 반대편 이웃에 세면,
    공유 길이 합이 둘레를 넘어 단지 형상지수가 1을 넘는다.

    반환값: (i, j, 공유경계 길이 m, 논두렁을 직접 맞대는지 여부) 목록, i < j
    """
    rs = [open_ring(r) for r in rings]
    n = len(rs)
    if n == 0:
        return []
    bb = np.array([[r[:, 0].min(), r[:, 1].min(), r[:, 0].max(), r[:, 1].max()] for r in rs])
    samples = [sample_boundary(r, step, normals=True) for r in rs]
    segs = [(r, np.roll(r, -1, axis=0)) for r in rs]
    near: dict[tuple[int, int], tuple[float, float]] = {}
    for i in range(n):
        P, W, N = samples[i]
        if len(P) == 0:
            continue
        cand = np.flatnonzero(
            (bb[:, 0] <= bb[i, 2] + tol)
            & (bb[:, 2] >= bb[i, 0] - tol)
            & (bb[:, 1] <= bb[i, 3] + tol)
            & (bb[:, 3] >= bb[i, 1] - tol)
        )
        cand = cand[cand != i]
        if len(cand) == 0:
            continue
        D = np.full((len(cand), len(P)), np.inf)
        for k, j in enumerate(cand):
            d, t, q = _nearest_on_segments(P, *segs[j], with_points=True)
            # 투영점이 상대 변의 안쪽에 있고 이 필지 바깥쪽에 있어야 '마주 본다'고 본다.
            # 경계가 사실상 겹치는 점(5cm 이내)은 예외.
            facing = ((t > end_eps) & (t < 1.0 - end_eps) & (((q - P) * N).sum(axis=1) > 0)) | (d <= 0.05)
            D[k] = np.where((d <= tol) & facing, d, np.inf)
        best = D.argmin(axis=0)
        dmin = D[best, np.arange(len(P))]
        hit = np.isfinite(dmin)
        for k in np.unique(best[hit]):
            m = hit & (best == k)
            near[(i, int(cand[k]))] = (float(W[m].sum()), float(W[m & (dmin <= touch_tol)].sum()))
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
