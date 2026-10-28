"""흙토람 토양특성(농촌진흥청 국립농업과학원, 1:5,000 토양도의 PNU 연계자료)으로 경사·배수·적성등급을 채운다.

공공데이터포털 인증키(DATA_GO_KR_KEY)가 필요하다. 두 API를 각각 활용신청해야 한다.
  토양특성 상세정보 V3  https://www.data.go.kr/data/15144225/openapi.do  배수등급, 논·밭 적성등급
  토양특성 단면정보 V2  https://www.data.go.kr/data/15144105/openapi.do  경사도
코드표는 각 API 기술명세서(OPEN API기술명세서_*.hwp) 3.1절을 따랐다.

개발계정은 API마다 하루 1,000건이다. 필지를 공간적으로 고르게 골라 예산만큼 조회하고,
못 부른 필지는 가장 가까운 조회 필지 값으로 채운다(토양도 도형 하나가 보통 여러 필지에 걸친다).
응답은 data/cache/soil*에 남으므로 다음 날 다시 돌리면 빈칸이 채워진다.
"""
from __future__ import annotations

import time
import urllib.error
import urllib.parse
import xml.etree.ElementTree as ET

import numpy as np

from . import http

URL_CHARAC = "https://apis.data.go.kr/1390802/SoilEnviron/SoilCharac/V3/getSoilCharacter"
URL_SCTNN = "https://apis.data.go.kr/1390802/SoilEnviron/SoilCharacSctnn/V2/getSoilCharacterSctnn"

# 경사 코드(01~06) → 등급 구간과 대표값(구간 중앙, %)
SLOPE_CLASS = {1: ("0-2%", 1.0), 2: ("2-7%", 4.5), 3: ("7-15%", 11.0), 4: ("15-30%", 22.5), 5: ("30-60%", 45.0), 6: ("60-100%", 80.0)}
# 배수등급 코드(01 매우양호 ~ 06 매우불량) → 시뮬레이터 3단계(1 양호, 2 보통, 3 불량)
DRAIN_NAME = {1: "매우양호", 2: "양호", 3: "약간양호", 4: "약간불량", 5: "불량", 6: "매우불량"}
DRAIN_TO_SIM = {1: 1, 2: 1, 3: 2, 4: 3, 5: 3, 6: 3}
# 적성등급 1~5급지 → 시뮬레이터 토양등급 1~4(5급지는 4로 묶는다)
GRADE_TO_SIM = {1: 1, 2: 2, 3: 3, 4: 4, 5: 4}


class SoilApiError(RuntimeError):
    pass


class QuotaExceeded(SoilApiError):
    pass


class SoilAuthError(SoilApiError):
    pass


# 잠시 뒤 다시 부르면 되는 게이트웨이 오류: 내부 오류, HTTP 오류, 시간 초과, 초당 호출 초과
TRANSIENT = {"01", "04", "05", "23"}


def _code(item, tag):
    el = item.find(tag)
    if el is None or not (el.text or "").strip():
        return None
    try:
        v = int(el.text.strip())
    except ValueError:
        return None
    return None if v == 99 else v


def _call(url: str, pnu: str, service_key: str, ns: str, retries: int = 3):
    params = {"PNU_CD": pnu}
    for attempt in range(retries + 1):
        body = http.get(url, params, secret={"serviceKey": service_key}, ns=ns)
        try:
            root = ET.fromstring(body)
        except ET.ParseError:
            root = None
        code = msg = ""
        if root is not None:
            code = (root.findtext(".//Result_Code") or root.findtext(".//returnReasonCode") or "").strip()
            msg = (root.findtext(".//Result_Msg") or root.findtext(".//returnAuthMsg") or root.findtext(".//errMsg") or "").strip()
        if code in ("200", "301"):  # 정상, 자료 없음만 캐시에 남긴다
            return root.find(".//item")
        http.uncache(url, params, ns)
        text = msg or body[:200].decode("utf-8", "replace")
        if code == "22" or "LIMITED_NUMBER_OF_SERVICE_REQUESTS_EXCEEDS" in text:
            raise QuotaExceeded(f"일일 호출 한도 초과: {text}")
        if code in ("101", "20", "30", "31") or "KEY" in text.upper() or "SERVICE_ACCESS" in text.upper():
            raise SoilAuthError(f"인증 실패({code} {text}). 공공데이터포털에서 이 API의 활용신청 승인과 인증키를 확인해 주세요: {url}")
        if (code in TRANSIENT or "PER_SECOND" in text) and attempt < retries:
            time.sleep(1.5 * (attempt + 1))
            continue
        raise SoilApiError(f"흙토람 API 오류({code}): {text}")


def _order(features) -> list[int]:
    """조회 순서: 480m → 240m → 120m → 60m 격자마다 한 필지씩, 이미 덮인 칸은 건너뛴다.

    하루 한도에서 어디서 끊기든 들녘 전체가 고르게 덮이도록 굵은 격자부터 채운다.
    """
    c = np.array([np.asarray(f["geometry"]["coordinates"][0], float)[:-1].mean(axis=0) for f in features])
    rel = c - c.min(axis=0)
    deg60 = np.array([0.00066, 0.00054])  # 위도 36°에서 약 60m
    order, taken = [], np.zeros(len(features), dtype=bool)
    for mult in (8, 4, 2, 1):
        cell = np.floor(rel / (deg60 * mult)).astype(int)
        covered = {tuple(cell[i]) for i in np.flatnonzero(taken)}
        for i in range(len(features)):
            k = tuple(cell[i])
            if not taken[i] and k not in covered:
                order.append(i)
                taken[i] = True
                covered.add(k)
    return order + [i for i in range(len(features)) if not taken[i]]


