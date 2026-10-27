"""API 요청 스키마."""
from __future__ import annotations

from pydantic import BaseModel, Field, field_validator

from .params import CROP_ORDER, DEFAULT_CROP_BOUNDS


class DemoRequest(BaseModel):
    seed: int = Field(7, ge=0, le=10_000_000, description="난수 시드")
    n_farmers: int = Field(30, ge=6, le=80, description="들녘 농가 수")
    participation: float = Field(0.6, ge=0.1, le=1.0, description="공동영농 참여율(농가 수 기준)")
    fragmentation: float = Field(0.75, ge=0.0, le=1.0, description="소유지 분산 정도(0 집중 ~ 1 무작위)")
    col_pairs: int = Field(4, ge=2, le=8, description="논 필지 열 쌍 수")
    rows: int = Field(16, ge=8, le=32, description="논 필지 행 수")


class OptimizeSettings(BaseModel):
    area_tol: float = Field(0.10, ge=0.0, le=0.5, description="경작자별 면적 변화 허용(비율)")
    value_tol: float = Field(0.10, ge=0.0, le=0.5, description="경작자별 평가가치 변화 허용(비율)")
    iterations: int = Field(40000, ge=500, le=400_000, description="다자간 교환 탐색 반복 수")
    seed: int = Field(0, ge=0)
    crop_bounds: dict[str, list[float]] = Field(
        default_factory=lambda: {k: list(v) for k, v in DEFAULT_CROP_BOUNDS.items()},
        description="법인 경작 면적 대비 작목별 [하한, 상한] 비율",
    )
    contiguity_weight: float = Field(1000.0, ge=0.0, le=100_000.0, description="같은 작목 인접 경계 1m당 보상(원)")
    seepage_penalty: float = Field(1500.0, ge=0.0, le=100_000.0, description="담수·밭 작목 맞닿은 경계 1m당 벌점(원)")
    time_limit_s: float = Field(10.0, gt=0.0, le=120.0, description="MILP 풀이 시간 제한(초)")
    shapley_samples: int = Field(150, ge=10, le=2000, description="샤플리 값 근사 표본 수")
    labor_share: float = Field(0.2, ge=0.0, le=1.0, description="토지·노동 혼합 배분에서 노동 비중")
    w_block: float = Field(1.0, ge=0.0, le=100.0)
    w_disp: float = Field(0.5, ge=0.0, le=100.0)
    w_base: float = Field(0.2, ge=0.0, le=100.0)
    coop_weight: float = Field(2.0, ge=0.0, le=100.0)
    w_change: float = Field(0.15, ge=0.0, le=10.0, description="경작자가 바뀌는 필지 1개당 비용(교환 최소화)")
    merge_bunds: bool = Field(True, description="법인 경작지의 같은 작목 맞닿은 필지를 한 포장으로 정비한다고 가정")

    @field_validator("crop_bounds")
    @classmethod
    def _check_bounds(cls, v: dict[str, list[float]]):
        out = {}
        for crop, pair in v.items():
            if crop not in CROP_ORDER:
                raise ValueError(f"알 수 없는 작목입니다: {crop}")
            if len(pair) != 2:
                raise ValueError(f"{crop}: [하한, 상한] 두 값이 필요합니다.")
            lo, hi = float(pair[0]), float(pair[1])
            if not (0.0 <= lo <= hi <= 1.0):
                raise ValueError(f"{crop}: 0 ≤ 하한 ≤ 상한 ≤ 1 이어야 합니다.")
            out[crop] = [lo, hi]
        return out


class OptimizeRequest(BaseModel):
    scenario: dict
    settings: OptimizeSettings = Field(default_factory=OptimizeSettings)
