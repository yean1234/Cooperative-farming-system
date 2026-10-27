"""`python -m server` 로 개발 서버를 띄운다."""
from __future__ import annotations

import argparse

import uvicorn


def main() -> None:
    ap = argparse.ArgumentParser(description="공동영농 필지 최적화 서비스 초안 서버")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--reload", action="store_true", help="코드 변경 시 자동 재시작")
    args = ap.parse_args()
    print(f"브라우저에서 http://{args.host}:{args.port} 를 여세요.")
    uvicorn.run("server.main:app", host=args.host, port=args.port, reload=args.reload)


if __name__ == "__main__":
    main()