def enrich(features, service_key: str, budget: int = 900, progress=None) -> dict:
    """features의 properties에 slope·drainage·soil_grade와 출처를 채운다. 조회 통계를 돌려준다.

    budget은 API별 이번 실행의 신규 호출 상한이다(캐시 응답은 세지 않는다).
    """
    if "%" in service_key:  # 인코딩된 키를 붙여 넣은 경우
        service_key = urllib.parse.unquote(service_key)
    by_pnu: dict[str, dict] = {}
    calls = {"charac": 0, "sctnn": 0}
    errors = {"charac": 0, "sctnn": 0}
    pnus_ordered, seen = [], set()
    for i in _order(features):
        p = features[i]["properties"].get("pnu", "")
        if len(p) == 19 and not p.endswith("00000000") and p not in seen:
            pnus_ordered.append(p)
            seen.add(p)
    stopped: dict[str, str] = {}
    for n_done, pnu in enumerate(pnus_ordered, start=1):
        rec: dict = {}
        for name, url, ns in (("charac", URL_CHARAC, "soil_charac"), ("sctnn", URL_SCTNN, "soil_sctnn")):
            cached = http.is_cached(url, {"PNU_CD": pnu}, ns)
            if not cached and (calls[name] >= budget or name in stopped):
                continue
            try:
                item = _call(url, pnu, service_key, ns)
            except SoilAuthError:
                raise
            except QuotaExceeded as exc:
                stopped[name] = str(exc)
                continue
            except (SoilApiError, urllib.error.URLError, TimeoutError) as exc:
                errors[name] += 1
                if not cached:
                    calls[name] += 1  # 실패한 호출도 한도에서 빠진다
                if errors[name] >= 20:
                    stopped[name] = f"오류가 20번 넘게 나서 멈췄습니다. 마지막 오류: {exc}"
                continue
            if not cached:
                calls[name] += 1
            if item is None:
                continue
            if name == "charac":
                rec["drain_cd"] = _code(item, "Soildra_Cd")
                rec["paddy_grade"] = _code(item, "Rfld_Grd_Cd")
                rec["upland_grade"] = _code(item, "Pfld_Grd_Cd")
            else:
                rec["slope_cd"] = _code(item, "Soilslope_Cd")
        if rec:
            by_pnu[pnu] = rec
        if progress and (n_done % 100 == 0 or n_done == len(pnus_ordered)):
            progress(f"  흙토람 {n_done}/{len(pnus_ordered)} PNU, 신규 호출 {calls}, 오류 {errors}")

    # 필지에 붙이기: 직접 조회 → 가장 가까운 조회 필지(같은 지목 우선)
    cent = np.array([np.asarray(f["geometry"]["coordinates"][0], float)[:-1].mean(axis=0) for f in features])
    have = {key: [i for i, f in enumerate(features) if (by_pnu.get(f["properties"].get("pnu", "")) or {}).get(key)] for key in ("drain_cd", "slope_cd", "paddy_grade", "upland_grade")}
    is_paddy = np.array([f["properties"].get("land_type") == "답" for f in features])
    filled = {"direct": 0, "nearest": 0, "none": 0}
    for i, f in enumerate(features):
        p = f["properties"]
        direct = by_pnu.get(p.get("pnu", ""), {})

        def pick(key):
            if direct.get(key):
                return direct[key], "흙토람"
            idx = np.array(have[key], dtype=int)
            if not len(idx):
                return None, None
            d = np.hypot(*(cent[idx] - cent[i]).T * np.array([[89_000.0], [110_574.0]]))
            # 논은 논끼리, 밭은 밭끼리 먼저 찾는다(같은 지형 단위일 가능성이 크다). 300m 넘게 떨어진 값은 빌리지 않는다
            same = is_paddy[idx] == is_paddy[i]
            for mask in (same & (d <= 300), d <= 300):
                if mask.any():
                    j = idx[mask][int(d[mask].argmin())]
                    return by_pnu[features[j]["properties"]["pnu"]][key], "흙토람(인접 필지)"
            return None, None

        sc, s_src = pick("slope_cd")
        if sc:
            p["slope_class"], p["slope"] = SLOPE_CLASS[sc]
            p["slope_source"] = s_src
        dc, d_src = pick("drain_cd")
        if dc:
            p["drainage"] = DRAIN_TO_SIM[dc]
            p["drainage_class"] = DRAIN_NAME[dc]
            p["drainage_source"] = d_src
        gkey = "paddy_grade" if p.get("land_type") == "답" else "upland_grade"
        g, g_src = pick(gkey)
        if g:
            p["soil_grade"] = GRADE_TO_SIM[g]
            p["suitability_grade"] = g
            p["soil_source"] = g_src
        filled["direct" if d_src == "흙토람" else "nearest" if d_src else "none"] += 1
    return {"pnus": len(pnus_ordered), "queried": len(by_pnu), "calls": calls, "errors": errors, "filled_drainage": filled, "stopped": stopped}
