// Browser-only fixture test. Intercepts every request; never starts a server or calls Sentry.
import { createRequire } from 'node:module';
import { readFile } from 'node:fs/promises';
import { dirname, resolve, extname } from 'node:path';
import { fileURLToPath } from 'node:url';
import assert from 'node:assert/strict';
import { bugPosition } from './intro-bugs.js';
const here = dirname(fileURLToPath(import.meta.url)), web = resolve(here,'../..');
const fixture = await readFile('/tmp/seer-board-fixture.json','utf8');
const puppeteer = createRequire('/Users/danielwliu/Dev/projects/2026/blender-to-threejs/package.json')('puppeteer');
const browser = await puppeteer.launch({ executablePath:'/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
  args:['--ignore-gpu-blocklist','--use-angle=metal','--enable-webgl'] });
const sleep = ms => new Promise(r=>setTimeout(r,ms));
const errors = [], results = [];
for (const width of [390,1440]) {
  assert(bugPosition(0,.15,width,900).x < -30);
  assert(bugPosition(1,.35,width,900).x > width+30);
  assert(bugPosition(2,.55,width,900).y > 930);
}
try {
  for (const width of [1440,390]) {
    const page = await browser.newPage(); let ask = null;
    page.on('pageerror',e=>errors.push(e.message));
    await page.setViewport({width,height:900}); await page.setRequestInterception(true);
    page.on('request',async r=>{
      const url = new URL(r.url()), path = url.pathname;
      if (url.origin !== 'http://seer.test') return r.abort();
      if (path === '/api/seer/ask') { ask=r; return; }
      if (path === '/api/telemetry/board') return r.respond({status:200,contentType:'application/json',body:fixture});
      if (path === '/api/events') return r.respond({status:200,contentType:'text/event-stream',body:': offline visual test\n\n'});
      if (path.startsWith('/api/')) return r.respond({status:200,contentType:'application/json',body:'{}'});
      const file = path === '/telemetry' ? resolve(web,'pages/telemetry.html') : resolve(web,path.startsWith('/vendor/') ? `landing${path}` : `.${path}`);
      try {
        const contentType = {'.js':'text/javascript','.html':'text/html','.css':'text/css','.json':'application/json','.woff2':'font/woff2'}[extname(file)] || 'application/octet-stream';
        await r.respond({status:200,contentType,body:await readFile(file)});
      } catch { await r.respond({status:404,body:''}); }
    });
    await page.goto('http://seer.test/telemetry',{waitUntil:'domcontentloaded'});
    await page.waitForSelector('.seer-intro-skip'); await sleep(650);
    await page.screenshot({path:`/tmp/seer-glass-crawl-${width}.png`});
    await page.waitForFunction(()=>!document.body.classList.contains('seer-intro')); await sleep(1300);
    await page.waitForSelector('h1 .seer-wordmark');
    await page.screenshot({path:`/tmp/seer-glass-${width}.png`});
    const layout = await page.evaluate(()=>({overflow:document.documentElement.scrollWidth>innerWidth,
      left:document.querySelector('#main').getBoundingClientRect().left,
      width:document.querySelector('#main').getBoundingClientRect().width,
      radius:getComputedStyle(document.querySelector('#main section')).borderRadius}));
    assert(!layout.overflow,JSON.stringify(layout)); assert(layout.width>width*.75); assert(parseFloat(layout.radius)>10);
    const headingTop=await page.$eval('.bandtext',e=>e.getBoundingClientRect().top);
    await page.evaluate(()=>scrollTo(0,300));await sleep(100);
    const scrolled=await page.evaluate(()=>({y:scrollY,top:document.querySelector('.bandtext').getBoundingClientRect().top}));
    assert(Math.abs(headingTop-scrolled.top-scrolled.y)<1,'Heading must scroll with the document');
    await page.screenshot({path:`/tmp/seer-scroll-${width}.png`});
    await page.evaluate(()=>scrollTo(0,0));
    await page.click('.fbtn.ask');
    await page.waitForFunction(()=>document.body.dataset.seerState==='thinking');
    assert(ask); await sleep(500);
    await page.screenshot({path:`/tmp/seer-glass-thinking-${width}.png`});
    await ask.respond({status:200,contentType:'application/json',body:JSON.stringify({state:'stumped',reason:'Offline visual test — no agent request was sent.'})}); ask=null;
    await page.waitForFunction(()=>document.body.dataset.seerState==='stumped');
    assert.equal(await page.$eval('.seer-state',e=>e.textContent),'No answer');
    await page.click('.fbtn.ask'); await page.waitForFunction(()=>document.body.dataset.seerState==='thinking');
    await ask.respond({status:200,contentType:'application/json',body:JSON.stringify({state:'verdict',verdict:'Offline visual test response.'})}); ask=null;
    await page.waitForFunction(()=>document.body.dataset.seerState==='verdict');
    assert.equal(await page.$eval('.seer-state',e=>e.textContent),'Answer ready');
    await page.goto('http://seer.test/pages/seer/dev-seer.html?room=1',{waitUntil:'domcontentloaded'});
    await page.waitForFunction(()=>window.seerReady);
    const rig = await page.evaluate(async()=>{
      const out={frames:0,maxGap:0,minScale:Infinity,maxScale:0,idleBeams:0,maxIntroShapes:0};
      await new Promise(resolve=>{
        const sample=()=>{ const d=seer._debug();out.frames++;out.maxGap=Math.max(out.maxGap,d.attachError||0,d.rootError||0);
          out.minScale=Math.min(out.minScale,d.S);out.maxScale=Math.max(out.maxScale,d.S);
          if(d.scan)out.idleBeams++;out.maxIntroShapes=Math.max(out.maxIntroShapes,d.introShapes||0);
          if(d.age<4.2||d.entrance<6.4) requestAnimationFrame(sample);else {out.roll=d.roll;out.decorativeAssets=d.decorativeAssets;resolve();} };
        requestAnimationFrame(sample);
      });
      return out;
    });
    assert(rig.maxGap<.05 && Math.abs(rig.roll+Math.PI/2)<.1 && rig.maxScale-rig.minScale<.001 && rig.idleBeams===0 && rig.maxIntroShapes===24 && rig.decorativeAssets===0,JSON.stringify(rig));
    await page.mouse.move(width*.8,80);await sleep(500);const up=await page.evaluate(()=>seer._debug().iris[0]);
    await page.mouse.move(width*.8,820);await sleep(500);const down=await page.evaluate(()=>seer._debug().iris[0]);
    assert(down>up+5,JSON.stringify({up,down}));
    // Cover the old nine-second idle laser cycle explicitly.
    await page.waitForFunction(()=>seer._debug().age>11.4);
    const idle = await page.evaluate(async()=>{let beams=0;const end=seer._debug().age+1.8;
      await new Promise(resolve=>{const tick=()=>{if(seer._debug().scan)beams++;if(seer._debug().age<end)requestAnimationFrame(tick);else resolve();};tick();});return beams;});
    assert.equal(idle,0);
    await page.evaluate(()=>seer.pause());const before=await page.evaluate(()=>seer._debug().frames);await sleep(150);
    assert.equal(await page.evaluate(()=>seer._debug().frames),before);
    await page.evaluate(()=>seer.resume());await sleep(150);assert(await page.evaluate(()=>seer._debug().frames)>before);
    results.push({width,layout,rig,states:['thinking','stumped','verdict'],mockedAgentRequests:2});
    console.log(JSON.stringify(results.at(-1))); await page.close();
  }
  assert.deepEqual(errors,[]); console.log(JSON.stringify({errors,results,crawlStartsOutsideViewport:true}));
} finally { await browser.close(); }
