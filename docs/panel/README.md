# 판넬 (OPEN HARBOR 2026 비즈니스톤 · 팀 물개 · 모두의 농장)

판넬 제작 기준(완성 1200×1800mm, 작업 크기 1220×1820mm, 바탕색 사방 10mm 여유, PDF 제출, 파일명 `비즈니스톤_팀명_아이템명_판넬`)에 맞춰
HTML로 만들고 Playwright(Chromium)로 PDF를 뽑는다. 설계 단위는 **1px = 1mm** 다.

| 파일 | 역할 |
|---|---|
| `panel.html` | 판넬 본문. `html[data-sangju]`가 `indirect`면 상주시청 간접 인용 카드를 넣고, `omit`이면 카드를 빼고 한 줄 서술로 바꾼다 |
| `capture.js` | 시제품 실행 화면 캡처(2배 해상도). 서버가 떠 있어야 한다 |
| `make_qr.py` | 노션 소개 페이지 QR(SVG·PNG, 오류 정정 H) |
| `build.js` | PDF(1220×1820mm)와 미리보기 PNG 생성 |
| `assets/` | 캡처 화면, QR |
| `fonts/` | Pretendard Variable(OFL) |
| `out/` | 제출용 PDF와 미리보기 |

## 다시 만들기

```bash
python -m server &                      # 캡처용 서버
node docs/panel/capture.js              # assets/shot-*.png
python docs/panel/make_qr.py            # assets/qr-notion.svg
node docs/panel/build.js                # out/비즈니스톤_물개_모두의농장_판넬.pdf (기본: 상주시청 간접 인용)
node docs/panel/build.js --omit         # out/…_판넬_상주제외.pdf (카드 제외, 한 줄 서술)
```

QR은 노션 페이지 **모두의 농장 — 한눈에 보기** (`https://www.notion.so/411ed99f9ad581adacfbfb67a33cb8c6`)로 연결된다.
노션에서 그 페이지를 **공유 → 웹에 게시**로 공개해야 심사위원이 QR로 열 수 있다. 주소는 바뀌지 않으므로 판넬 출력 뒤에도 페이지 내용은 고쳐도 된다.

## 제출 전 확인

- [x] 작업 크기 1220×1820mm, 내용은 1200×1800mm 안 (PDF MediaBox 1220.2×1820.3mm 확인)
- [x] 바탕색이 재단선 밖 10mm까지 채워짐 (상단 초록, 하단 연두 띠, 본문 흰색)
- [x] 글자·QR은 재단선에서 50mm 이상 안쪽 (하단 QR은 22mm)
- [x] QR 스캔 확인 (미리보기 PNG에서 OpenCV로 복호화)
- [ ] 노션 페이지 웹 게시 켜기 (판넬 제출 전 휴대폰으로 한 번 더 스캔)
- [ ] `openharbor2026@gmail.com` 에 제목 `[판넬 제출] 물개` 로 PDF 1개 보내기 (11/7 토 23:59까지)
