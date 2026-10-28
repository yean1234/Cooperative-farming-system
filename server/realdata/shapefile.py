"""폴리곤 SHP·DBF 최소 리더(ESRI Shapefile 기술 백서 형식). 외부 GIS 라이브러리 없이 numpy만 쓴다.

시군 단위 팜맵 SHP(수십 MB)에서 관심 구역만 꺼내 쓰므로, .shx 색인으로 각 레코드의 경계상자만 먼저 읽어 거른다.
"""
from __future__ import annotations

import struct
from pathlib import Path

import numpy as np


class PolygonShapefile:
    def __init__(self, path: str | Path, encoding: str | None = None) -> None:
        base = Path(path).with_suffix("")
        self.shp_path, self.shx_path, self.dbf_path = (base.with_suffix(s) for s in (".shp", ".shx", ".dbf"))
        cpg = base.with_suffix(".cpg")
        if encoding is None:
            encoding = cpg.read_text(encoding="ascii").strip() if cpg.exists() else "cp949"
        self.encoding = {"EUC-KR": "cp949", "EUCKR": "cp949"}.get(encoding.upper(), encoding)
        shx = self.shx_path.read_bytes()
        rec = np.frombuffer(shx, dtype=">i4", offset=100).reshape(-1, 2)
        self.offsets = rec[:, 0].astype(np.int64) * 2
        self.n = len(self.offsets)
        self._shp = self.shp_path.read_bytes()
        shape_type = struct.unpack_from("<i", self._shp, 32)[0]
        if shape_type not in (5, 15, 25):
            raise ValueError(f"폴리곤 SHP가 아닙니다(shape type {shape_type}).")
        self._fields, self._hlen, self._rlen = self._dbf_header()

    # ------------------------------------------------------------------ 도형
    def bboxes(self) -> np.ndarray:
        """레코드별 [xmin, ymin, xmax, ymax]. 빈 도형(type 0)은 NaN."""
        out = np.full((self.n, 4), np.nan)
        for k, off in enumerate(self.offsets):
            if struct.unpack_from("<i", self._shp, off + 8)[0] != 0:
                out[k] = struct.unpack_from("<4d", self._shp, off + 12)
        return out

    def rings(self, k: int) -> list[np.ndarray]:
        off = int(self.offsets[k]) + 8
        if struct.unpack_from("<i", self._shp, off)[0] == 0:
            return []
        n_parts, n_pts = struct.unpack_from("<2i", self._shp, off + 36)
        parts = list(struct.unpack_from(f"<{n_parts}i", self._shp, off + 44)) + [n_pts]
        pts = np.frombuffer(self._shp, dtype="<f8", count=2 * n_pts, offset=off + 44 + 4 * n_parts).reshape(-1, 2)
        return [pts[parts[i] : parts[i + 1]].copy() for i in range(n_parts)]

    # ------------------------------------------------------------------ 속성
    def _dbf_header(self):
        with open(self.dbf_path, "rb") as f:
            head = f.read(32)
            hlen, rlen = struct.unpack("<HH", head[8:12])
            fields, pos = [], 1
            while True:
                d = f.read(32)
                if not d or d[0] == 0x0D:
                    break
                name = d[:11].split(b"\0")[0].decode("ascii")
                size = d[16]
                fields.append((name, chr(d[11]), pos, size))
                pos += size
        return fields, hlen, rlen

    @property
    def field_names(self) -> list[str]:
        return [f[0] for f in self._fields]

    def records(self, indices) -> list[dict]:
        out = []
        with open(self.dbf_path, "rb") as f:
            for k in indices:
                f.seek(self._hlen + int(k) * self._rlen)
                raw = f.read(self._rlen)
                row = {}
                for name, typ, pos, size in self._fields:
                    v = raw[pos : pos + size].decode(self.encoding, "replace").strip()
                    if typ in ("N", "F"):
                        try:
                            v = float(v) if v else None
                        except ValueError:
                            v = None
                    row[name] = v
                out.append(row)
        return out


def signed_area(ring: np.ndarray) -> float:
    """SHP 외곽 링은 시계 방향(음수)이다."""
    x, y = ring[:, 0], ring[:, 1]
    return 0.5 * float(np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1)))
