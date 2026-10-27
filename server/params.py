"""모델 파라미터 기본값.

모든 금액은 원(KRW)이다. 작목 소득은 10a(1,000㎡) 기준이다.
아래 숫자는 데모를 돌리기 위한 '초안 가정값'이다. 실제 적용 전에 출처 자료로 교체해야 한다.

- 작목 소득·경영비: 농촌진흥청 「농산물소득자료집」(농사로 > 경영자료실)
- 전략작물직불금(2026): 농림축산식품부 공고 제2026-127호
  하계 두류·가루쌀 200만원/ha, 하계 조사료 550만원/ha, 동계 밀 100만원/ha, 이모작 +100만원/ha
- 벼 단작 농업소득 약 670만원/ha: 경북 문경 영순 공동영농 사례(경상북도 발표 인용 보도)
- 농작업 능률: 농촌진흥청 농작업 표준능률·농기계 이용 실태 자료
"""
from __future__ import annotations

from copy import deepcopy

COOP_ID = "COOP"
COOP_NAME = "공동영농법인"

# 최적화 대상 작목(범례 순서). '시설채소'는 고정 작목이라 배치 대상이 아니다.
CROP_ORDER = ["벼", "가루쌀", "콩", "조사료"]
ALL_CROPS = CROP_ORDER + ["시설채소"]

# 토양 적성등급(1 최상 ~ 4) 수량 계수
SOIL_MULT = {1: 1.00, 2: 0.95, 3: 0.88, 4: 0.80}

MACHINES = {
    "트랙터": {"cost_per_h": 60_000},
    "이앙기": {"cost_per_h": 55_000},
    "콤바인": {"cost_per_h": 110_000},
    "드론": {"cost_per_h": 70_000},
}

_RICE_OPS = [
    {"name": "경운", "machine": "트랙터", "capacity_ha_h": 0.45, "passes": 1},
    {"name": "써레질", "machine": "트랙터", "capacity_ha_h": 0.50, "passes": 1},
    {"name": "이앙", "machine": "이앙기", "capacity_ha_h": 0.35, "passes": 1},
    {"name": "방제", "machine": "드론", "capacity_ha_h": 4.0, "passes": 3},
    {"name": "수확", "machine": "콤바인", "capacity_ha_h": 0.30, "passes": 1},
]

# water: wet(담수 작물) / dry(밭 상태 작물) / none(시설 등 주변 영향 없음)
# drain_mult: 배수등급(1 양호, 2 보통, 3 불량)별 수량 계수. 키가 없으면 재배 불가.
# gross_margin: 기계작업비를 뺀 나머지 경영비만 차감한 소득(원/10a, 가정값)
# direct_payment: 지목별 전략작물직불금(원/10a). 논(답)에만 지급.
CROPS = {
    "벼": {
        "water": "wet",
        "land_types": ["답"],
        "needs_irrigation": True,
        "drain_mult": {1: 1.0, 2: 1.0, 3: 1.0},
        "gross_margin": 780_000,
        "direct_payment": {"답": 0},
        "patrol_visits": 40,
        "ops": _RICE_OPS,
    },
    "가루쌀": {
        "water": "wet",
        "land_types": ["답"],
        "needs_irrigation": True,
        "drain_mult": {1: 1.0, 2: 1.0, 3: 1.0},
        "gross_margin": 600_000,
        "direct_payment": {"답": 200_000},
        "patrol_visits": 40,
        "ops": _RICE_OPS,
    },
    "콩": {
        "water": "dry",
        "land_types": ["답", "전"],
        "needs_irrigation": False,
        "drain_mult": {1: 1.0, 2: 0.85},
        "gross_margin": 520_000,
        "direct_payment": {"답": 200_000},
        "patrol_visits": 8,
        "ops": [
            {"name": "경운", "machine": "트랙터", "capacity_ha_h": 0.45, "passes": 1},
            {"name": "두둑·파종", "machine": "트랙터", "capacity_ha_h": 0.35, "passes": 1},
            {"name": "방제", "machine": "드론", "capacity_ha_h": 4.0, "passes": 2},
            {"name": "수확", "machine": "콤바인", "capacity_ha_h": 0.25, "passes": 1},
        ],
    },
    "조사료": {
        "water": "dry",
        "land_types": ["답", "전"],
        "needs_irrigation": False,
        "drain_mult": {1: 1.0, 2: 0.95, 3: 0.80},
        "gross_margin": 250_000,
        "direct_payment": {"답": 550_000},
        "patrol_visits": 6,
        "ops": [
            {"name": "경운", "machine": "트랙터", "capacity_ha_h": 0.45, "passes": 1},
            {"name": "파종", "machine": "트랙터", "capacity_ha_h": 0.60, "passes": 1},
            {"name": "수확·결속", "machine": "트랙터", "capacity_ha_h": 0.30, "passes": 2},
        ],
    },
    "시설채소": {
        "water": "none",
        "locked": True,
        "land_types": ["전", "답"],
        "needs_irrigation": False,
        "drain_mult": {1: 1.0, 2: 1.0, 3: 1.0},
        "gross_margin": 2_500_000,
        "direct_payment": {},
        "patrol_visits": 0,
        "ops": [],
    },
}

