#!/usr/bin/env node
// Requires a host-installed Playwright. No browser binaries or credentials bundled.
const {chromium} = require('playwright');
const fs = require('node:fs');
const path = require('node:path');
const {pathToFileURL} = require('node:url');
const {createHash} = require('node:crypto');
const assert = require('node:assert/strict');

(async () => {
  if (!process.argv[2]) {
    console.error('usage: browser_qa.cjs <论文短名>.html [output-dir]');
    process.exit(2);
  }
  const input = path.resolve(process.argv[2]);
  const output = path.resolve(process.argv[3] || 'reader-qa');
  fs.mkdirSync(output, {recursive: true});
  const candidates = process.platform === 'darwin' ? [
    '/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge',
    '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'
  ] : [];
  const executablePath = process.env.READER_BROWSER_EXECUTABLE || candidates.find(file => fs.existsSync(file));
  const browser = await chromium.launch({headless: true, ...(executablePath ? {executablePath} : {})});
  const report = {ok: false, checks: [], requests: [], errors: []};
  try {
    const page = await browser.newPage({viewport: {width: 1440, height: 1080}, acceptDownloads: true});
    page.on('request', request => {
      if (/^https?:/.test(request.url())) report.requests.push(request.url());
    });
    page.on('pageerror', error => report.errors.push(error.message));
    await page.goto(pathToFileURL(input).href, {waitUntil: 'networkidle'});
    await page.evaluate(() => document.fonts.ready);
    const rowCount = await page.locator('.reading-row').count();
    assert.ok(rowCount > 0);
    const mark = page.locator('mark[data-kind]').first();
    if (await mark.count()) {
      const kind = await mark.getAttribute('data-kind');
      await page.locator(`.legend-button[data-kind="${kind}"]`).click();
      assert.ok(await mark.evaluate(node => node.classList.contains('dimmed')));
      await page.locator(`.legend-button[data-kind="${kind}"]`).click();
      report.checks.push('highlight legend toggle');
    }
    const term = await page.locator('.translation p').first().textContent();
    await page.locator('#reader-search').fill(term.trim().slice(0, 5));
    const visible = await page.locator('.reading-row:visible').count();
    assert.ok(visible > 0 && visible <= rowCount);
    await page.locator('#reader-search').fill('NO_MATCH_READER_QA_91c03');
    assert.equal(await page.locator('.reading-row:visible').count(), 0);
    await page.locator('#reader-search').fill('');
    assert.equal(await page.locator('.reading-row:visible').count(), rowCount);
    report.checks.push('Chinese search, zero results, reset');
    await page.locator('#reading-mode').selectOption('zh');
    assert.equal(await page.locator('.english:visible').count(), 0);
    await page.locator('#reading-mode').selectOption('en');
    assert.equal(await page.locator('.translation:visible').count(), 0);
    await page.locator('#reading-mode').selectOption('bi');
    report.checks.push('three language modes');
    await page.locator('.section-links a[href="#overview"]').click();
    await page.waitForFunction(() => location.hash === '#overview');
    await page.evaluate(() => document.documentElement.style.scrollBehavior = 'auto');
    await page.locator('#overview').scrollIntoViewIfNeeded();
    await page.screenshot({path: path.join(output, 'argument-map.png')});
    report.checks.push('argument overview navigation');
    if (await page.locator('#pdf-data').count()) {
      const expected = await page.locator('#reader-manifest').evaluate(node => JSON.parse(node.textContent).pdf_sha256);
      const promise = page.waitForEvent('download');
      await page.locator('#download-pdf').click();
      const download = await promise;
      const saved = path.join(output, 'downloaded-original.pdf');
      await download.saveAs(saved);
      assert.equal(createHash('sha256').update(fs.readFileSync(saved)).digest('hex'), expected);
      report.checks.push('PDF download SHA-256');
    }
    for (const width of [1440, 900, 768, 390]) {
      await page.setViewportSize({width, height: width < 500 ? 1000 : 1080});
      await page.evaluate(() => scrollTo(0, 0));
      await page.waitForFunction(() => scrollY === 0);
      await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
      const layout = await page.evaluate(() => ({
        overflow: document.documentElement.scrollWidth > innerWidth + 1,
        columns: getComputedStyle(document.querySelector('.reading-row')).display,
        nav: getComputedStyle(document.querySelector('.section-nav')).position,
        translation: getComputedStyle(document.querySelector('.translation')).backgroundColor,
      }));
      assert.equal(layout.overflow, false, `document overflow at ${width}px`);
      assert.equal(layout.nav, 'sticky');
      assert.equal(layout.columns, width <= 760 ? 'block' : 'grid');
      assert.equal(layout.translation, 'rgb(240, 236, 226)');
      if (width === 1440 || width === 390) {
        await page.screenshot({path: path.join(output, `reader-${width}.png`), animations: 'disabled'});
      }
      report.checks.push(`responsive ${width}px`);
    }
    assert.deepEqual(report.requests, []);
    assert.deepEqual(report.errors, []);
    report.checks.push('zero HTTP(S) load requests and no JS errors');
    report.ok = true;
  } finally {
    fs.writeFileSync(path.join(output, 'browser-qa.json'), JSON.stringify(report, null, 2) + '\n');
    await browser.close();
  }
  console.log(JSON.stringify(report, null, 2));
})().catch(error => { console.error(error); process.exitCode = 1; });
