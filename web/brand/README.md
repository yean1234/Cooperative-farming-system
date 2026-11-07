# 모두의 농장 로고 파일

세트 A '모이는 들녘'(brand-identity 키트, `docs/brand/kit/`)에서 웹 화면용으로 뽑은 파일이다. 모두 벡터(SVG)라 크기 제한이 없다.

| 파일 | 쓰는 곳 |
|---|---|
| `logo-lockup.svg` | 밝은 바탕 헤더·푸터의 가로 락업(마크 + 모두의 농장) |
| `logo-lockup-white.svg` | 어두운 바탕(남색·초록) 헤더·푸터 |
| `logo-lockup-mono.svg` | 단색. `color` CSS 값으로 색이 정해진다(푸터 회색 등) |
| `logo-mark.svg` · `logo-mark-white.svg` · `logo-mark-mono.svg` | 마크만. 좁은 자리, 버튼, 내비게이션 |
| `favicon.svg` · `favicon-32.png` | 브라우저 탭 아이콘 |
| `app-icon-512.png` | 앱 아이콘, 공유 썸네일 |
| `logo-lockup.png` | SVG를 못 쓰는 곳(메일 서명 등) |

쓰는 법

```html
<link rel="icon" href="brand/favicon.svg">
<img src="brand/logo-lockup.svg" alt="모두의 농장" height="28">
<img src="brand/logo-lockup-white.svg" alt="모두의 농장" height="28">   <!-- 어두운 바탕 -->
```

규칙: 마크의 비율과 조각 배치를 바꾸지 않는다. 기울어진 조각은 노란 하나뿐이다. 최소 높이 24px(마크) / 8mm(인쇄). 색 값은 `docs/brand/kit/tokens/tokens.css`.
