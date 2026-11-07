"""판넬 QR 코드 생성. 노션 소개 페이지로 연결한다.

사용: python docs/panel/make_qr.py
출력: docs/panel/assets/qr-notion.svg, qr-notion.png
"""
from pathlib import Path
import segno

# 노션 '모두의 농장 — 한눈에 보기' 페이지. 공개(웹에 게시) 상태여야 누구나 열 수 있다.
NOTION_URL = "https://www.notion.so/411ed99f9ad581adacfbfb67a33cb8c6"

out = Path(__file__).parent / "assets"
out.mkdir(parents=True, exist_ok=True)
qr = segno.make(NOTION_URL, error="h")  # 오류 정정 H: 인쇄물 손상·흠집에 강함
qr.save(out / "qr-notion.svg", scale=10, border=2, dark="#0f2d1f", light=None)
qr.save(out / "qr-notion.png", scale=40, border=2, dark="#0f2d1f", light="#ffffff")
print("QR", NOTION_URL, "version", qr.version, "->", out)
