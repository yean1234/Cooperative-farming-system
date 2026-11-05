"""Sentinel-2 위성영상으로 '실제 공동영농 단지'의 근사 경계(정답 라벨)를 만든다.

왜 이렇게 하나
  실제 단지 경계는 공개되지 않는다. 대신 작목 전환이 위성에서 보인다.
  문경 영순(늘봄영농조합법인)은 2023년에 벼 일모작을 콩 + 양파·감자 이모작으로 바꿨다.
  6월 중순 논은 모내기 뒤 물을 댄 상태라 물 지수(NDWI)가 높지만, 콩을 심은 논은 마른 땅이다.
  그래서 "논인데 6월에 담수되지 않은 필지"가 한 덩어리로 100ha 안팎 모이면 그것이 단지 경계의 근사값이다.

무엇을 하나
  1. Earth Search STAC(AWS, 키 불필요)에서 날짜·구역에 맞는 Sentinel-2 L2A 장면을 찾는다.
  2. 필지를 10m 격자에 래스터화하고 장면별 NDWI·NDVI 필지 평균을 구한다(SCL로 구름 제외).
  3. 논 필지 가운데 NDWI가 문턱값보다 낮은(담수 안 된) 필지를 고르고, 인접 그래프로 덩어리를 만든다.
  4. 면적 범위에 드는 덩어리에 truth_complex 번호를 붙여 지도 JSON에 써 넣는다. 확인용 PNG도 그린다.

사용
  python -m server.realdata.truth_s2 data/regions/yeongsun.json --dates 2023-06-16 2023-06-06 \
      --min-ha 60 --max-ha 200 --out data/regions/yeongsun.json --png out/yeongsun_truth.png

주의
  이 라벨은 위성 판독으로 만든 추정값이다. 기사 사진·항공사진으로 한 번 눈으로 대조한 뒤 쓴다.
  문턱값(--ndwi)을 안 주면 논 필지 NDWI 분포를 둘로 가르는 값(Otsu)을 쓴다. 분포가 둘로 안 갈리면 결과를 믿지 않는다.
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from pathlib import Path

import numpy as np

STAC = "https://earth-search.aws.element84.com/v1/search"
UA = {"User-Agent": "Mozilla/5.0", "Content-Type": "application/json"}
SCL_BAD = {0, 1, 2, 3, 8, 9, 10, 11}  # no data, saturated, dark, cloud shadow, cloud(med/high), cirrus, snow


def log(msg: str) -> None:
    print(msg, flush=True)


# ------------------------------------------------------------------ STAC
def find_scenes(bbox, dates: list[str], max_cloud: float, n_scenes: int = 2) -> list[dict]:
    """날짜('2023-06-16')마다 구름이 가장 적은 장면 하나씩. 기간('2023-06-01/2023-06-30')이면 가장 맑은 n_scenes개."""
    out = []
    for d in dates:
        rng = d if "/" in d else f"{d}/{d}"
        d0, d1 = rng.split("/")
        body = {
            "collections": ["sentinel-2-l2a"],
            "bbox": list(bbox),
            "datetime": f"{d0}T00:00:00Z/{d1}T23:59:59Z",
            "limit": 100,
        }
        req = urllib.request.Request(STAC, data=json.dumps(body).encode(), headers=UA)
        with urllib.request.urlopen(req, timeout=60) as r:
            feats = json.load(r).get("features", [])
        feats = [f for f in feats if f["properties"].get("eo:cloud_cover", 100) <= max_cloud]
        if not feats:
            log(f"[S2] {d}: 구름 {max_cloud}% 이하 장면 없음, 건너뜀")
            continue
        feats.sort(key=lambda f: f["properties"].get("eo:cloud_cover", 100))
        pick = feats[: (n_scenes if "/" in d else 1)]
        # 같은 날 여러 타일이 겹치면 구역을 덮는 타일 하나만
        seen = set()
        for f in pick:
            if f["properties"]["datetime"][:10] in seen:
                continue
            seen.add(f["properties"]["datetime"][:10])
            out.append(f)
            log(f"[S2] {f['properties']['datetime'][:10]}: {f['id']} 구름 {f['properties'].get('eo:cloud_cover'):.1f}%")
    return out


# ------------------------------------------------------------------ 래스터화·구역 통계
def parcel_indices(scn: dict, scene: dict, pad_px: int = 2):
    """장면 좌표계에서 필지를 10m 격자에 래스터화한다. (window, transform, label 배열)"""
    import rasterio
    from pyproj import Transformer
    from rasterio import features as rfeat
    from rasterio.windows import Window

    href = scene["assets"]["green"]["href"]
    with rasterio.Env(GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR", CPL_VSIL_CURL_ALLOWED_EXTENSIONS=".tif"):
        with rasterio.open(href) as ds:
            crs, base_tr = ds.crs, ds.transform
            tr = Transformer.from_crs("EPSG:4326", crs, always_xy=True)
            feats = scn["parcels"]["features"]
            polys = []
            for f in feats:
                ring = f["geometry"]["coordinates"][0]
                xs, ys = tr.transform([p[0] for p in ring], [p[1] for p in ring])
                polys.append({"type": "Polygon", "coordinates": [list(zip(xs, ys))]})
            allx = np.concatenate([[p[0] for p in g["coordinates"][0]] for g in polys])
            ally = np.concatenate([[p[1] for p in g["coordinates"][0]] for g in polys])
            r0, c0 = ds.index(allx.min(), ally.max())
            r1, c1 = ds.index(allx.max(), ally.min())
            r0, c0 = max(r0 - pad_px, 0), max(c0 - pad_px, 0)
            r1, c1 = min(r1 + pad_px, ds.height - 1), min(c1 + pad_px, ds.width - 1)
            win = Window(c0, r0, c1 - c0 + 1, r1 - r0 + 1)
            wtr = ds.window_transform(win)
            labels = rfeat.rasterize(
                [(g, i + 1) for i, g in enumerate(polys)], out_shape=(int(win.height), int(win.width)),
                transform=wtr, fill=0, all_touched=False, dtype="int32",
            )
    return win, wtr, labels


def read_band(href: str, win) -> np.ndarray:
    import rasterio

    with rasterio.Env(GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR", CPL_VSIL_CURL_ALLOWED_EXTENSIONS=".tif"):
        with rasterio.open(href) as ds:
            return ds.read(1, window=win).astype(np.float32)


def zonal_mean(values: np.ndarray, labels: np.ndarray, valid: np.ndarray, n: int) -> np.ndarray:
    """필지별 평균. 유효 화소가 없으면 NaN."""
    m = valid & (labels > 0)
    s = np.bincount(labels[m], weights=values[m], minlength=n + 1)[1:]
    c = np.bincount(labels[m], minlength=n + 1)[1:]
    with np.errstate(invalid="ignore", divide="ignore"):
        return np.where(c > 0, s / np.maximum(c, 1), np.nan)


def scene_indices(scn: dict, scene: dict) -> dict:
    """장면 하나에서 필지별 NDWI(물), NDVI(식생), 유효 화소 비율."""
    import rasterio
    from rasterio.windows import Window

    win, wtr, labels = parcel_indices(scn, scene)
    n = len(scn["parcels"]["features"])
    a = scene["assets"]
    green = read_band(a["green"]["href"], win)
    nir = read_band(a["nir"]["href"], win)
    red = read_band(a["red"]["href"], win)
    # SCL은 20m 격자라 창을 절반 크기로 읽고 2배로 늘린다
    with rasterio.Env(GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR", CPL_VSIL_CURL_ALLOWED_EXTENSIONS=".tif"):
        with rasterio.open(a["scl"]["href"]) as ds:
            w2 = Window(win.col_off / 2, win.row_off / 2, win.width / 2, win.height / 2)
            scl = ds.read(1, window=w2, out_shape=(int(win.height), int(win.width)), resampling=rasterio.enums.Resampling.nearest)
    valid = ~np.isin(scl, list(SCL_BAD)) & (green > 0) & (nir > 0)
    with np.errstate(invalid="ignore", divide="ignore"):
        ndwi = (green - nir) / (green + nir)
        ndvi = (nir - red) / (nir + red)
    cover = zonal_mean(valid.astype(np.float32), labels, np.ones_like(valid), n)
    return {
        "id": scene["id"],
        "date": scene["properties"]["datetime"][:10],
        "ndwi": zonal_mean(ndwi, labels, valid, n),
        "ndvi": zonal_mean(ndvi, labels, valid, n),
        "cover": cover,
    }


# ------------------------------------------------------------------ 문턱값·덩어리
def otsu(x: np.ndarray, bins: int = 64) -> float:
    x = x[np.isfinite(x)]
    hist, edges = np.histogram(x, bins=bins)
    mids = (edges[:-1] + edges[1:]) / 2
    w = hist.astype(float)
    tot = w.sum()
    best, thr = -1.0, float(np.median(x))
    for k in range(1, bins):
        w0, w1 = w[:k].sum(), w[k:].sum()
        if w0 == 0 or w1 == 0:
            continue
        m0, m1 = (w[:k] * mids[:k]).sum() / w0, (w[k:] * mids[k:]).sum() / w1
        v = w0 * w1 * (m0 - m1) ** 2 / tot**2
        if v > best:
            best, thr = v, float(edges[k])
    return thr


def components(scn: dict, mask: np.ndarray) -> list[list[int]]:
    """adjacency(인접 간선)로 mask가 참인 필지의 연결 덩어리를 만든다."""
    n = len(mask)
    nb: list[list[int]] = [[] for _ in range(n)]
    for i, j, *_ in scn.get("adjacency", []):
        i, j = int(i), int(j)
        if mask[i] and mask[j]:
            nb[i].append(j)
            nb[j].append(i)
    seen = np.zeros(n, dtype=bool)
    comps = []
    for s in np.flatnonzero(mask):
        if seen[s]:
            continue
        stack, comp = [int(s)], []
        seen[s] = True
        while stack:
            c = stack.pop()
            comp.append(c)
            for q in nb[c]:
                if not seen[q]:
                    seen[q] = True
                    stack.append(q)
        comps.append(comp)
    return comps


# ------------------------------------------------------------------ 그림
def draw_png(scn: dict, truth: np.ndarray, dry: np.ndarray, path: Path, size: int = 1600) -> None:
    from PIL import Image, ImageDraw

    feats = scn["parcels"]["features"]
    pts = np.concatenate([np.asarray(f["geometry"]["coordinates"][0]) for f in feats])
    lon0, lat0, lon1, lat1 = pts[:, 0].min(), pts[:, 1].min(), pts[:, 0].max(), pts[:, 1].max()
    kx = size / (lon1 - lon0)
    ky = kx * 1.0 / np.cos(np.radians((lat0 + lat1) / 2))
    H = int((lat1 - lat0) * ky) + 1
    im = Image.new("RGB", (size, H), "white")
    dr = ImageDraw.Draw(im)
    for i, f in enumerate(feats):
        ring = [((x - lon0) * kx, (lat1 - y) * ky) for x, y in f["geometry"]["coordinates"][0]]
        use = f["properties"].get("land_use")
        if truth[i] > 0:
            fill = ["#d62728", "#ff7f0e", "#9467bd", "#8c564b"][(truth[i] - 1) % 4]
        elif dry[i]:
            fill = "#f2c572"  # 담수 안 된 논인데 덩어리 조건에는 못 든 필지
        elif use == "논":
            fill = "#9ecae1"
        elif use == "밭":
            fill = "#e5e5c0"
        else:
            fill = "#dddddd"
        dr.polygon(ring, fill=fill, outline="#888888")
    dr.rectangle([0, 0, 420, 90], fill="white")
    dr.text((8, 6), "red/orange = truth complex, tan = dry paddy (not in complex)", fill="black")
    dr.text((8, 24), "blue = flooded paddy, beige = upland", fill="black")
    im.save(path)


# ------------------------------------------------------------------ 메인
def main() -> None:
    ap = argparse.ArgumentParser(description="Sentinel-2로 공동영농 단지 근사 경계(truth_complex)를 지도 JSON에 붙인다")
    ap.add_argument("region", type=Path, help="build.py로 만든 지도 JSON")
    ap.add_argument("--dates", nargs="+", default=["2023-06-01/2023-06-30"], help="담수기 장면 날짜 또는 기간(시작/끝). 여러 장면이면 필지별 NDWI 중앙값")
    ap.add_argument("--max-cloud", type=float, default=30.0)
    ap.add_argument("--n-scenes", type=int, default=2, help="기간을 줬을 때 쓸 가장 맑은 장면 수")
    ap.add_argument("--ndwi", type=float, help="담수 판정 문턱값. 없으면 논 필지 분포에서 Otsu로 정한다")
    ap.add_argument("--min-cover", type=float, default=0.6, help="필지 안 유효(구름 아님) 화소 비율 하한")
    ap.add_argument("--min-ha", type=float, default=60.0, help="단지로 볼 덩어리 최소 면적")
    ap.add_argument("--max-ha", type=float, default=250.0)
    ap.add_argument("--fill-holes", action="store_true", help="덩어리에 둘러싸인(이웃이 모두 덩어리인) 담수 논도 단지에 넣는다")
    ap.add_argument("--out", type=Path, help="저장할 JSON(없으면 region을 덮어쓴다)")
    ap.add_argument("--png", type=Path, help="확인용 그림")
    ap.add_argument("--csv", type=Path, help="필지별 지표 CSV")
    a = ap.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    scn = json.loads(a.region.read_text(encoding="utf-8"))
    feats = scn["parcels"]["features"]
    n = len(feats)
    bbox = scn["meta"]["bbox"]
    area_ha = np.array([f["properties"]["area_m2"] for f in feats]) / 1e4
    paddy = np.array([f["properties"].get("land_use") == "논" for f in feats])
    log(f"[지도] {scn['meta']['name']}: 필지 {n}개, 논 {paddy.sum()}개 {area_ha[paddy].sum():.1f}ha")

    scenes = find_scenes(bbox, a.dates, a.max_cloud, a.n_scenes)
    if not scenes:
        raise SystemExit("쓸 수 있는 장면이 없습니다. --dates 또는 --max-cloud를 바꿔 보세요.")
    per = [scene_indices(scn, s) for s in scenes]
    for p in per:
        ok = np.isfinite(p["ndwi"]) & (p["cover"] >= a.min_cover)
        log(f"[지표] {p['date']}: 유효 필지 {ok.sum()}/{n}, 논 NDWI 중앙값 {np.nanmedian(p['ndwi'][paddy & ok]):.3f}")
    ndwi_all = np.vstack([np.where(p["cover"] >= a.min_cover, p["ndwi"], np.nan) for p in per])
    ndvi_all = np.vstack([np.where(p["cover"] >= a.min_cover, p["ndvi"], np.nan) for p in per])
    with np.errstate(all="ignore"):
        ndwi = np.nanmedian(ndwi_all, axis=0)
        ndvi = np.nanmedian(ndvi_all, axis=0)

    valid = np.isfinite(ndwi)
    thr = a.ndwi if a.ndwi is not None else otsu(ndwi[paddy & valid])
    lo, hi = np.nanpercentile(ndwi[paddy & valid], [10, 90])
    log(f"[문턱] NDWI {thr:.3f} ({'지정' if a.ndwi is not None else 'Otsu'}), 논 10~90분위 {lo:.3f}~{hi:.3f}")
    dry = paddy & valid & (ndwi < thr)
    log(f"[판정] 담수 안 된 논 {dry.sum()}개 {area_ha[dry].sum():.1f}ha / 담수 논 {(paddy & valid & ~dry).sum()}개")

    comps = sorted(components(scn, dry), key=lambda c: -area_ha[c].sum())
    truth = np.zeros(n, dtype=int)
    k = 0
    for comp in comps:
        ha = area_ha[comp].sum()
        if ha < a.min_ha:
            break
        if ha > a.max_ha:
            log(f"  덩어리 {ha:.1f}ha는 --max-ha {a.max_ha}를 넘어 제외(문턱값을 올려 보세요)")
            continue
        k += 1
        truth[comp] = k
        log(f"  truth {k}: 필지 {len(comp)}개 {ha:.1f}ha")
    if a.fill_holes and k:
        nb: list[set] = [set() for _ in range(n)]
        for i, j, *_ in scn.get("adjacency", []):
            nb[int(i)].add(int(j))
            nb[int(j)].add(int(i))
        for i in np.flatnonzero(paddy & (truth == 0)):
            around = {truth[j] for j in nb[i] if truth[j] > 0}
            if len(around) == 1 and all(truth[j] > 0 for j in nb[i]) and nb[i]:
                truth[i] = around.pop()
    if not k:
        top = [round(area_ha[c].sum(), 1) for c in comps[:5]]
        log(f"[결과] 면적 범위에 드는 덩어리가 없습니다. 큰 덩어리 상위: {top} ha. --ndwi, --min-ha를 조정하세요.")

    for i, f in enumerate(feats):
        p = f["properties"]
        p["truth_complex"] = int(truth[i])
        p["s2_ndwi"] = None if not np.isfinite(ndwi[i]) else round(float(ndwi[i]), 4)
        p["s2_ndvi"] = None if not np.isfinite(ndvi[i]) else round(float(ndvi[i]), 4)
        p["s2_dry_paddy"] = bool(dry[i])
    scn["meta"]["truth_complexes"] = int(k)
    scn["meta"]["truth_source"] = {
        "method": "Sentinel-2 L2A NDWI: 담수기(6월)에 담수되지 않은 논 필지의 연결 덩어리",
        "scenes": [p["id"] for p in per],
        "ndwi_threshold": round(float(thr), 4),
        "min_ha": a.min_ha,
        "max_ha": a.max_ha,
        "note": "위성 판독 추정값. 실제 단지 경계가 아니라 작목 전환 구역의 근사이며, 기사·항공사진으로 대조해야 한다.",
    }
    out = a.out or a.region
    out.write_text(json.dumps(scn, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    log(f"[저장] {out}: truth 단지 {k}개")
    if a.csv:
        with open(a.csv, "w", encoding="utf-8") as fh:
            fh.write("id,land_use,area_ha,ndwi,ndvi,dry_paddy,truth\n")
            for i, f in enumerate(feats):
                p = f["properties"]
                fh.write(f"{p['id']},{p.get('land_use')},{area_ha[i]:.3f},{p['s2_ndwi']},{p['s2_ndvi']},{int(dry[i])},{truth[i]}\n")
    if a.png:
        a.png.parent.mkdir(parents=True, exist_ok=True)
        draw_png(scn, truth, dry, a.png)
        log(f"[그림] {a.png}")


if __name__ == "__main__":
    main()
