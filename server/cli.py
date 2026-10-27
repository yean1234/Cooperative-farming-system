"""명령줄에서 데모 시나리오를 돌려 비교표를 출력한다.

    python -m server.cli --seed 7
    python -m server.cli --scenario my_field.json --out out/result.json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .demo import generate_demo
from .pipeline import run_pipeline


def _fmt_won(v: float) -> str:
    return f"{v / 1e6:,.1f}백만"


def main() -> None:
    ap = argparse.ArgumentParser(description="공동영농 필지 최적화 시나리오 비교")
    ap.add_argument("--seed", type=int, default=7, help="가상 들녘 시드")
    ap.add_argument("--participation", type=float, default=0.6)
    ap.add_argument("--fragmentation", type=float, default=0.75)
    ap.add_argument("--scenario", type=Path, help="시나리오 JSON 경로(없으면 가상 들녘 생성)")
    ap.add_argument("--no-merge", action="store_true", help="논두렁 정비(대구획화) 가정을 끈다")
    ap.add_argument("--out", type=Path, help="결과 JSON 저장 경로")
    args = ap.parse_args()

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    if args.scenario:
        scn = json.loads(args.scenario.read_text(encoding="utf-8"))
    else:
        scn = generate_demo(seed=args.seed, participation=args.participation, fragmentation=args.fragmentation)
    res = run_pipeline(scn, {"merge_bunds": not args.no_merge})

    print(f"들녘: {scn.get('meta', {}).get('name', '-')}, 필지 {len(res['parcel_ids'])}개, 조합원 {len(res['members'])}명")
    header = f"{'시나리오':<24}{'블록':>6}{'기계시간':>10}{'이동km':>9}{'순이익':>14}{'법인 순이익':>14}"
    print(header)
    print("-" * len(header))
    for s in res["scenarios"]:
        t, g = s["totals"], s["group"]
        print(
            f"{s['label']:<24}{t['blocks']:>6.0f}{t['machine_hours']:>10.0f}{t['travel_km']:>9.0f}"
            f"{_fmt_won(t['net_income']):>14}{_fmt_won(g['net_income']):>14}"
        )
    sh = res.get("sharing")
    if sh:
        print()
        print(f"조합원 협동잉여 {_fmt_won(sh['surplus'])}, 평당 배분 {sh['per_pyeong']:,.0f}원")
        for r in sh["rules"].values():
            print(f"  {r['label']:<20} 개별영농보다 나은 조합원 {r['better_off']}명, 나빠지는 조합원 {r['worse_off']}명")
    print(f"\n계산 시간 {res['timings']['total']}초")
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"결과 저장: {args.out}")


if __name__ == "__main__":
    main()
