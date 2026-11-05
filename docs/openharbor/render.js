const { chromium } = require('playwright');
(async () => {
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1280, height: 720 } });
  await page.goto('file://' + process.cwd() + '/deck.html', { waitUntil: 'networkidle' });
  await page.evaluate(() => document.fonts.ready);
  // overflow check
  const over = await page.evaluate(() => Array.from(document.querySelectorAll('.slide')).map((s,i)=>({i:i+1, sh:s.scrollHeight, sw:s.scrollWidth})));
  console.log(JSON.stringify(over));
  await page.pdf({ path: process.argv[2] || 'deck.pdf', width: '1280px', height: '720px', printBackground: true, preferCSSPageSize: true });
  await browser.close();
})();
