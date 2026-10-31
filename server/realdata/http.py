"""외부 API 호출 공용 도구: .env 키 읽기, 디스크 캐시, 재시도.

공공데이터포털 개발계정은 하루 1,000~10,000건으로 제한되므로 응답을 data/cache/에 저장해 다시 부르지 않는다.
캐시 파일 이름에는 인증키를 넣지 않는다.
"""
from __future__ import annotations

import hashlib
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CACHE = ROOT / "data" / "cache"
UA = "Cooperative-farming-system/0.2 (Handong-POSTECH hackathon; research use)"


def load_env(path: Path | None = None) -> None:
    """저장소 루트 .env의 KEY=VALUE 줄을 환경변수로 올린다(이미 있는 값은 그대로 둔다)."""
    p = path or ROOT / ".env"
    if not p.exists():
        return
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def key(name: str) -> str | None:
    load_env()
    v = os.environ.get(name, "").strip()
    return v or None


def _cache_path(ns: str, ident: str, ext: str) -> Path:
    return CACHE / ns / (hashlib.sha1(ident.encode("utf-8")).hexdigest()[:20] + ext)


def _get_ident(url: str, params: dict | None) -> str:
    return url + "?" + urllib.parse.urlencode(sorted((params or {}).items()))


def is_cached(url: str, params: dict | None, ns: str) -> bool:
    return _cache_path(ns, _get_ident(url, params), ".bin").exists()


def uncache(url: str, params: dict | None, ns: str) -> None:
    """오류 응답처럼 다시 불러야 할 응답을 캐시에서 지운다."""
    _cache_path(ns, _get_ident(url, params), ".bin").unlink(missing_ok=True)


def _fetch(req: urllib.request.Request, timeout: float, retries: int = 3) -> bytes:
    last = None
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as res:
                return res.read()
        except (urllib.error.URLError, TimeoutError) as exc:
            last = exc
            if isinstance(exc, urllib.error.HTTPError) and exc.code not in (429, 500, 502, 503, 504):
                raise
            time.sleep(2.0 * (attempt + 1))
    raise last  # type: ignore[misc]


def get(url: str, params: dict | None = None, *, secret: dict | None = None, ns: str | None = None,
        timeout: float = 60.0) -> bytes:
    """GET. secret 매개변수(인증키)는 요청에만 붙이고 캐시 식별자에는 넣지 않는다."""
    ident = _get_ident(url, params)
    if ns:
        cp = _cache_path(ns, ident, ".bin")
        if cp.exists():
            return cp.read_bytes()
    q = dict(params or {})
    q.update(secret or {})
    full = url + ("?" + urllib.parse.urlencode(q) if q else "")
    body = _fetch(urllib.request.Request(full, headers={"User-Agent": UA}), timeout)
    if ns:
        cp.parent.mkdir(parents=True, exist_ok=True)
        cp.write_bytes(body)
    return body


def get_json(url: str, params: dict | None = None, **kw):
    return json.loads(get(url, params, **kw).decode("utf-8"))


def post_form(url: str, form: dict, *, ns: str | None = None, timeout: float = 120.0) -> bytes:
    data = urllib.parse.urlencode(form).encode("utf-8")
    if ns:
        cp = _cache_path(ns, url + "\n" + data.decode("utf-8"), ".bin")
        if cp.exists():
            return cp.read_bytes()
    req = urllib.request.Request(url, data=data, headers={"User-Agent": UA, "Content-Type": "application/x-www-form-urlencoded"})
    body = _fetch(req, timeout)
    if ns:
        cp.parent.mkdir(parents=True, exist_ok=True)
        cp.write_bytes(body)
    return body


def get_range(url: str, start: int, end: int, *, ns: str = "range", timeout: float = 120.0) -> bytes:
    cp = _cache_path(ns, f"{url}#{start}-{end}", ".bin")
    if cp.exists():
        return cp.read_bytes()
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Range": f"bytes={start}-{end}"})
    body = _fetch(req, timeout)
    cp.parent.mkdir(parents=True, exist_ok=True)
    cp.write_bytes(body)
    return body
