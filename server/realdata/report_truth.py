"""정답 라벨(truth_complex)이 붙은 실측 지도에서 후보단지 엔진을 검증하고 결과를 표와 그림으로 낸다.

    python -m server.realdata.report_truth data/regions/yeongsun.json --png out/yeongsun_eval.png

내는 것
  1. 재현 검증: 필지 단위 Precision·Recall·F1, 면적 IoU, 제약 충족률 — 제안 방법 vs 공간 KMeans vs 면적순 greedy
  2. 순위 적중: 정답 덩어리와 가장 많이 겹치는 후보 단지가 점수 순위 몇 위인지, 상위 k개가 정답 면적을 얼마나 덮는지
  3. 민감도: 조건을 흔들었을 때 선정 필지의 Jaccard 안정도
  4. 그림: 정답 덩어리(빨간 윤곽) 위에 후보 단지(파랑, 번호)
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import replace
from pathlib import Path

import numpy as np

from ..selection import Region, SiteSettings, solve
from ..selection_eval import validate


def log(msg: str) -> None:
    print(msg, flush=True)


def rank_hits(R: Region, res: dict) -> dict:
    """정답 덩어리별로, 겹치는 후보 단지의 순위와 겹침 비율."""
    assign = np.asarray(res["assign"])
    out = []
    for t in range(1, int(R.truth.max()) + 1):
        tm = R.truth == t
        t_area = R.area[tm].sum()
        rows = []
        for c in res["complexes"]:
            k = c["id"]
            cm = assign == k
            inter = R.area[tm & cm].sum()
            if inter > 0:
                rows.append({"rank": k, "area_ha": c["area_ha"], "truth_covered": round(inter / t_area, 3), "complex_inside_truth": round(inter / R.area[cm].sum(), 3)})
        rows.sort(key=lambda r: -r["truth_covered"])
        cum, covered = [], 0.0
        for c in res["complexes"]:
            covered += R.area[tm & (assign == c["id"])].sum() / t_area
            cum.append(round(covered, 3))
        out.append({"truth": t, "truth_ha": round(t_area / 1e4, 1), "overlaps": rows, "cum_cover_by_rank": cum})
    return out


def draw(R: Region, res: dict, path: Path, size: int = 1600) -> None:
    from PIL import Image, ImageDraw

    assign = np.asarray(res["assign"])
    # Region은 경위도 링을 보관하지 않으므로 원본 링을 다시 읽는다
    feats = R.scn["parcels"]["features"]
    pts = np.concatenate([np.asarray(f["geometry"]["coordinates"][0]) for f in feats])
    lon0, lat0, lon1, lat1 = pts[:, 0].min(), pts[:, 1].min(), pts[:, 0].max(), pts[:, 1].max()
    kx = size / (lon1 - lon0)
    ky = kx / np.cos(np.radians((lat0 + lat1) / 2))
    H = int((lat1 - lat0) * ky) + 1
    im = Image.new("RGB", (size, H), "white")
    dr = ImageDraw.Draw(im)
    palette = ["#1f77b4", "#2ca02c", "#9467bd", "#8c564b", "#17becf", "#bcbd22", "#e377c2", "#7f7f7f", "#aec7e8", "#98df8a"]
    for i, f in enumerate(feats):
        ring = [((x - lon0) * kx, (lat1 - y) * ky) for x, y in f["geometry"]["coordinates"][0]]
        k = int(assign[i])
        fill = palette[(k - 1) % len(palette)] if k else ("#f1e3c8" if R.use[i] == "논" else "#ececec")
        dr.polygon(ring, fill=fill, outline="#bbbbbb")
    for i, f in enumerate(feats):
        if R.truth[i] > 0:
            ring = [((x - lon0) * kx, (lat1 - y) * ky) for x, y in f["geometry"]["coordinates"][0]]
            dr.polygon(ring, outline="#d62728", width=2)
    for c in res["complexes"]:
        x, y = c["centroid"]
        dr.text(((x - lon0) * kx - 6, (lat1 - y) * ky - 6), str(c["id"]), fill="black")
    dr.rectangle([0, 0, 470, 22], fill="white")
    dr.text((6, 5), "red outline = truth block (Sentinel-2 proxy); colored = candidate complexes (number = rank)", fill="black")
    im.save(path)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("region", type=Path)
    ap.add_argument("--a-min", type=float, default=20)
    ap.add_argument("--a-max", type=float, default=60)
    ap.add_argument("--n-min", type=int, default=5)
    ap.add_argument("--slope-max", type=float, default=7)
    ap.add_argument("--no-road", action="store_true", help="도로 접속 제약(C5)을 끈다. OSM 도로가 없는 지도에서 비교용")
    ap.add_argument("--png", type=Path)
    ap.add_argument("--json", type=Path)
    a = ap.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    scn = json.loads(a.region.read_text(encoding="utf-8"))
    R = Region(scn)
    R.scn = scn
    s = SiteSettings(a_min_ha=a.a_min, a_max_ha=a.a_max, n_min=a.n_min, slope_max=a.slope_max, owner_mode="proxy", require_road=not a.no_road)
    log(f"[지도] {scn['meta']['name']}: 필지 {R.n}개, 정답 덩어리 {int(R.truth.max())}개 ({R.area[R.truth > 0].sum() / 1e4:.1f}ha)")
    log(f"[조건] 면적 {s.a_min_ha:g}~{s.a_max_ha:g}ha, 농가 ≥{s.n_min}(필지 수 추정), 경사 ≤{s.slope_max:g}%, 도로 접속 {'필수' if s.require_road else '선택'}")

    res = solve(R, s)
    sm = res["summary"]
    log(f"[후보] 단지 {sm['n_complexes']}곳, {sm['complex_area_ha']}ha, {sm['seconds']}초")
    hits = rank_hits(R, res)
    for h in hits:
        log(f"[적중] 정답 {h['truth']} ({h['truth_ha']}ha)")
        for r in h["overlaps"][:5]:
            log(f"   후보 {r['rank']}위 {r['area_ha']}ha: 정답의 {r['truth_covered']:.0%} 덮음, 단지의 {r['complex_inside_truth']:.0%}가 정답 안")
        cum = h["cum_cover_by_rank"]
        marks = {k: cum[k - 1] for k in (1, 3, 5, 10) if k <= len(cum)}
        log(f"   상위 k개 누적 덮음: " + ", ".join(f"k={k} {v:.0%}" for k, v in marks.items()))

    v = validate(R, s)
    log("[재현 검증] 방법 / F1 / 정밀도 / 재현율 / IoU / 제약 충족")
    for row in v["table"]:
        if row.get("available"):
            log(f"   {row['method']:<14} F1 {row['f1']:.2f}  P {row['precision']:.2f}  R {row['recall']:.2f}  IoU {row['iou_mean']:.2f}  충족 {row['feasible']}/{row['complexes']}")
    log(f"[민감도] Jaccard 평균 {v['sensitivity']['mean']:.2f}, 최소 {v['sensitivity']['min']:.2f}")
    for r in v["sensitivity"]["rows"]:
        log(f"   {r['case']:<10} Jaccard {r['jaccard']:.2f}, 단지 {r['n_complexes']}곳 {r['area_ha']}ha")

    if a.png:
        a.png.parent.mkdir(parents=True, exist_ok=True)
        draw(R, res, a.png)
        log(f"[그림] {a.png}")
    if a.json:
        a.json.write_text(json.dumps({"settings": s.__dict__, "summary": sm, "hits": hits, "validate": {k: v[k] for k in ("table", "sensitivity")}}, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
