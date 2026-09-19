import { createRequire } from 'node:module';
const puppeteer = createRequire('/Users/danielwliu/Dev/projects/2026/blender-to-threejs/package.json')('puppeteer');
const browser = await puppeteer.launch({ executablePath: '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome', args: ['--ignore-gpu-blocklist', '--use-angle=metal', '--enable-webgl'] });
try {
  const page = await browser.newPage();const errors=[];page.on('pageerror',e=>errors.push(e.message));
  await page.setViewport({width:768,height:768,deviceScaleFactor:1});
  await page.goto('http://127.0.0.1:8000/dev-room-decor.html');
  await page.waitForFunction(()=>window.ready,{timeout:60000});
  await page.screenshot({path:'models/room-decor/camera-ink.png',omitBackground:true});
  console.log(JSON.stringify({triangles:await page.evaluate(()=>window.decor.triangles),errors}));
} finally { await browser.close(); }
