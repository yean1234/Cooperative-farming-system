"""명령줄에서 후보단지 선정·검증·정확해 비교를 돌린다.

    python -m server.sites_cli                       # 가상 읍면, 기본 조건
    python -m server.sites_cli --geojson my.geojson  # 팜맵 기반 GeoJSON
    python -m server.sites_cli --validate --exact    # 재현 검증 + 소규모 MILP 비교
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import replace
from pathlib import Path

import numpy as np

from .region_demo import generate_region
from .selection import Region, SiteSettings, solve
from .selection_eval import exact_window, validate, window_around


def main() -> None:
    ap = argparse.ArgumentParser(description="공동영농 후보단지 선정")
    ap.add_argument("--seed", type=int, default=11)
    ap.add_argument("--geojson", type=Path)
    ap.add_argument("--a-min", type=float, default=20)
    ap.add_argument("--a-max", type=float, default=60)
    ap.add_argument("--n-min", type=int, default=5)
    ap.add_argument("--slope-max", type=float, default=7)
    ap.add_argument("--proxy-owners", action="store_true", help="참여 농가 수를 필지 수로 추정")
    ap.add_argument("--validate", action="store_true")
    ap.add_argument("--exact", action="store_true", help="소규모 구역 MILP 정확해와 비교")
    ap.add_argument("--out", type=Path)
    a = ap.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    if a.geojson:
        data = json.loads(a.geojson.read_text(encoding="utf-8"))
        scn = {"parcels": data} if data.get("type") == "FeatureCollection" else data
    else:
        scn = generate_region(seed=a.seed)
    R = Region(scn)
    s = SiteSettings(a_min_ha=a.a_min, a_max_ha=a.a_max, n_min=a.n_min, slope_max=a.slope_max, owner_mode="proxy" if a.proxy_owners else "data")
    res = solve(R, s)
    sm = res["summary"]
    print(f"필지 {R.n}개, 적격 {sm['eligible_area_ha']}ha → 후보 단지 {sm['n_complexes']}곳, {sm['complex_area_ha']}ha ({sm['seconds']}초)")
    print(f"{'순위':>4}{'면적':>8}{'필지':>6}{'농가':>6}{'경사%':>7}{'배수':>10}{'형상':>7}{'소득 보수~낙관(백만원)':>24}")
    for c in res["complexes"]:
        lo, mid, hi = c["income_musd"]
        drain = f"{c['drain_class']} {c['homog']:.0%}"
        print(f"{c['id']:>4}{c['area_ha']:>8.1f}{c['n_parcels']:>6}{c['n_owners']:>6}{c['mean_slope']:>7.1f}{drain:>10}{c['compactness']:>7.2f}   {lo:.1f} / {mid:.1f} / {hi:.1f}")
    if a.validate:
        v = validate(R, s)
        print("\n재현 검증" + (" (가상 정답)" if v["truth_synthetic"] else ""))
        for row in v["table"]:
            if row.get("available"):
                print(f"  {row['method']:<14} F1 {row['f1']:.2f}  IoU {row['iou_mean']:.2f}  제약 충족 {row['feasible']}/{row['complexes']}")
        print(f"  민감도 Jaccard 평균 {v['sensitivity']['mean']:.2f}, 최소 {v['sensitivity']['min']:.2f}")
    if a.exact:
        ws = replace(s, a_min_ha=8, a_max_ha=20)
        center = int(np.argmax(R.truth == 1)) if R.truth.max() else int(np.argmax(R.area))
        for n in (50, 70):
            out = exact_window(R, ws, window_around(R, ws, center, n), K=2, time_limit=60)
            print(f"\n정확해 비교(필지 {n}개, A_min 8ha): MILP {out['milp_value']} (상계 {out['milp_bound']}), 휴리스틱 {out['heuristic_value']}, 차이 {out['gap_vs_milp']}, {out['seconds']}초")
    if a.out:
        a.out.parent.mkdir(parents=True, exist_ok=True)
        a.out.write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
