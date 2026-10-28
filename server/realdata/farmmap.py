"""팜맵(농경지 전자지도) SHP에서 관심 구역 필지를 꺼낸다.

출처: 농림수산식품교육문화정보원_팜맵공간정보_<시도>(공공데이터포털 파일데이터, 연 1회 갱신, SHP·EPSG:5179·EUC-KR).
  https://www.data.go.kr/data/15104488/fileData.do (경상북도. 다른 시도는 15104481~15104491)
압축을 풀면 시군별 SHP가 나온다. 속성 정의(2025년도 팜맵 속성정의서):
  CLSF_NM  분류명 논/밭/과수/시설/비경지(항공·위성영상 판독)
  LDCG_CD  대표지목(중첩 면적이 가장 큰 지적 필지의 지목)
  PNU      대표 PNU(같은 기준)      FL_ARMT_YN  경지정리 여부(논·밭·인삼만 Y/N)
  AREA     구획 면적(㎡)            FLIGHT_YMD  판독 영상 촬영일
"""
from __future__ import annotations

from pathlib import Path

import numpy as np

from ..geo import UTMK
from .shapefile import PolygonShapefile, signed_area

# 팜맵 분류 → 시뮬레이터 (land_type, land_use). 비경지는 농경지가 아니라 지도에서 뺀다.
CLASS_MAP = {"논": ("답", "논"), "밭": ("전", "밭"), "과수": ("과", "과수"), "시설": ("전", "시설")}


def extract(shp_path: str | Path, bbox_lonlat: tuple[float, float, float, float]) -> tuple[list[dict], dict]:
    """bbox(경도·위도 최소, 최대) 안에 중심이 있는 팜맵 필지를 GeoJSON Feature 목록으로 돌려준다."""
    utmk = UTMK()
    shp = PolygonShapefile(shp_path)
    lo = utmk.to_xy([bbox_lonlat[:2]])[0]
    hi = utmk.to_xy([bbox_lonlat[2:]])[0]
    # 경위도 사각형은 UTM-K에서 살짝 기울어지므로 넉넉히 거른 뒤 중심점을 경위도로 다시 판정한다
    pad = 50.0
    bb = shp.bboxes()
    cand = np.flatnonzero(
        (bb[:, 2] >= min(lo[0], hi[0]) - pad) & (bb[:, 0] <= max(lo[0], hi[0]) + pad)
        & (bb[:, 3] >= min(lo[1], hi[1]) - pad) & (bb[:, 1] <= max(lo[1], hi[1]) + pad)
    )
    recs = shp.records(cand)
    features, skipped = [], {"비경지": 0, "기타": 0}
    flight = []
    for k, rec in zip(cand, recs):
        cls = rec.get("CLSF_NM") or ""
        if cls not in CLASS_MAP:
            skipped["비경지" if cls == "비경지" else "기타"] += 1
            continue
        rings = [r for r in shp.rings(k) if len(r) >= 4]
        outers = [r for r in rings if signed_area(r) < 0] or rings
        if not outers:
            continue
        outer = max(outers, key=lambda r: abs(signed_area(r)))
        c = utmk.to_lonlat([outer.mean(axis=0)])[0]
        if not (bbox_lonlat[0] <= c[0] < bbox_lonlat[2] and bbox_lonlat[1] <= c[1] < bbox_lonlat[3]):
            continue
        ll = np.round(utmk.to_lonlat(outer[::-1]), 7).tolist()  # GeoJSON 외곽 링은 반시계 방향
        if ll[0] != ll[-1]:
            ll.append(ll[0])
        land_type, land_use = CLASS_MAP[cls]
        props = {
            "id": rec.get("ID") or f"F{k}",
            "pnu": rec.get("PNU") or "",
            "land_type": land_type,
            "land_use": land_use,
            "jimok": rec.get("LDCG_CD") or "",
            "consolidated": rec.get("FL_ARMT_YN") == "Y",
            "area_m2": round(float(rec.get("AREA") or 0.0), 1),
            "address": rec.get("STDG_ADDR") or "",
        }
        if rec.get("FLIGHT_YMD"):
            flight.append(rec["FLIGHT_YMD"])
        features.append({"type": "Feature", "id": props["id"], "geometry": {"type": "Polygon", "coordinates": [ll]}, "properties": props})
    info = {
        "file": Path(shp_path).name,
        "n_parcels": len(features),
        "skipped": skipped,
        "imagery": [min(flight), max(flight)] if flight else None,
    }
    return features, info
