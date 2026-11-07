/* 판넬 PDF·미리보기 생성.
 * 규격(판넬 제작 기준): 완성 1200×1800mm, 작업 크기 1220×1820mm(바탕색 사방 10mm 여유), PDF 제출.
 * 사용: node docs/panel/build.js            → out/비즈니스톤_물개_모두의농장_판넬.pdf (상주시청 간접 인용 카드 포함)
 *       node docs/panel/build.js --omit     → out/…_판넬_상주제외.pdf (카드를 빼고 한 줄 서술)
 * 미리보기 PNG(2440×3640)도 같이 만든다.
 */
const { chromium } = require("playwright");
const path = require("path");
const fs = require("fs");

const MM = 96 / 25.4;                         // CSS px per mm
const W = 1220, H = 1820;                     // 작업 크기(mm)
const variant = process.argv.includes("--omit") ? "omit" : "indirect";
const OUT = path.join(__dirname, "out");
fs.mkdirSync(OUT, { recursive: true });
const base = "비즈니스톤_물개_모두의농장_판넬" + (variant === "omit" ? "_상주제외" : "");
const html = "file://" + path.join(__dirname, "panel.html");

(async () => {
  const browser = await chromium.launch();

  // PDF: 설계 단위 1px=1mm 를 scale(MM)로 키워 1220×1820mm 페이지에 그대로 얹는다
  const page = await browser.newPage({ viewport: { width: Math.round(W * MM), height: Math.round(H * MM) } });
  await page.goto(html, { waitUntil: "networkidle" });
  await page.evaluate((v) => document.documentElement.setAttribute("data-sangju", v), variant);
  await page.evaluate((k) => { document.documentElement.style.setProperty("--k", String(k)); document.body.classList.add("print"); }, MM);
  await page.evaluate(() => document.fonts.ready);
  await page.waitForTimeout(500);
  const pdfPath = path.join(OUT, base + ".pdf");
  await page.pdf({ path: pdfPath, width: `${W}mm`, height: `${H}mm`, printBackground: true, margin: { top: 0, right: 0, bottom: 0, left: 0 }, preferCSSPageSize: false });

  // 미리보기 PNG (재단선 포함)
  const prev = await browser.newPage({ viewport: { width: W, height: H }, deviceScaleFactor: 2 });
  await prev.goto(html, { waitUntil: "networkidle" });
  await prev.evaluate((v) => document.documentElement.setAttribute("data-sangju", v), variant);
  await prev.evaluate(() => document.fonts.ready);
  await prev.waitForTimeout(500);
  const pngPath = path.join(OUT, base + "_미리보기.png");
  await prev.screenshot({ path: pngPath });

  await browser.close();
  const kb = (f) => Math.round(fs.statSync(f).size / 1024);
  console.log(`${pdfPath} (${kb(pdfPath)} KB)\n${pngPath} (${kb(pngPath)} KB)`);
})().catch((e) => { console.error(e); process.exit(1); });
