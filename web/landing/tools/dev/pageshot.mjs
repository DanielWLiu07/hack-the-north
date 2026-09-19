import { createRequire } from 'node:module';
const puppeteer = createRequire('/Users/danielwliu/Dev/projects/2026/blender-to-threejs/package.json')('puppeteer');
const [url, out, w = '1440', h = '900', scrollTo = '', full = ''] = process.argv.slice(2);
const browser = await puppeteer.launch({ executablePath: '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
  args: ['--ignore-gpu-blocklist', '--use-angle=metal', '--enable-webgl'], protocolTimeout: 90000 });
try {
  const page = await browser.newPage();
  await page.setViewport({ width: +w, height: +h, deviceScaleFactor: 1 });
  const errs = [];
  page.on('pageerror', (e) => errs.push('pageerror: ' + e.message.slice(0, 200)));
  page.on('console', (m) => { if (m.type() === 'error') errs.push(m.text().slice(0, 160)); });
  page.on('response', (r) => { if (r.status() >= 400) errs.push(`${r.status()} ${r.url().replace('http://127.0.0.1:8000', '')}`); });
  await page.goto(url, { waitUntil: 'networkidle2', timeout: 40000 }).catch((e) => errs.push('goto: ' + e.message.slice(0, 80)));
  await new Promise((r) => setTimeout(r, 1500));
  if (scrollTo) { await page.evaluate((id) => document.getElementById(id)?.scrollIntoView(), scrollTo); await new Promise((r) => setTimeout(r, 1200)); }
  await page.screenshot({ path: out, fullPage: !!full });
  const info = await page.evaluate(() => ({ h: document.documentElement.scrollHeight, overflowX: document.documentElement.scrollWidth > innerWidth,
    loopRunning: window.gitrl ? undefined : undefined }));
  console.log(JSON.stringify({ info, errs: [...new Set(errs)].slice(0, 10) }));
} finally { await browser.close(); }
