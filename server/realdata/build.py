"""실측 읍면 지도 JSON을 만든다. 결과는 /api/sites/import 와 같은 형식이라 화면에서 바로 불러온다.

    python -m server.realdata.build --farmmap-shp <시군 팜맵.shp> --bbox 129.3405,36.113,129.3743,36.135 \
        --name "포항시 흥해읍 들녘" --out data/regions/heunghae.json

키(DATA_GO_KR_KEY, VWORLD_KEY)가 .env에 있으면 흙토람·연속지적도 단계를 함께 돈다. 없으면 건너뛰고 meta에 남긴다.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import date
from pathlib import Path

import numpy as np

from ..geo import LocalProjection, adjacency
from . import dem, farmmap, http, osm, soil, vworld

SOURCES = {
    "farmmap": {
        "name": "팜맵 공간정보(농경지 전자지도)",
        "provider": "농림축산식품부·농림수산식품교육문화정보원",
        "url": "https://www.data.go.kr/data/15104488/fileData.do",
        "license": "공공데이터포털 이용허락범위 제한 없음",
        "fields": ["필지 경계", "논·밭·과수·시설 구분", "대표지목", "대표 PNU", "경지정리 여부"],
    },
    "osm": {
        "name": "OpenStreetMap 도로",
        "provider": "OpenStreetMap contributors (Overpass API)",
        "url": "https://www.openstreetmap.org/copyright",
        "license": "ODbL 1.0, © OpenStreetMap contributors",
        "fields": ["도로 접속(C5)", "지도 도로선"],
    },
    "dem": {
        "name": "Copernicus DEM GLO-30",
        "provider": "ESA·EU Copernicus (AWS Open Data)",
        "url": "https://registry.opendata.aws/copernicus-dem/",
        "license": "Copernicus WorldDEM-30 © DLR e.V. 2010-2014 and © Airbus Defence and Space GmbH 2014-2018, provided under COPERNICUS by the European Union and ESA",
        "fields": ["경사(흙토람 경사가 없을 때 대체값)"],
    },
    "soil": {
        "name": "흙토람 토양특성 상세정보 V3·단면정보 V2",
        "provider": "농촌진흥청 국립농업과학원(공공데이터포털)",
        "url": "https://www.data.go.kr/data/15144225/openapi.do",
        "license": "공공누리 제4유형(출처표시, 상업적 이용금지, 변경금지)",
        "fields": ["경사 등급", "배수등급", "논·밭 적성등급"],
        "key_required": "DATA_GO_KR_KEY",
    },
    "vworld": {
        "name": "연속지적도(LP_PA_CBND_BUBUN)",
        "provider": "국토교통부(브이월드 2D 데이터 API)",
        "url": "https://www.vworld.kr/dev/v4dv_2ddataguide2_s002.do?svcIde=cadastral",
        "license": "브이월드 오픈API 이용약관",
        "fields": ["지목 '도' 필지 인접(도로 접속 보강)", "공시지가"],
        "key_required": "VWORLD_KEY",
    },
}


def _log(msg: str) -> None:
    print(msg, flush=True)


def build(shp: Path, bbox, name: str, soil_budget: int = 900, use_soil: bool = True, use_vworld: bool = True) -> dict:
    t0 = time.perf_counter()
    status: dict[str, dict] = {}
    warnings: list[str] = []

    feats, info = farmmap.extract(shp, bbox)
    if not feats:
        raise SystemExit("구역 안에 팜맵 필지가 없습니다. --bbox와 SHP 시군을 확인해 주세요.")
    status["farmmap"] = {"used": True, **info}
    _log(f"[팜맵] 필지 {len(feats)}개 (비경지 {info['skipped']['비경지']}개 제외), 영상 {info['imagery']}")

    slopes, dinfo = dem.parcel_slopes(feats, bbox)
    for f, s in zip(feats, slopes):
        f["properties"]["slope_dem"] = s
        f["properties"]["slope"] = s
        f["properties"]["slope_source"] = "Copernicus DEM GLO-30"
    status["dem"] = {"used": True, **dinfo}
    _log(f"[DEM] 경사 중앙값 {np.median(slopes):.1f}%, 표고 {dinfo['elev_range_m']} m")

    roads, rinfo = osm.fetch_roads(bbox)
    acc = osm.road_access(feats, roads)
    for f, a in zip(feats, acc):
        f["properties"]["road_osm"] = a
    status["osm"] = {"used": True, **rinfo}
    _log(f"[OSM] 도로 {rinfo['n_ways']}개, 도로선 {osm.ROAD_TOL:g}m 이내 필지 {sum(acc)}개")

    vkey = http.key("VWORLD_KEY")
    if use_vworld and vkey:
        cad, cinfo = vworld.fetch_parcels(bbox, vkey)
        einfo = vworld.enrich(feats, cad)
        status["vworld"] = {"used": True, **cinfo, **einfo}
        _log(f"[연속지적도] 지적 필지 {cinfo['n_parcels']}개, 도로 필지 인접 {einfo['adjacent_to_road_parcel']}개, 공시지가 {einfo['with_land_value']}개")
    else:
        status["vworld"] = {"used": False, "reason": "VWORLD_KEY 없음" if use_vworld else "--no-vworld"}

    skey = http.key("DATA_GO_KR_KEY")
    sinfo = None
    if use_soil and skey:
        try:
            sinfo = soil.enrich(feats, skey, budget=soil_budget, progress=_log)
        except soil.SoilAuthError as exc:
            status["soil"] = {"used": False, "reason": "인증 실패"}
            warnings.append(str(exc))
            _log(f"[흙토람] {exc}")
    else:
        status["soil"] = {"used": False, "reason": "DATA_GO_KR_KEY 없음" if use_soil else "--no-soil"}
    if sinfo:
        status["soil"] = {"used": True, **sinfo}
        _log(f"[흙토람] PNU {sinfo['pnus']}개 중 {sinfo['queried']}개 조회(이번 신규 호출 {sinfo['calls']}), 배수 채움 {sinfo['filled_drainage']}")
        if sinfo["stopped"]:
            warnings.append(f"흙토람 조회가 중간에 멈췄습니다({'; '.join(sinfo['stopped'].values())}). 내일 다시 돌리면 캐시 다음부터 이어 받습니다.")
        if sinfo["queried"] < sinfo["pnus"]:
            warnings.append(
                f"흙토람은 PNU {sinfo['pnus']}개 중 {sinfo['queried']}개만 직접 조회했습니다(개발계정 하루 1,000건). "
                "나머지 필지는 300m 안의 가장 가까운 조회 필지(논은 논, 밭은 밭 우선) 값을 썼습니다."
            )
    n_dem = sum(f["properties"].get("slope_source") == "Copernicus DEM GLO-30" for f in feats)
    n_nodrain = sum("drainage" not in f["properties"] for f in feats)
    if n_nodrain == len(feats):
        warnings.append("배수등급·토양 적성등급이 없습니다(흙토람 키 필요). 모든 필지를 배수 '보통', 토양 2등급으로 보고 계산하므로 배수 동질성(C8)이 걸러내지 못합니다.")
    elif n_nodrain:
        warnings.append(f"필지 {n_nodrain}개는 배수등급을 못 구해 '보통'으로 계산합니다.")
    if n_dem == len(feats):
        warnings.append("경사는 30m DEM에서 구한 대체값입니다. 흙토람 경사 등급보다 거칩니다.")
    elif n_dem:
        warnings.append(f"필지 {n_dem}개는 흙토람 경사가 없어 30m DEM 대체값을 씁니다.")

    n_none = 0
    for f in feats:
        p = f["properties"]
        src = [s for s, ok in (("OSM 도로", p.get("road_osm")), ("경지정리 구획", p.get("consolidated")), ("지적 도로 필지", p.get("road_parcel_adjacent"))) if ok]
        p["road_access"] = bool(src)
        p["road_source"] = ", ".join(src)
        n_none += not src
    _log(f"[도로 접속] 접속 {len(feats) - n_none}개, 미접속 {n_none}개")

    # 인접 그래프는 미리 계산해 넣는다(불러올 때 다시 계산하지 않도록)
    t1 = time.perf_counter()
    proj = LocalProjection(bbox[0], bbox[1])
    edges = adjacency([proj.to_xy(f["geometry"]["coordinates"][0]) for f in feats])
    _log(f"[인접] 간선 {len(edges)}개 ({time.perf_counter() - t1:.1f}초)")

    area_ha = sum(f["properties"]["area_m2"] for f in feats) / 1e4
    uses: dict[str, int] = {}
    for f in feats:
        uses[f["properties"]["land_use"]] = uses.get(f["properties"]["land_use"], 0) + 1
    road_lines = [r["coords"] for r in roads]
    meta = {
        "name": name,
        "synthetic": False,
        "description": "공개 실측 자료로 만든 지도입니다. 소유자 정보는 공개되지 않아(농지대장 비공개) 참여 농가 수를 필지 수로 추정합니다.",
        "built": date.today().isoformat(),
        "bbox": list(bbox),
        "origin": [bbox[0], bbox[1]],
        "n_parcels": len(feats),
        "total_area_ha": round(area_ha, 1),
        "land_use_counts": uses,
        "consolidated_share": round(sum(f["properties"]["consolidated"] for f in feats) / len(feats), 3),
        "truth_complexes": 0,
        "sources": [dict(SOURCES[k], status=status.get(k, {})) for k in ("farmmap", "osm", "dem", "soil", "vworld")],
        "warnings": warnings,
        "roads": road_lines,
        "seconds": round(time.perf_counter() - t0, 1),
    }
    return {
        "meta": meta,
        "parcels": {"type": "FeatureCollection", "features": feats},
        "adjacency": [[i, j, length, touch] for i, j, length, touch in edges],
    }


def _round_coords(scn: dict, nd: int = 6) -> None:
    for f in scn["parcels"]["features"]:
        f["geometry"]["coordinates"] = [[[round(x, nd), round(y, nd)] for x, y in ring] for ring in f["geometry"]["coordinates"]]
    scn["meta"]["roads"] = [[[round(x, nd), round(y, nd)] for x, y in line] for line in scn["meta"]["roads"]]


def main() -> None:
    ap = argparse.ArgumentParser(description="공개 실측 자료로 후보단지 시뮬레이터용 읍면 지도를 만든다")
    ap.add_argument("--farmmap-shp", type=Path, required=True, help="팜맵 시군 SHP(.shp) 경로")
    ap.add_argument("--bbox", required=True, help="경도최소,위도최소,경도최대,위도최대 (EPSG:4326)")
    ap.add_argument("--name", required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--soil-budget", type=int, default=900, help="흙토람 API별 이번 실행 최대 신규 호출 수(개발계정 하루 1,000건)")
    ap.add_argument("--no-soil", action="store_true")
    ap.add_argument("--no-vworld", action="store_true")
    a = ap.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    bbox = tuple(float(v) for v in a.bbox.split(","))
    if len(bbox) != 4 or bbox[0] >= bbox[2] or bbox[1] >= bbox[3]:
        raise SystemExit("--bbox 형식: 경도최소,위도최소,경도최대,위도최대")
    scn = build(a.farmmap_shp, bbox, a.name, a.soil_budget, not a.no_soil, not a.no_vworld)
    _round_coords(scn)
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(json.dumps(scn, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    _log(f"저장: {a.out} ({a.out.stat().st_size / 1e6:.1f} MB, {scn['meta']['seconds']}초)")
    for w in scn["meta"]["warnings"]:
        _log(f"  확인 필요: {w}")


if __name__ == "__main__":
    main()
