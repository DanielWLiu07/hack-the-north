import { createRequire } from 'node:module';
const puppeteer = createRequire('/Users/danielwliu/Dev/projects/2026/blender-to-threejs/package.json')('puppeteer');
const browser = await puppeteer.launch({ executablePath: '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome', args: ['--ignore-gpu-blocklist', '--use-angle=metal', '--enable-webgl'] });
try {
  const page = await browser.newPage(), errors = [];
  page.on('pageerror', e => errors.push(e.message));
  await page.evaluateOnNewDocument(() => {
    if (location.pathname !== '/robot' || localStorage.getItem('gitirl-room-conversations-v1')) return;
    localStorage.setItem('gitirl-room-conversations-v1', JSON.stringify([{ id: 'layout-preview', title: 'Layout preview', created: new Date().toISOString(), turns: Array.from({ length: 8 }, (_, i) => ({ text: i === 0 ? 'dw' : `Where are my keys? (${i + 1})`, time: new Date().toISOString(), response: { ok: false, error: { message: 'No matching object in this preview conversation.' } } })) }]));
  });
  const results = [];
  for (const [width, height] of [[1440,810],[390,844],[1280,600]]) {
    await page.setViewport({ width, height });
    await page.goto('http://127.0.0.1:8000/robot');
    await page.waitForSelector('.chat-message');
    await new Promise(r => setTimeout(r, 500));
    results.push(await page.evaluate(() => {
      const panel = document.querySelector('.agent-chat').getBoundingClientRect();
      const messages = document.querySelector('#chat-messages');
      const form = document.querySelector('#agent-form').getBoundingClientRect();
      return { viewport: [innerWidth, innerHeight], title: document.querySelector('.chat-eyebrow').textContent, top: panel.top, bottomMargin: innerHeight - panel.bottom, messageHeight: messages.clientHeight, scrollable: messages.scrollHeight > messages.clientHeight, composerVisible: form.bottom <= panel.bottom, youLabels: [...document.querySelectorAll('.chat-message header span')].filter(e => e.textContent === 'YOU').length, savedTestMessages: JSON.parse(localStorage.getItem('gitirl-room-conversations-v1')).flatMap(c => c.turns).filter(t => t.text === 'dw').length, savedTurns: JSON.parse(localStorage.getItem('gitirl-room-conversations-v1'))[0].turns.length };
    }));
    await page.screenshot({ path: `/tmp/agent-panel-${width}.png` });
  }
  await page.setRequestInterception(true);
  page.on('request', request => {
    if (request.url().includes('/api/search?')) request.respond({ status: 200, contentType: 'application/json', body: JSON.stringify({ results: [{ object_id: 'layout-keys', class: 'Keys', present_now: true, last_seen: { zone: 'desk' }, matched_by: { bm25: true } }], provenance: { synthetic_results: 0 } }) });
    else request.continue();
  });
  await page.click('.chat-more > summary');
  await page.click('.agent-search > summary');
  await page.type('#room-q', 'keys');
  await page.click('#room-query button');
  await page.waitForSelector('#room-results .search-result');
  const searchWorks = await page.$eval('#room-results', e => e.textContent.includes('Keys'));
  for (const panel of ['room-settings', 'system-status', 'agent-chat']) {
    await page.click(`[data-panel="${panel}"]`);
    await page.waitForFunction(panel => document.querySelector(`[data-panel="${panel}"]`).getAttribute('aria-pressed') === 'true', {}, panel);
  }
  console.log(JSON.stringify({ results, searchWorks, errors }));
} finally { await browser.close(); }
