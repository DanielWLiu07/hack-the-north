import {createRequire} from 'node:module';
import {readFile} from 'node:fs/promises';
import assert from 'node:assert/strict';
import {assetURL} from './spatial-telemetry.js';
assert.equal(assetURL('../outside.glb'),null);assert.equal(assetURL('https://example.com/a.glb'),null);assert.equal(assetURL('cap_1/cam0.glb'),'/live/cap_1/cam0.glb');
const puppeteer=createRequire('/Users/danielwliu/Dev/projects/2026/blender-to-threejs/package.json')('puppeteer');
const fixture=await readFile('/tmp/seer-board-fixture.json','utf8');
const browser=await puppeteer.launch({executablePath:'/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',args:['--ignore-gpu-blocklist','--use-angle=metal','--enable-webgl']});
const errors=[];let missing=false;
try{
 for(const width of [1440,390]){
  const page=await browser.newPage();page.on('pageerror',e=>errors.push(e.message));await page.setViewport({width,height:1000});await page.emulateMediaFeatures([{name:'prefers-reduced-motion',value:'reduce'}]);await page.setRequestInterception(true);
  page.on('request',r=>{const u=new URL(r.url()),p=u.pathname;const json=d=>r.respond({status:200,contentType:'application/json',body:JSON.stringify(d)});
   if(u.origin!=='http://localhost:8000'||r.method()!=='GET')return r.abort();
   if(p==='/api/telemetry/board')return r.respond({status:200,contentType:'application/json',body:fixture});
   if(p.startsWith('/api/telemetry/sentry/'))return json({});
   if(p==='/api/events')return r.respond({status:200,contentType:'text/event-stream',body:': fixture\n\n'});
   if(p==='/api/robot/view/status')return json({live:true,camera:'cam0',frames:10,frame_kb:200,frame_age_s:.1,boot_id:'b'});
   if(p==='/api/robot/link')return json({reachable:true,healthz:{cameras:['cam0']},watch:{watching:false}});
   if(p==='/api/robot/view.mjpg')return r.abort();
   if(p==='/live/latest.json'&&missing)return r.respond({status:503,contentType:'application/json',body:JSON.stringify({detail:'Camera capture unavailable for this check.'})});
   r.continue();
  });
  await page.goto('http://localhost:8000/telemetry',{waitUntil:'domcontentloaded'});await page.waitForSelector('.spatial-tabs');
  assert.equal(await page.$$eval('.spatial-tabs button',a=>a.length),6);
  await page.waitForFunction(()=>document.querySelector('.rl-stage').classList.contains('live'));
  await page.click('.rl-lenses button:nth-child(2)');assert(await page.$eval('.rl-stage',e=>e.classList.contains('right-eye')));
  await page.click('.rl-lenses button:nth-child(3)');assert(await page.$eval('.rl-stage',e=>e.classList.contains('stereo')&&!e.classList.contains('right-eye')));
  for(const mode of ['cloud','voxels','map','captures','data']){
   await page.click(`#perception-tab-${mode}`);
   await page.waitForFunction(()=>!document.querySelector('.spatial-toolbar button:last-child').disabled,{timeout:30000});
   const status=await page.$eval('.spatial-status',e=>e.textContent);console.log(JSON.stringify({width,mode,status}));
   if(['cloud','voxels','map'].includes(mode)){assert(await page.$('.spatial-stage canvas'));assert(!/unavailable|timed out|exceeds/i.test(status),status);await page.click('.spatial-toolbar button:nth-of-type(1)');}
   if(mode==='cloud')assert(status.includes('points'));
   if(mode==='voxels')assert(status.includes('STORED VOXELS'));
   if(mode==='map')assert(status.includes('RECORDED ROOM STATE'));
   if(mode==='captures')assert(await page.$('.spatial-images img'));
   if(mode==='data')assert((await page.$$eval('.spatial-table-wrap tbody tr',a=>a.length))>0);
   assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth), JSON.stringify(await page.evaluate(()=>[...document.querySelectorAll('body *')].filter(e=>e.getBoundingClientRect().right>innerWidth+1).slice(-12).map(e=>({tag:e.tagName,cls:e.className,width:e.getBoundingClientRect().width,right:e.getBoundingClientRect().right})))));
   await page.$eval('.spatial-telemetry',e=>e.scrollIntoView());await new Promise(r=>setTimeout(r,300));
   await page.screenshot({path:`/tmp/telemetry-${mode}-${width}.png`});
  }
  missing=true;await page.click('#perception-tab-cloud');await page.click('.spatial-toolbar button:last-child');await page.waitForFunction(()=>document.querySelector('.spatial-status').textContent.includes('unavailable for this check'));
  missing=false;await page.click('.spatial-toolbar button:last-child');await page.waitForFunction(()=>document.querySelector('.spatial-status').textContent.includes('points'),{timeout:30000});
  await page.click('#perception-tab-camera');assert(await page.$eval('.rl-top',e=>!e.hidden));assert(await page.$eval('.spatial-stage',e=>e.hidden));
  await page.close();
 }
 assert.deepEqual(errors,[]);console.log(JSON.stringify({errors,passed:true}));
}finally{await browser.close();}
