"""후보단지 시뮬레이터 API(/api/sites/*).

지도는 한 번 올리면 서버 메모리에 보관하고 region_id로 참조한다.
슬라이더를 움직일 때마다 수백 KB의 지도를 다시 보내지 않기 위해서다.
"""
from __future__ import annotations

import threading
import uuid
from collections import OrderedDict
from dataclasses import fields

from fastapi import APIRouter, Body, HTTPException
from pydantic import BaseModel, Field

from .region_demo import generate_region
from .selection import Region, SiteSettings, solve
from .selection_eval import validate

router = APIRouter(prefix="/api/sites", tags=["후보단지 시뮬레이터"])

_CACHE: "OrderedDict[str, Region]" = OrderedDict()
_LOCK = threading.Lock()
_MAX = 8


def _store(region: Region) -> str:
    rid = uuid.uuid4().hex[:12]
    with _LOCK:
        _CACHE[rid] = region
        while len(_CACHE) > _MAX:
            _CACHE.popitem(last=False)
    return rid


def _get(rid: str) -> Region:
    with _LOCK:
        region = _CACHE.get(rid)
    if region is None:
        raise HTTPException(status_code=404, detail="지도가 서버에서 만료됐습니다. 데이터를 다시 불러와 주세요.")
    return region


class SiteSettingsIn(BaseModel):
    a_min_ha: float = Field(20.0, ge=0.5, le=500)
    a_max_ha: float = Field(60.0, ge=1, le=2000)
    n_min: int = Field(5, ge=1, le=500)
    slope_max: float = Field(7.0, ge=0, le=60)
    compact_min: float = Field(0.20, ge=0, le=1)
    homog_min: float = Field(0.60, ge=0, le=1)
    require_road: bool = True
    include_upland: bool = True
    w_income: float = Field(1.0, ge=0, le=10)
    w_mech: float = Field(1.0, ge=0, le=10)
    w_consol: float = Field(1.0, ge=0, le=10)
    w_friction: float = Field(1.0, ge=0, le=10)
    owner_mode: str = Field("data", pattern="^(data|proxy)$")
    parcels_per_owner: float = Field(2.5, gt=0, le=50)

    def to_settings(self) -> SiteSettings:
        d = self.model_dump()
        if d["a_max_ha"] < d["a_min_ha"]:
            raise HTTPException(status_code=422, detail="최대 단지 면적이 최소 단지 면적보다 작습니다.")
        names = {f.name for f in fields(SiteSettings)}
        return SiteSettings(**{k: v for k, v in d.items() if k in names})


class DemoIn(BaseModel):
    seed: int = Field(11, ge=0, le=10_000_000)
    n_farmers: int = Field(150, ge=10, le=2000)


class SolveIn(BaseModel):
    region_id: str
    settings: SiteSettingsIn = Field(default_factory=SiteSettingsIn)


def _payload(scn: dict, region: Region, rid: str) -> dict:
    meta = dict(scn.get("meta") or {})
    meta.setdefault("name", "불러온 지도")
    meta["n_parcels"] = region.n
    meta["total_area_ha"] = round(float(region.area.sum()) / 1e4, 1)
    meta["has_owner"] = region.has_owner
    meta["truth_complexes"] = int(region.truth.max())
    return {"region_id": rid, "meta": meta, "parcels": scn["parcels"]}


@router.post("/demo")
def demo(req: DemoIn | None = None) -> dict:
    req = req or DemoIn()
    scn = generate_region(seed=req.seed, n_farmers=req.n_farmers)
    region = Region(scn)
    return _payload(scn, region, _store(region))


@router.post("/import")
def import_map(payload: dict = Body(...)) -> dict:
    """팜맵 등에서 만든 GeoJSON(EPSG:4326)을 받는다. 속성: slope, drainage, soil_grade, road_access, land_type, land_use, owner."""
    scn = {"parcels": payload} if payload.get("type") == "FeatureCollection" else dict(payload)
    if not (scn.get("parcels") or {}).get("features"):
        raise HTTPException(status_code=422, detail="필지 FeatureCollection이 비어 있습니다.")
    try:
        region = Region(scn)
    except (ValueError, KeyError, TypeError, IndexError) as exc:
        raise HTTPException(status_code=422, detail=f"지도를 읽지 못했습니다: {exc}") from exc
    warnings = []
    props = [f.get("properties") or {} for f in scn["parcels"]["features"]]
    for key, msg in (
        ("slope", "경사(slope) 속성이 없어 0%로 보았습니다."),
        ("road_access", "도로 접속(road_access) 속성이 없어 '없음'으로 보았습니다. 도로 접속 필수를 끄거나 속성을 채워 주세요."),
        ("drainage", "배수등급(drainage) 속성이 없어 2(보통)로 보았습니다."),
    ):
        if not any(key in p for p in props):
            warnings.append(msg)
    if not region.has_owner:
        warnings.append("소유자(owner) 속성이 없어 참여 농가 수를 필지 수로 추정합니다(대리 지표).")
    scn.setdefault("meta", {})["warnings"] = warnings
    return _payload(scn, region, _store(region))


@router.post("/solve")
def solve_sites(req: SolveIn) -> dict:
    return solve(_get(req.region_id), req.settings.to_settings())


@router.post("/validate")
def validate_sites(req: SolveIn) -> dict:
    region = _get(req.region_id)
    if region.truth.max() == 0:
        raise HTTPException(status_code=422, detail="정답 단지(truth_complex) 속성이 없어 재현 검증을 할 수 없습니다.")
    return validate(region, req.settings.to_settings())
