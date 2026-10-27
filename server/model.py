"""시나리오 JSON과 내부 모델(Field) 사이의 변환.

시나리오 JSON 형식
{
  "meta": {"name": str, "origin": [lon, lat], "synthetic": bool, ...},
  "parcels": GeoJSON FeatureCollection (EPSG:4326, Polygon/MultiPolygon),
  "farmers": [{"id", "name", "age", "member", "home": [lon, lat], "labor_days"}],
  "depot": [lon, lat],              # 법인 농기계 보관소(없으면 필지 중심)
  "adjacency": [[i, j, 길이, 맞닿음], ...]   # 선택. 없으면 서버가 계산
}
필지 properties: id, land_type(답/전), soil_grade(1~4), drainage(1~3), irrigated,
land_value(원/㎡), owner, exchangeable, crop_locked, current_crop
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .geo import LocalProjection, adjacency, open_ring, ring_area, ring_centroid
from .params import ALL_CROPS, SOIL_MULT

LAND_TYPE_ALIASES = {"답": "답", "논": "답", "paddy": "답", "전": "전", "밭": "전", "upland": "전"}


@dataclass
class Parcel:
    id: str
    xy: np.ndarray
    ring_lonlat: list
    area: float
    cx: float
    cy: float
    land_type: str = "답"
    soil_grade: int = 2
    drainage: int = 2
    irrigated: bool = True
    land_value: float = 30_000.0
    owner: str = ""
    exchangeable: bool = True
    crop_locked: bool = False
    current_crop: str = "벼"


@dataclass
class Farmer:
    id: str
    name: str
    age: int
    member: bool
    home: np.ndarray
    labor_days: float


class Field:
    """들녘 한 곳의 필지·농가·인접 그래프."""

    def __init__(self, parcels, farmers, depot_xy, proj, edges, meta):
        self.parcels: list[Parcel] = parcels
        self.n = len(parcels)
        self.ids = [p.id for p in parcels]
        self.index = {p.id: i for i, p in enumerate(parcels)}
        self.area = np.array([p.area for p in parcels])
        self.cx = np.array([p.cx for p in parcels])
        self.cy = np.array([p.cy for p in parcels])
        # 공시지가 기준 토지가액(원). 1:1 교환 비과세 요건(가액 차이 1/4) 판단에 쓴다.
        self.value = np.array([p.area * p.land_value for p in parcels])
        # 교환 평가가치 = 공시지가 × 면적 × 토양 생산력 계수. 경작자별 가치 보전 제약에 쓴다.
        self.qvalue = np.array([p.area * p.land_value * SOIL_MULT.get(p.soil_grade, 0.85) for p in parcels])
        self.farmers: dict[str, Farmer] = farmers
        self.depot = np.asarray(depot_xy, dtype=float)
        self.proj: LocalProjection = proj
        self.edges: list[tuple[int, int, float, bool]] = edges
        self.nbrs: list[list[tuple[int, float, bool]]] = [[] for _ in range(self.n)]
        for i, j, length, touch in edges:
            self.nbrs[i].append((j, length, touch))
            self.nbrs[j].append((i, length, touch))
        self.nbr_idx = [[j for j, _, _ in nb] for nb in self.nbrs]
        self.nbr_touch = [[j for j, _, t in nb if t] for nb in self.nbrs]
        self.meta = meta

    def to_lonlat(self, xy) -> list:
        return np.round(self.proj.to_lonlat(xy), 7).tolist()

    def members(self) -> list[str]:
        return [f.id for f in self.farmers.values() if f.member]


def _outer_ring(geom: dict):
    gtype = (geom or {}).get("type")
    if gtype == "Polygon":
        return geom["coordinates"][0]
    if gtype == "MultiPolygon":
        return max((poly[0] for poly in geom["coordinates"]), key=ring_area)
    raise ValueError(f"지원하지 않는 geometry 타입입니다: {gtype}")


def field_from_scenario(scn: dict) -> Field:
    warnings: list[str] = []
    feats = (scn.get("parcels") or {}).get("features") or []
    if not feats:
        raise ValueError("필지(parcels.features)가 비어 있습니다.")
    rings_ll = [np.asarray(_outer_ring(f.get("geometry")), dtype=float) for f in feats]
    meta = dict(scn.get("meta") or {})
    origin = meta.get("origin")
    if not origin:
        allpts = np.vstack(rings_ll)
        origin = [float(allpts[:, 0].min()), float(allpts[:, 1].min())]
        meta["origin"] = origin
    proj = LocalProjection(*origin)

    parcels: list[Parcel] = []
    seen: set[str] = set()
    for k, (feat, rll) in enumerate(zip(feats, rings_ll)):
        pr = feat.get("properties") or {}
        pid = str(pr.get("id") or feat.get("id") or f"P{k + 1:03d}")
        if pid in seen:
            raise ValueError(f"필지 id가 중복됩니다: {pid}")
        seen.add(pid)
        land_type = LAND_TYPE_ALIASES.get(str(pr.get("land_type", "답")), None)
        if land_type is None:
            raise ValueError(f"{pid}: land_type은 답/전 중 하나여야 합니다 (입력값 {pr.get('land_type')}).")
        crop = str(pr.get("current_crop") or ("벼" if land_type == "답" else "콩"))
        if crop not in ALL_CROPS:
            raise ValueError(f"{pid}: current_crop '{crop}'은(는) 지원 작목이 아닙니다. 가능: {', '.join(ALL_CROPS)}")
        owner = str(pr.get("owner") or "")
        if not owner:
            raise ValueError(f"{pid}: owner(소유·경작 농가 id)가 없습니다.")
        xy = open_ring(proj.to_xy(rll))
        c = ring_centroid(xy)
        ring_ll = open_ring(rll).tolist()
        ring_ll.append(ring_ll[0])
        parcels.append(
            Parcel(
                id=pid,
                xy=xy,
                ring_lonlat=ring_ll,
                area=ring_area(xy),
                cx=float(c[0]),
                cy=float(c[1]),
                land_type=land_type,
                soil_grade=int(np.clip(int(pr.get("soil_grade", 2)), 1, 4)),
                drainage=int(np.clip(int(pr.get("drainage", 2)), 1, 3)),
                irrigated=bool(pr.get("irrigated", land_type == "답")),
                land_value=float(pr.get("land_value", 30_000)),
                owner=owner,
                exchangeable=bool(pr.get("exchangeable", True)),
                crop_locked=bool(pr.get("crop_locked", crop == "시설채소")),
                current_crop=crop,
            )
        )

    # 농가
    owners = sorted({p.owner for p in parcels})
    farmers: dict[str, Farmer] = {}
    for fr in scn.get("farmers") or []:
        fid = str(fr["id"])
        home = fr.get("home")
        farmers[fid] = Farmer(
            id=fid,
            name=str(fr.get("name") or fid),
            age=int(fr.get("age", 65)),
            member=bool(fr.get("member", True)),
            home=proj.to_xy(home)[0] if home else None,
            labor_days=float(fr.get("labor_days", 30)),
        )
    missing = [o for o in owners if o not in farmers]
    if missing:
        warnings.append(f"농가 정보가 없는 소유자 {len(missing)}명은 조합원으로 간주했습니다.")
        for o in missing:
            farmers[o] = Farmer(id=o, name=o, age=65, member=True, home=None, labor_days=30)
    for f in farmers.values():
        if f.home is None:
            idx = [i for i, p in enumerate(parcels) if p.owner == f.id]
            if idx:
                a = np.array([parcels[i].area for i in idx])
                f.home = np.array(
                    [np.average([parcels[i].cx for i in idx], weights=a), np.average([parcels[i].cy for i in idx], weights=a)]
                )
            else:
                f.home = np.zeros(2)
    # 필지가 없는 농가는 제외
    owner_set = set(owners)
    farmers = {k: v for k, v in farmers.items() if k in owner_set}

    depot = scn.get("depot")
    if depot:
        depot_xy = proj.to_xy(depot)[0]
    else:
        warnings.append("법인 농기계 보관소(depot) 위치가 없어 필지 중심을 사용했습니다.")
        a = np.array([p.area for p in parcels])
        depot_xy = np.array([np.average([p.cx for p in parcels], weights=a), np.average([p.cy for p in parcels], weights=a)])

    edges = None
    cached = scn.get("adjacency")
    if cached:
        try:
            edges = [(int(i), int(j), float(length), bool(t)) for i, j, length, t in cached]
            if any(not (0 <= i < len(parcels) and 0 <= j < len(parcels)) for i, j, _, _ in edges):
                edges = None
        except (TypeError, ValueError):
            edges = None
    if edges is None:
        edges = adjacency([p.xy for p in parcels])

    meta["warnings"] = list(dict.fromkeys(list(meta.get("warnings") or []) + warnings))
    return Field(parcels, farmers, depot_xy, proj, edges, meta)
