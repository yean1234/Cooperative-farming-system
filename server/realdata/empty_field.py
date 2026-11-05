"""빈 필드 시뮬레이터용 실측 단지 데이터 만들기.

흥해 실측 지도(data/regions/heunghae.json)에서 후보단지 엔진이 고른 단지 하나와
그 주변 필지를 잘라 web/data/empty-field-heunghae.js로 내보낸다.
브라우저가 파일로 바로 열어도 읽을 수 있게 JSON이 아니라 `window.EF_DATA = {...}` 스크립트로 쓴다.

소유자: 농지대장이 비공개라 실제 소유자를 알 수 없다. 대신 이웃한 필지를 묶어 농가를 추정한다.
농가 1곳의 목표 면적은 1.8ha다. 위천친환경영농조합법인(상주, 2026 공동영농 확산 지원사업 선정)이
30ha·17농가, 즉 농가당 약 1.8ha라는 보도에 맞췄다. 추정값이므로 화면에 '추정 소유자'로 표시한다.

실행:
    python -m server.realdata.empty_field            # 기본: 단지 6
    python -m server.realdata.empty_field --complex 3
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np

from ..geo import open_ring
from ..selection import Region, SiteSettings, solve

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "data" / "regions" / "heunghae.json"
OUT = ROOT / "web" / "data" / "empty-field-heunghae.js"

FARM_HA = 1.8      # 추정 농가 1곳 목표 면적
BUFFER_M = 260     # 단지 주변으로 함께 보여 줄 거리(편입 후보 범위)
SEED = 11


def simplify(ring: np.ndarray, tol: float = 1.2) -> np.ndarray:
    """Douglas-Peucker로 꼭짓점을 줄인다(화면용, 면적 계산은 원본 값을 쓴다)."""
    if len(ring) <= 4:
        return ring

    def rdp(pts):
        a, b = pts[0], pts[-1]
        ab = b - a
        L = float(np.hypot(*ab))
        rel = pts - a
        d = np.abs(ab[0] * rel[:, 1] - ab[1] * rel[:, 0]) / L if L > 1e-9 else np.hypot(rel[:, 0], rel[:, 1])
        k = int(np.argmax(d))
        if d[k] > tol and 0 < k < len(pts) - 1:
            return np.vstack([rdp(pts[: k + 1])[:-1], rdp(pts[k:])])
        return np.vstack([a, b])

    # 고리를 가장 먼 두 점에서 둘로 나눠 각각 줄인다
    far = int(np.argmax(np.hypot(*(ring - ring[0]).T)))
    first = rdp(ring[: far + 1])
    second = rdp(np.vstack([ring[far:], ring[:1]]))
    out = np.vstack([first[:-1], second[:-1]])
    return out if len(out) >= 3 else ring


def group_farms(ids: list[int], area_ha: dict[int, float], nbrs: list[dict[int, float]], rng, prefix: str) -> dict[int, str]:
    """이웃한 필지를 묶어 농가를 추정한다. 한 농가는 약 FARM_HA가 될 때까지 붙어 있는 필지를 모은다."""
    pool = set(ids)
    order = sorted(ids)
    rng.shuffle(order)
    owner: dict[int, str] = {}
    k = 0
    for seed in order:
        if seed in owner:
            continue
        k += 1
        name = f"{prefix}{k}"
        grp, frontier, a = [seed], [seed], area_ha[seed]
        owner[seed] = name
        while frontier and a < FARM_HA:
            cur = frontier.pop(0)
            for nb in sorted(nbrs[cur], key=lambda j: -nbrs[cur][j]):
                if nb in pool and nb not in owner and a < FARM_HA:
                    owner[nb] = name
                    grp.append(nb)
                    frontier.append(nb)
                    a += area_ha[nb]
    # 너무 작은 농가(0.4ha 미만)는 이웃 농가에 붙인다
    sizes: dict[str, float] = {}
    for i, n in owner.items():
        sizes[n] = sizes.get(n, 0) + area_ha[i]
    for i in sorted(owner):
        n = owner[i]
        if sizes[n] < 0.4:
            cands = [owner[j] for j in nbrs[i] if j in owner and owner[j] != n]
            if cands:
                tgt = max(cands, key=lambda m: sizes[m])
                for j in [j for j in owner if owner[j] == n]:
                    owner[j] = tgt
                sizes[tgt] += sizes.pop(n)
    return owner


def build(complex_id: int = 6) -> dict:
    scn = json.loads(SRC.read_text(encoding="utf-8"))
    R = Region(scn)
    rep = solve(R, SiteSettings(owner_mode="proxy"))
    comp = next(c for c in rep["complexes"] if c["id"] == complex_id)
    idx_of = {pid: i for i, pid in enumerate(R.ids)}
    members = [idx_of[p] for p in comp["parcels"] if R.use[idx_of[p]] == "논"]
    mset = set(members)

    # 단지 주변 BUFFER_M 안의 필지
    mx, my = R.cx[members], R.cy[members]
    near = []
    for i in range(R.n):
        if i in mset:
            continue
        if np.min(np.hypot(mx - R.cx[i], my - R.cy[i])) <= BUFFER_M:
            near.append(i)
    keep = members + near
    kset = set(keep)

    # 다른 후보단지에 든 필지는 편입 후보에서 뺀다(배경으로만 보여 준다)
    other = {idx_of[p] for c in rep["complexes"] if c["id"] != complex_id for p in c["parcels"]}
    area_ha = {i: float(R.area[i]) / 1e4 for i in keep}
    nbrs = [{j: L for j, L in R.nbrs[i].items() if j in kset} if i in kset else {} for i in range(R.n)]
    rng = np.random.default_rng(SEED)
    owner = group_farms(members, area_ha, nbrs, rng, "참여 ")
    cand_ids = [i for i in near if R.use[i] == "논" and i not in other]
    owner.update(group_farms(cand_ids, area_ha, nbrs, rng, "이웃 "))

    # 화면 좌표: 단지 주변 영역의 왼쪽 위를 원점으로, y는 아래로 증가(SVG)
    rings = {}
    for i in keep:
        g = scn["parcels"]["features"][i]["geometry"]
        rll = np.asarray(g["coordinates"][0] if g["type"] == "Polygon" else max((p[0] for p in g["coordinates"]), key=len), float)
        rings[i] = open_ring(R.proj.to_xy(rll))
    allxy = np.vstack(list(rings.values()))
    x0, y1 = float(allxy[:, 0].min()) - 40, float(allxy[:, 1].max()) + 40
    x1, y0 = float(allxy[:, 0].max()) + 40, float(allxy[:, 1].min()) - 40
    T = lambda xy: [[round(float(x - x0), 1), round(float(y1 - y), 1)] for x, y in xy]

    order = sorted(keep, key=lambda i: (round(R.cy[i]), R.cx[i]))
    local = {i: k for k, i in enumerate(order)}
    parcels = []
    for i in order:
        kind = "member" if i in mset else ("cand" if i in owner else "bg")
        parcels.append({
            "id": local[i],
            "pnu": scn["parcels"]["features"][i]["properties"].get("pnu", ""),
            "use": R.use[i],
            "area": round(area_ha[i], 4),
            "soil": int(R.soil[i]),
            "drain": int(R.drain[i]),
            "kind": kind,
            "owner": owner.get(i),
            "c": T([[R.cx[i], R.cy[i]]])[0],
            "poly": T(simplify(rings[i])),
            "nb": sorted(local[j] for j in nbrs[i]),
        })

    # 도로: 영역 안을 지나는 OSM 도로선
    roads = []
    for line in scn["meta"].get("roads", []):
        xy = R.proj.to_xy(np.asarray(line, float))
        inside = (xy[:, 0] >= x0) & (xy[:, 0] <= x1) & (xy[:, 1] >= y0) & (xy[:, 1] <= y1)
        if inside.any():
            roads.append(T(xy))

    # 농기계 보관소: 단지 바깥 도로 위에서 단지에 가장 가까운 점(단지 한가운데 농로는 피한다).
    # 단지 필지 중심에서 90~200m 떨어진 도로 꼭짓점 가운데, 단지 경계 쪽으로 가장 가까운 점을 고른다.
    cxy = np.array([float(np.mean(mx)), float(np.mean(my))])
    best = None
    for line in scn["meta"].get("roads", []):
        xy = R.proj.to_xy(np.asarray(line, float))
        for p in xy:
            d = float(np.min(np.hypot(mx - p[0], my - p[1])))
            if 90 <= d <= 200 and (best is None or d < best[0]):
                best = (d, p)
    depot = T([best[1]])[0] if best else T([[float(mx.min()) - 60, float(my.min()) - 60]])[0]

    n_member_farms = len({owner[i] for i in members})
    return {
        "meta": {
            "name": scn["meta"]["name"],
            "complex_id": complex_id,
            "complex_area_ha": round(sum(area_ha[i] for i in members), 2),
            "n_member_parcels": len(members),
            "n_member_farms": n_member_farms,
            "farm_ha": FARM_HA,
            "buffer_m": BUFFER_M,
            "width": round(x1 - x0, 1),
            "height": round(y1 - y0, 1),
            "owner_note": "소유자는 추정값입니다(농지대장 비공개). 이웃한 필지를 농가당 약 1.8ha가 되도록 묶었습니다.",
            "sources": "팜맵 필지 경계·논밭 구분, 흙토람 토양·배수, OpenStreetMap 도로 (data/regions/heunghae.json)",
        },
        "depot": depot,
        "roads": roads,
        "parcels": parcels,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--complex", type=int, default=6, help="후보단지 번호(기본 6)")
    ap.add_argument("--out", type=Path, default=OUT)
    a = ap.parse_args()
    data = build(a.complex)
    a.out.parent.mkdir(parents=True, exist_ok=True)
    body = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    a.out.write_text(f"/* 자동 생성: python -m server.realdata.empty_field --complex {a.complex} */\nwindow.EF_DATA = {body};\n", encoding="utf-8")
    m = data["meta"]
    kinds = {}
    for p in data["parcels"]:
        kinds[p["kind"]] = kinds.get(p["kind"], 0) + 1
    print(f"{a.out} · 단지 {m['complex_id']} {m['complex_area_ha']}ha · 참여 필지 {m['n_member_parcels']}개 · 추정 참여 농가 {m['n_member_farms']}곳 · {kinds} · {a.out.stat().st_size // 1024}KB")


if __name__ == "__main__":
    main()
