import {createRequire} from 'node:module';
const puppeteer=createRequire('/Users/danielwliu/Dev/projects/2026/blender-to-threejs/package.json')('puppeteer');
const browser=await puppeteer.launch({executablePath:'/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',args:['--ignore-gpu-blocklist','--use-angle=metal','--enable-webgl']});
try {
  const page=await browser.newPage();
  await page.setViewport({width:1440,height:900});
  await page.goto('http://127.0.0.1:8000/pages/seer/dev-seer.html?room=1',{waitUntil:'domcontentloaded'});
  await page.waitForFunction(()=>window.seerReady);
  for(const time of [1.75,2.05,2.35,2.65,3.05,3.6]) {
    await page.waitForFunction(t=>seer._debug().age>=t,{},time);
    await page.screenshot({path:`/tmp/seer-turn-${time}.png`});
  }
  console.log('Captured six transition frames.');
} finally {await browser.close();}
