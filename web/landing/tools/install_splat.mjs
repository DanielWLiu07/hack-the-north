// node tools/install_splat.mjs [trained.ply] [--raw]
//
// Installs the trained Bracket Bot splat for the landing: packs OpenSplat's .ply into
// models/robot_splat.ksplat and points models/robot_splat.json at it. dev-splat.html
// reloads by itself a few seconds later; splat.js needs no edit.
//
// WHY PACK. OpenSplat writes 62 floats a gaussian (248 bytes: three SH bands of
// view-dependent colour). The landing draws it through a near-binary ink pass at SH degree 0,
// so almost all of that is dead weight on the wire. .ksplat at SH 0 / 16-bit is ~10x smaller,
// and it is the renderer's native format, so it also skips the parse on load.
// The packing is done by the SAME vendored library the page uses, inside headless Chrome
// (it imports 'three' through the page's import map, which node cannot resolve).
// --raw copies the .ply untouched instead (debugging: rules the packer out).
//
// Needs the dev server: python3 serve.py (127.0.0.1:8124, from web/landing).
import { createRequire } from 'node:module';
import { copyFileSync, readFileSync, writeFileSync, unlinkSync, openSync, readSync, closeSync, statSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const DEFAULT_SRC = process.env.SPLAT_SRC || 'robot_splat.ply';   // or pass the .ply path as the first argument
const args = process.argv.slice(2), raw = args.includes('--raw');
const src = resolve(args.find((a) => !a.startsWith('--')) || DEFAULT_SRC);
const landing = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const models = resolve(landing, 'models'), manifestPath = resolve(models, 'robot_splat.json');

// is it really a 3DGS .ply?
const fd = openSync(src, 'r'), headBuf = Buffer.alloc(8192); readSync(fd, headBuf, 0, 8192, 0); closeSync(fd);
const head = headBuf.toString('latin1'), count = +(/element vertex (\d+)/.exec(head) || [])[1];
for (const f of ['f_dc_0', 'opacity', 'scale_0', 'rot_0']) {
  if (!head.includes(`property float ${f}`)) { console.error(`${src}: no "${f}" property: not a 3DGS .ply`); process.exit(1); }
}
const shBands = (head.match(/f_rest_/g) || []).length, canonical = /^comment canonical_robot_frame/m.test(head);
console.log(`${src}\n  ${count.toLocaleString()} gaussians, ${shBands} f_rest fields, ${(statSync(src).size / 1e6).toFixed(1)} MB`);

const setManifest = (file) => {
  const m = JSON.parse(readFileSync(manifestPath, 'utf8')); m.file = file;
  // bake.py marks a .ply it has already put in the landing frame (feet y = 0, mast on x = z = 0,
  // facing +Z, 1.0 tall); splat.js then only scales it, and `orient` is left for unbaked files
  m.canonical = canonical;
  writeFileSync(manifestPath, JSON.stringify(m, null, 2) + '\n');
  console.log(`  models/robot_splat.json -> file: ${JSON.stringify(file)}, canonical: ${canonical}`);
};

if (raw) {
  copyFileSync(src, resolve(models, 'robot_splat.ply'));
  setManifest('robot_splat.ply');
  process.exit(0);
}

const incoming = resolve(models, '.incoming.ply');
copyFileSync(src, incoming);
const puppeteer = createRequire('/Users/danielwliu/Dev/projects/2026/blender-to-threejs/package.json')('puppeteer');
const browser = await puppeteer.launch({ executablePath: '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome', protocolTimeout: 600000 });
try {
  const page = await browser.newPage();
  page.on('pageerror', (e) => console.error('pageerror:', e.message));
  await page.goto('http://127.0.0.1:8124/tools/pack.html', { waitUntil: 'domcontentloaded' }).catch(() => {
    throw new Error('dev server not reachable: run `python3 serve.py` in web/landing first');
  });
  await page.waitForFunction(() => window.pack, { timeout: 30000 });
  const info = await page.evaluate(() => window.pack('../models/.incoming.ply'));
  const out = resolve(models, 'robot_splat.ksplat'), parts = [];
  for (let i = 0; i < info.chunks; i++) parts.push(Buffer.from(await page.evaluate((k) => window.chunk(k), i), 'base64'));
  writeFileSync(out, Buffer.concat(parts));
  console.log(`  kept ${info.kept.toLocaleString()} of ${info.total.toLocaleString()} gaussians -> models/robot_splat.ksplat, ${(statSync(out).size / 1e6).toFixed(1)} MB`);
  setManifest('robot_splat.ksplat');
} finally {
  await browser.close();
  unlinkSync(incoming);
}
