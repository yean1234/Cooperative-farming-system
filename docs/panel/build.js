// 판넬 PDF 빌드: panel.html + assets/ → 비즈니스톤_물개_모두의농장_판넬.pdf, preview.png
// 사용: node docs/panel/build.js   (Playwright와 Chromium, pdftoppm이 필요하다)
const fs = require("fs");
const os = require("os");
const path = require("path");
const { execSync } = require("child_process");
const { chromium } = require("playwright");

const here = __dirname;
const outPdf = path.join(here, "비즈니스톤_물개_모두의농장_판넬.pdf");
const outPng = path.join(here, "preview.png");

(async () => {
  let html = fs.readFileSync(path.join(here, "panel.html"), "utf8");
  // 이미지 경로를 절대 경로로 바꿔 어느 위치에서 열어도 같은 결과가 나오게 한다
  html = html.replace(/src="assets\//g, `src="file://${path.join(here, "assets")}/`);
  const tmp = path.join(fs.mkdtempSync(path.join(os.tmpdir(), "panel-")), "panel.built.html");
  fs.writeFileSync(tmp, html);

  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1600, height: 2400 } });
  page.on("console", (m) => { if (m.type() === "error") console.log("console:", m.text()); });
  await page.goto("file://" + tmp, { waitUntil: "load" });
  await page.evaluate(() => document.fonts.ready);
  await page.waitForTimeout(1500);
  // 글꼴이 실제로 Pretendard로 잡혔는지 확인
  const font = await page.evaluate(() => {
    const el = document.querySelector("h1");
    return { ok: document.fonts.check('900 10px "Wanted Sans"'), family: getComputedStyle(el).fontFamily };
  });
  console.log("font:", JSON.stringify(font));
  // 넘침 검사: 섹션 밖으로 나간 요소가 있으면 알린다
  const overflow = await page.evaluate(() => {
    const bad = [];
    document.querySelectorAll("section").forEach((s) => {
      const r = s.getBoundingClientRect();
      s.querySelectorAll("*").forEach((el) => {
        if (el.closest("svg")) return;
        const b = el.getBoundingClientRect();
        if (b.width === 0 || b.height === 0) return;
        if (b.bottom > r.bottom + 0.5 || b.right > r.right + 0.5) bad.push(`${s.className} > ${el.tagName.toLowerCase()}.${el.className} bottom ${Math.round(b.bottom - r.bottom)} right ${Math.round(b.right - r.right)}`);
      });
    });
    return bad.slice(0, 20);
  });
  console.log("overflow:", overflow.length ? overflow : "none");

  await page.pdf({ path: outPdf, width: "1220mm", height: "1820mm", printBackground: true, preferCSSPageSize: true, margin: { top: 0, right: 0, bottom: 0, left: 0 } });
  await browser.close();

  // 미리보기 PNG (약 30dpi → 1440px 너비)
  execSync(`pdftoppm -png -r 30 -singlefile "${outPdf}" "${outPng.replace(/\.png$/, "")}"`);
  const info = execSync(`pdfinfo "${outPdf}"`).toString();
  console.log(info.split("\n").filter((l) => /Pages|Page size|File size/.test(l)).join("\n"));
  console.log("wrote", outPdf, "and", outPng);
})().catch((e) => { console.error(e); process.exit(1); });
