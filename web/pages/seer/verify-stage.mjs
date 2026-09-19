import { createRequire } from 'node:module';
import assert from 'node:assert/strict';
const puppeteer = createRequire('/Users/danielwliu/Dev/projects/2026/blender-to-threejs/package.json')('puppeteer');
const browser = await puppeteer.launch({ executablePath: '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
  args: ['--ignore-gpu-blocklist', '--use-angle=metal', '--enable-webgl'] });
const sleep = ms => new Promise(r => setTimeout(r, ms));
const errors = [], results = [];
try {
  for (const mode of ['desktop', 'mobile', 'skip', 'reduced']) {
    const page = await browser.newPage();
    page.on('pageerror', e => errors.push(e.message));
    await page.setViewport({ width: mode === 'desktop' ? 1440 : 390, height: 900 });
    await page.setRequestInterception(true);
    page.on('request', r => r.method() !== 'GET' || /\/api\/telemetry\/sentry\//.test(r.url()) ? r.abort() : r.continue());
    if (mode === 'reduced') await page.emulateMediaFeatures([{ name: 'prefers-reduced-motion', value: 'reduce' }]);
    await page.goto('http://127.0.0.1:8000/telemetry', { waitUntil: 'domcontentloaded', timeout: 60000 });
    if (mode === 'reduced') {
      await page.waitForSelector('[data-seer-scan]');
      assert.equal(await page.evaluate(() => document.body.classList.contains('seer-intro')), false);
    } else {
      await page.waitForSelector('.seer-intro-skip');
      if (mode === 'skip') {
        await page.click('.seer-intro-skip');
      } else {
        await sleep(800);
        await page.screenshot({ path: `/tmp/seer-bughunt-${mode}.png` });
        await sleep(450);
        const bounds = await page.$eval('#stage', e => ({ height: e.getBoundingClientRect().height, top: e.getBoundingClientRect().top }));
        assert(bounds.height > 850 && Math.abs(bounds.top) < 2, JSON.stringify(bounds));
        assert.equal(await page.$eval('#main', e => getComputedStyle(e).visibility), 'hidden');
        assert.equal(await page.$eval('.robotlive', e => getComputedStyle(e).visibility), 'hidden');
        await page.screenshot({ path: `/tmp/seer-fullscreen-${mode}.png` });
      }
      await page.waitForFunction(() => !document.body.classList.contains('seer-intro'));
    }
    await sleep(3000);
    assert((await page.$eval('#stage', e => e.getBoundingClientRect().height)) > 850);
    assert.equal(await page.$eval('#main', e => getComputedStyle(e).visibility), 'visible');
    assert.equal(await page.$eval('.robotlive', e => getComputedStyle(e).visibility), 'visible');
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
    await page.screenshot({ path: `/tmp/seer-reveal-${mode}.png` });
    await page.click('#seer-toggle');
    assert.equal(await page.$eval('#seer-toggle', e => e.getAttribute('aria-pressed')), 'false');
    await page.click('#seer-toggle');
    assert.equal(await page.$eval('#seer-toggle', e => e.getAttribute('aria-pressed')), 'true');
    results.push({ mode, passed: true }); console.log(JSON.stringify(results.at(-1)));
    await page.close();
  }
  assert.deepEqual(errors, []);
  console.log(JSON.stringify({ results, errors }));
} finally { await browser.close(); }
