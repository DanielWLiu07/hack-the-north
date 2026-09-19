import { createRequire } from 'node:module';
import { readFile } from 'node:fs/promises';
import assert from 'node:assert/strict';
const puppeteer = createRequire('/Users/danielwliu/Dev/projects/2026/blender-to-threejs/package.json')('puppeteer');
const browser = await puppeteer.launch({executablePath:'/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',args:['--ignore-gpu-blocklist','--use-angle=metal','--enable-webgl']});
const script = await readFile(new URL('../telemetry-robot.js', import.meta.url),'utf8');
const fixture = await readFile('/tmp/seer-board-fixture.json','utf8');
const view = {live:true,camera:'cam0',frame_age_s:.1,robot_frame_age_ms:25,target_fps:2,frames:100,polls:110,frame_kb:240,viewers:1,boot_id:'fixture-boot'};
const link = {reachable:true,healthz:{cameras:['cam0'],unavailable:{cam1:'publisher stopped'},preview:{reads:10,served:20,last_age_ms:25},telemetry:{source_errors:3,overruns:2,dropped:1},events:{last_id:'boot:9',dropped:4},frames_clients:1,frames_dropped:5,last_capture:'cap_0005'},watch:{watching:true,sentry_live:true,dry_run:false,healthy:false,checked_s_ago:1,pending:{clock_skew:3},open:{camera_unavailable:'cam1 publisher stopped'},filed:[{kind:'camera_unavailable',level:'error',at:'2026-09-19T12:00:00Z',detail:'<img src=x onerror=alert(1)>'},{kind:'camera_unavailable_recovered',level:'info',at:'2026-09-19T12:01:00Z',detail:'cleared after 60 s'}]}};
const errors=[];
try {
  for (const width of [1440,390]) {
    const page=await browser.newPage();page.on('pageerror',e=>errors.push(e.message));
    await page.setViewport({width,height:900});
    await page.emulateMediaFeatures([{name:'prefers-reduced-motion',value:'reduce'}]);
    await page.setRequestInterception(true);
    page.on('request',r=>{
      const url=new URL(r.url()), path=url.pathname;
      if(url.origin!=='http://localhost:8000'||r.method()!=='GET')return r.abort();
      const json=body=>r.respond({status:200,contentType:'application/json',body:JSON.stringify(body)});
      if(path==='/pages/telemetry-robot.js')return r.respond({status:200,contentType:'text/javascript',body:script});
      if(path==='/api/robot/view/status')return json(view);
      if(path==='/api/robot/link')return json(link);
      if(path==='/api/telemetry/board')return r.respond({status:200,contentType:'application/json',body:fixture});
      if(path==='/api/events')return r.respond({status:200,contentType:'text/event-stream',body:': fixture\n\n'});
      if(path==='/api/robot/view.mjpg')return r.abort();
      if(path.startsWith('/api/'))return json({});
      r.continue();
    });
    await page.goto('http://localhost:8000/telemetry',{waitUntil:'domcontentloaded'});
    await page.waitForSelector('.camera-diagnostics');
    await page.click('.camera-diagnostics > summary');
    await page.waitForFunction(()=>document.querySelector('.camera-diagnostics').textContent.includes('publisher stopped'));
    const text=await page.$eval('.camera-diagnostics',e=>e.textContent);
    assert(text.includes('25 ms old at receipt'));
    assert(text.includes('PENDING · clock_skew'));
    assert(text.includes('enabled on watcher'));
    assert(text.includes('cleared after 60 s'));
    assert.equal(await page.$('.camera-diagnostics img'),null);
    assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
    await page.$eval('.camera-diagnostics',e=>e.scrollIntoView());
    await page.screenshot({path:`/tmp/camera-diagnostics-${width}.png`});
    await page.evaluate(async()=>{
      const {createCameraDiagnostics}=await import('/pages/seer/camera-diagnostics.js');
      const host=document.createElement('div');host.id='diagnostic-test';document.body.append(host);
      const d=createCameraDiagnostics(host);window.testDiagnostics=d;
      host.querySelector('details').open=true;
      d.update({view:{},link:{watch:{watching:true,dry_run:true}},pollErrors:['Camera status unavailable: HTTP 503']});
    });
    await page.waitForFunction(()=>document.querySelector('#diagnostic-test').textContent.includes('HTTP 503'));
    const missing=await page.$eval('#diagnostic-test',e=>e.textContent);
    assert(missing.includes('not reported'));assert(missing.includes('dry run; not sent'));assert(!missing.includes('enabled on watcher'));
    await page.evaluate(()=>{testDiagnostics.dispose();document.querySelector('#diagnostic-test').remove();});
    console.log(JSON.stringify({width,passed:true}));await page.close();
  }
  assert.deepEqual(errors,[]);
} finally { await browser.close(); }