OPERATIONS = {
    "eta_max": 0.85,              # 큰 필지의 포장효율 상한
    "eta_half_ha": 0.06,          # 포장효율이 상한의 절반이 되는 필지 면적(ha)
    "setup_min_coop": 15.0,       # 작업 블록 진입·세팅 시간(법인, 분)
    "setup_min_individual": 20.0, # 같은 항목(개별 농가, 분)
    "transition_min": 3.0,        # 같은 블록 안 필지 간 이동(분)
    "road_speed_kmh": 12.0,       # 농기계 도로 주행 속도
    "detour": 1.3,                # 직선거리 대비 도로 우회 계수
    "patrol_speed_kmh": 25.0,     # 물꼬·생육 순회 차량 속도
    "patrol_stop_min": 5.0,       # 순회 시 블록당 확인 시간(분)
    "labor_cost_per_h": 15_000,   # 순회 인건비
    "coop_overhead_per_ha": 300_000,  # 법인 운영비(사무장·회계·보험 등)
    # 경작권 교환으로 남의 땅을 경작하면 평가가치(공시지가×면적×토양계수)의 이 비율만큼 연 임차료를 정산한다.
    # 공시지가 3.3만원/㎡ 논 10a면 약 50만원/년 수준(가정값).
    "rent_rate": 0.015,
}

# 법인 경작 면적(고정 작목 제외) 대비 작목별 면적 비율 [하한, 상한]
DEFAULT_CROP_BOUNDS = {
    "벼": [0.0, 0.70],      # 쌀 적정생산 정책 반영
    "가루쌀": [0.0, 0.20],  # 가공업체 계약 물량
    "콩": [0.10, 0.40],     # 전략작물 최소 비율
    "조사료": [0.0, 0.15],  # 인근 축산농가 수요
}


def default_params() -> dict:
    return deepcopy(
        {
            "crops": CROPS,
            "machines": MACHINES,
            "ops": OPERATIONS,
            "soil_mult": SOIL_MULT,
        }
    )


def crop_feasible(crop: str, parcel, params: dict) -> bool:
    """작목을 해당 필지에 새로 배치할 수 있는지 판단한다."""
    cdef = params["crops"].get(crop)
    if cdef is None or cdef.get("locked"):
        return False
    if parcel.land_type not in cdef["land_types"]:
        return False
    if cdef.get("needs_irrigation") and not parcel.irrigated:
        return False
    return parcel.drainage in cdef["drain_mult"]


def revenue_parts(crop: str, parcel, params: dict) -> tuple[float, float]:
    """필지 하나의 (작물 소득, 직불금)을 원 단위로 돌려준다. 기계작업비는 별도 계산."""
    cdef = params["crops"][crop]
    soil = params["soil_mult"].get(parcel.soil_grade, 0.85)
    drain = cdef["drain_mult"].get(parcel.drainage, 0.55)
    per10a = parcel.area / 1000.0
    gm = cdef["gross_margin"] * soil * drain * per10a
    dp = cdef["direct_payment"].get(parcel.land_type, 0) * per10a
    return gm, dp


def crop_water(crop: str, params: dict) -> str:
    return params["crops"][crop]["water"]
