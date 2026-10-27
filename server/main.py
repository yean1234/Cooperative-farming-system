"""FastAPI 앱. API는 /api 아래에, 웹 화면은 / 에서 제공한다."""
from __future__ import annotations

import mimetypes
from pathlib import Path

from fastapi import Body, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from .demo import generate_demo
from .model import field_from_scenario
from .params import ALL_CROPS, COOP_ID, COOP_NAME, CROP_ORDER, default_params
from .pipeline import run_pipeline
from .schemas import DemoRequest, OptimizeRequest, OptimizeSettings
from .sites_api import router as sites_router

# Windows 레지스트리에 .js가 text/plain으로 등록된 경우를 막는다(text/* 이면 charset=utf-8이 붙는다).
mimetypes.add_type("text/javascript", ".js")
mimetypes.add_type("text/css", ".css")

WEB_DIR = Path(__file__).resolve().parent.parent / "web"

app = FastAPI(
    title="공동영농 필지 최적화 API",
    version="0.2.0",
    description=(
        "/api/sites: 읍면 필지 지도에서 공동영농 후보단지를 제약 조건 하에 제안하는 시뮬레이터(주 기능). "
        "/api/scenario·/api/optimize: 선정된 단지의 운영 설계(경작권 교환·작목 배치·수익 배분) 확장 기능."
    ),
)
app.include_router(sites_router)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok"}


@app.get("/api/defaults")
def defaults() -> dict:
    params = default_params()
    params["soil_mult"] = {str(k): v for k, v in params["soil_mult"].items()}
    for c in params["crops"].values():
        c["drain_mult"] = {str(k): v for k, v in c["drain_mult"].items()}
    return {
        "settings": OptimizeSettings().model_dump(),
        "crop_order": CROP_ORDER,
        "all_crops": ALL_CROPS,
        "coop": {"id": COOP_ID, "name": COOP_NAME},
        "params": params,
    }


@app.post("/api/scenario/demo")
def scenario_demo(req: DemoRequest | None = None) -> dict:
    req = req or DemoRequest()
    return generate_demo(**req.model_dump())


@app.post("/api/scenario/import")
def scenario_import(payload: dict = Body(...)) -> dict:
    """GeoJSON FeatureCollection 또는 시나리오 JSON을 받아 검증하고 인접 관계를 계산해 돌려준다."""
    scn = {"parcels": payload} if payload.get("type") == "FeatureCollection" else dict(payload)
    scn.pop("adjacency", None)
    try:
        field = field_from_scenario(scn)
    except (ValueError, KeyError, TypeError, IndexError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    farmers = []
    for f in field.farmers.values():
        farmers.append(
            {
                "id": f.id,
                "name": f.name,
                "age": f.age,
                "member": f.member,
                "home": field.to_lonlat(f.home.reshape(1, 2))[0],
                "labor_days": f.labor_days,
            }
        )
    meta = dict(field.meta)
    meta.setdefault("name", "불러온 들녘")
    meta.setdefault("synthetic", False)
    meta["n_parcels"] = field.n
    meta["n_members"] = len(field.members())
    meta["total_area_ha"] = round(float(field.area.sum()) / 1e4, 2)
    member_set = set(field.members())
    meta["member_area_ha"] = round(
        float(sum(p.area for p in field.parcels if p.owner in member_set)) / 1e4, 2
    )
    return {
        "meta": meta,
        "parcels": scn["parcels"],
        "farmers": farmers,
        "depot": field.to_lonlat(field.depot.reshape(1, 2))[0],
        "adjacency": [[i, j, length, touch] for i, j, length, touch in field.edges],
    }


@app.post("/api/optimize")
def optimize(req: OptimizeRequest) -> dict:
    try:
        return run_pipeline(req.scenario, req.settings.model_dump())
    except (ValueError, KeyError, TypeError, IndexError) as exc:
        raise HTTPException(status_code=422, detail=f"시나리오를 처리할 수 없습니다: {exc}") from exc


if WEB_DIR.exists():
    app.mount("/", StaticFiles(directory=WEB_DIR, html=True), name="web")
