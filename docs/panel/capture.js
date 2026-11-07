/* 판넬에 넣을 시제품 화면 캡처. 서버(python -m server)가 127.0.0.1:8000 에 떠 있어야 한다.
 * 사용: node docs/panel/capture.js
 * 출력: docs/panel/assets/shot-*.png (2배 해상도)
 */
const { chromium } = require("playwright");
const path = require("path");
const OUT = path.join(__dirname, "assets");
const BASE = process.env.BASE || "http://127.0.0.1:8000";

(async () => {
  const browser = await chromium.launch();
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 }, deviceScaleFactor: 2, locale: "ko-KR" });
  const page = await ctx.newPage();

  // ① 후보단지 찾기 (실측 지도 자동 로드)
  await page.goto(`${BASE}/`, { waitUntil: "networkidle" });
  await page.waitForFunction(() => document.querySelectorAll("#rank table tr").length > 3, null, { timeout: 120000 });
  await page.waitForTimeout(1500);
  await page.screenshot({ path: path.join(OUT, "shot-1-sites.png"), fullPage: false });

  // 빈 필드 시뮬레이터
  await page.goto(`${BASE}/empty-field.html`, { waitUntil: "networkidle" });
  await page.waitForFunction(() => document.querySelectorAll("#map [data-id]").length > 100);
  await page.evaluate(() => { document.querySelector(".howto").open = false; });
  await page.waitForTimeout(500);

  // 단지 안 농가를 면적 순으로 고른다(가운데 농가부터)
  const farms = await page.evaluate(() => {
    const list = [...F.farms.values()].filter((f) => f.member);
    const mx = F.meta.width / 2, my = F.meta.height / 2;
    const d = (f) => { const p = F.parcels[f.parcels[0]]; return Math.hypot(p.c[0] - mx, p.c[1] - my); };
    return list.sort((a, b) => d(a) - d(b)).map((f) => ({ name: f.name, first: f.parcels[0], area: f.area }));
  });
  const click = async (pid) => { await page.locator(`#map [data-id="${pid}"]`).first().dispatchEvent("click"); await page.waitForTimeout(400); };

  // ② 거절 농가 1곳 → 구멍·기준 충족
  await click(farms[0].first);
  await page.screenshot({ path: path.join(OUT, "shot-2-refuse.png") });

  // ③ 대체 필지 추천 탭(오른쪽 열)
  await page.locator("#tab1").click();
  await page.locator(".col.right").screenshot({ path: path.join(OUT, "shot-3-alternatives.png") });

  // ④ 배당금·흉년 탭
  await page.locator("#tab3").click();
  await page.waitForTimeout(300);
  await page.locator(".col.right").screenshot({ path: path.join(OUT, "shot-4-payout.png") });
  await page.locator("#tab1").click();

  // 히어로: 거절 3농가 → 기준 미충족 상태
  await click(farms[1].first); await click(farms[2].first);
  await page.waitForTimeout(500);
  await page.screenshot({ path: path.join(OUT, "shot-hero.png") });
  await page.locator(".col.map").screenshot({ path: path.join(OUT, "shot-hero-map.png") });
  await page.locator("#decide").screenshot({ path: path.join(OUT, "shot-hero-decide.png") });

  // ⑤ 설명 자료(대표 보고용·농가 설명용)
  await page.locator("#btnSheet").click();
  await page.waitForTimeout(400);
  await page.evaluate(() => { document.querySelector("#sheetDlg .dlg-head").style.display = "none"; document.querySelector("#sheetDlg").scrollTop = 0; });
  await page.locator("#sheet").screenshot({ path: path.join(OUT, "shot-5-sheet-boss.png") });
  await page.locator('#segSheet [data-v="farm"]').dispatchEvent("click");
  await page.waitForTimeout(400);
  await page.locator("#sheet").screenshot({ path: path.join(OUT, "shot-5-sheet-farm.png") });

  await browser.close();
  console.log("captured ->", OUT);
})().catch((e) => { console.error(e); process.exit(1); });
