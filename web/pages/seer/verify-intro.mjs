import { createRequire } from 'node:module';
import assert from 'node:assert/strict';
const puppeteer = createRequire('/Users/danielwliu/Dev/projects/2026/blender-to-threejs/package.json')('puppeteer');
const browser = await puppeteer.launch({ executablePath: '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
  args: ['--ignore-gpu-blocklist', '--use-angle=metal', '--enable-webgl'] });
const sleep = ms => new Promise(r => setTimeout(r, ms));
try {
  const page = await browser.newPage(), errors = [], requests = [];
  page.on('pageerror', e => errors.push(e.message));
  // Never let a visual audit start paid/debugging work, including host auto-summon.
  await page.setRequestInterception(true);
  page.on('request', r => {
    if (r.method() !== 'GET' || /\/api\/telemetry\/sentry\//.test(r.url())) { requests.push(r.url()); r.abort(); }
    else r.continue();
  });
  const results = [];
  for (const width of [1440, 390]) {
    await page.setViewport({ width, height: 900 });
    await page.goto('http://127.0.0.1:8000/pages/seer/dev-seer.html');
    await page.waitForFunction(() => window.seerReady);
    await page.evaluate(() => {
      window.audit = { frames: 0, maxGap: 0, scans: 0, invalid: 0 };
      const sample = () => {
        if (!window.seer) return;
        const d = seer._debug(); audit.frames++;
        audit.maxGap = Math.max(audit.maxGap, d.attachError || 0, d.rootError || 0);
        if (d.hands.length !== 8 || d.hands.some(h => !h || h.some(v => !Number.isFinite(v)))) audit.invalid++;
        if (d.scan) audit.scans++;
        requestAnimationFrame(sample);
      }; requestAnimationFrame(sample);
    });
    for (const time of [.35, 1.1, 2.2, 3.75, 5.3]) {
      await page.waitForFunction(t => seer._debug().age >= t, {}, time);
      await page.screenshot({ path: `/tmp/seer-intro-${width}-${time}.png` });
    }
    const audit = await page.evaluate(() => window.audit);
    assert.equal(await page.evaluate(() => seer._debug().propsVisible), false);
    assert(audit.maxGap < .05 && !audit.invalid && audit.scans === 0, JSON.stringify(audit));
    await page.evaluate(() => { seer.react('thinking', document.querySelector('[data-seer-target]')); });
    await page.waitForFunction(() => seer._debug().scan?.strength > .2);
    await page.evaluate(() => { document.querySelector('[data-seer-target]').style.transform = 'translateX(17px)'; scrollTo(0, 45); });
    await sleep(100);
    const track = await page.evaluate(() => {
      const d = seer._debug(), r = document.querySelector('[data-seer-target]').getBoundingClientRect();
      return { scan: d.scan, x: r.left + 24, y: Math.max(16, r.top + 12) };
    });
    assert(track.scan && Math.abs(track.scan.end.x - track.x) < 1 && Math.abs(track.scan.end.y - track.y) < 1);
    await page.evaluate(() => seer.pause());
    const frames = await page.evaluate(() => seer._debug().frames);
    await sleep(180);
    assert.equal(await page.evaluate(() => seer._debug().frames), frames);
    assert.equal(await page.evaluate(() => seer._debug().scan), null);
    await page.evaluate(() => seer.resume()); await sleep(180);
    assert(await page.evaluate(() => seer._debug().frames) > frames);
    await page.evaluate(() => { document.body.style.minHeight = '2400px'; scrollTo(0, 1200); });
    await sleep(200);
    const offscreen = await page.evaluate(() => seer._debug().frames);
    await sleep(200);
    assert.equal(await page.evaluate(() => seer._debug().frames), offscreen);
    assert.equal(await page.evaluate(() => seer._debug().scan), null);
    await page.evaluate(() => scrollTo(0, 0)); await sleep(200);
    assert(await page.evaluate(() => seer._debug().frames) > offscreen);
    const background = await browser.newPage(); await background.bringToFront(); await sleep(200);
    const hidden = await page.evaluate(() => ({ hidden: document.hidden, frames: seer._debug().frames }));
    assert(hidden.hidden); await sleep(200);
    assert.equal(await page.evaluate(() => seer._debug().frames), hidden.frames);
    await page.bringToFront(); await background.close(); await sleep(200);
    assert(await page.evaluate(() => seer._debug().frames) > hidden.frames);
    await page.evaluate(() => { seer.react('stumped'); }); await sleep(300);
    assert.equal(await page.evaluate(() => seer._debug().scan), null);
    await page.emulateMediaFeatures([{ name: 'prefers-reduced-motion', value: 'reduce' }]);
    await page.evaluate(() => seer.react('idle')); await sleep(300);
    assert.equal(await page.evaluate(() => seer._debug().entrance), 6.4);
    assert.equal(await page.evaluate(() => seer._debug().scan), null);
    await page.evaluate(() => seer.dispose());
    assert.equal(await page.$('[data-seer-scan]'), null);
    await page.emulateMediaFeatures([{ name: 'prefers-reduced-motion', value: 'no-preference' }]);
    await page.goto('http://127.0.0.1:8000/telemetry', { waitUntil: 'domcontentloaded' });
    await page.waitForSelector('.seer-intro');
    await sleep(1800);
    const fullscreen = await page.$eval('#stage', el => ({ height: el.getBoundingClientRect().height, top: el.getBoundingClientRect().top }));
    assert(fullscreen.height > 850 && Math.abs(fullscreen.top) < 2, JSON.stringify(fullscreen));
    await page.screenshot({ path: `/tmp/seer-fullscreen-${width}.png` });
    await page.waitForFunction(() => !document.body.classList.contains('seer-intro'));
    await sleep(1600);
    await page.screenshot({ path: `/tmp/seer-intro-page-${width}.png` });
    await page.click('#seer-toggle');
    assert.equal(await page.$eval('#seer-toggle', el => el.getAttribute('aria-pressed')), 'false');
    assert.equal(await page.$eval('[data-seer-scan]', el => el.style.display), 'none');
    await page.click('#seer-toggle'); await sleep(250);
    assert.equal(await page.$eval('#seer-toggle', el => el.getAttribute('aria-pressed')), 'true');
    await page.goto('http://127.0.0.1:8000/?info', { waitUntil: 'domcontentloaded' });
    await page.goBack({ waitUntil: 'domcontentloaded' });
    await page.waitForFunction(() => !document.body.classList.contains('seer-intro')); await sleep(800);
    assert.equal(new URL(page.url()).pathname, '/telemetry');
    assert.equal(await page.$$eval('[data-seer-scan]', els => els.length), 1);
    results.push({ width, ...audit, tracking: true, pauseResume: true, offscreenReturn: true,
      hiddenTabReturn: true, navigationReturn: true, toggle: true, reducedMotion: true, dispose: true });
  }
  assert.deepEqual(errors, []);
  console.log(JSON.stringify({ results, errors, blockedHostRequests: requests }, null, 2));
} finally { await browser.close(); }
