import {createRequire} from 'node:module';
import assert from 'node:assert/strict';
const p=createRequire('/Users/danielwliu/Dev/projects/2026/blender-to-threejs/package.json')('puppeteer');
const browser=await p.launch({headless:true,executablePath:'/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'});
try{
  const page=await browser.newPage(),errors=[],posts=[];page.on('pageerror',e=>errors.push(e.message));page.on('request',r=>{if(r.method()==='POST')posts.push(r.url());});
  await page.setViewport({width:1440,height:900});await page.goto('http://127.0.0.1:8000/pages/robot.html');
  await page.waitForSelector('#agent-input');await page.type('#agent-input','status');await page.click('#send-agent');
  await page.waitForFunction(()=>!document.querySelector('#send-agent').disabled);
  assert.match(await page.$eval('#chat-messages',e=>e.textContent),/recorded room state|recorded room has changes/);
  await page.click('#new-conversation');await page.type('#agent-input','log');await page.click('#send-agent');await page.waitForFunction(()=>!document.querySelector('#send-agent').disabled);
  assert.match(await page.$eval('#chat-messages',e=>e.textContent),/recent room commits/);
  await page.reload();await page.waitForSelector('#conversation-picker option');assert.equal(await page.$$eval('#conversation-picker option',x=>x.length),2);
  await page.screenshot({path:'/tmp/room-chat-desktop.png'});
  for(const name of ['room-search','room-settings','system-status','agent-chat']){await page.click(`[data-panel="${name}"]`);assert.equal(await page.$eval(`[data-panel="${name}"]`,e=>e.getAttribute('aria-pressed')),'true');}
  await page.setViewport({width:390,height:844});await page.screenshot({path:'/tmp/room-chat-mobile.png'});assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth),390);
  assert(posts.every(url=>url.endsWith('/api/agent/command')));assert.deepEqual(errors,[]);console.log('PASS: live status/log replies, saved conversations, navigation, mobile, only read/plan endpoint used');
}finally{await browser.close();}
