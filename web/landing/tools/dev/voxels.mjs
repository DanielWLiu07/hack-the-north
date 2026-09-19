import { createRequire } from 'node:module';
import assert from 'node:assert/strict';
const p=createRequire('/Users/danielwliu/Dev/projects/2026/blender-to-threejs/package.json')('puppeteer');
const browser=await p.launch({headless:true,executablePath:'/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'});
try{
  const page=await browser.newPage(),errors=[];
  page.on('pageerror',e=>errors.push(e.message));
  await page.setViewport({width:1440,height:810});
  if(process.argv.includes('--mock')){
    await page.setRequestInterception(true);
    page.on('request',r=>{
      if(!r.url().includes('/api/voxels?'))return r.continue();
      const cells=[];for(let x=0;x<25;x++)for(let y=0;y<20;y++)cells.push({center:[x*.05,y*.05,(x>10&&x<15&&y>8&&y<13)?.3:0],size:.05,object_id:x>10&&x<15?'test-object':'test-floor'});
      r.respond({status:200,contentType:'application/json',body:JSON.stringify({cells,total:500,commit_sha:'test12345',source:'test',provenance:{kind:'test'}})});
    });
  }
  await page.goto('http://127.0.0.1:8000/pages/robot.html');
  await page.waitForFunction(()=>!!window.roomVoxels);
  assert.equal(await page.evaluate(()=>roomVoxels.state.enabled),false);
  assert.equal(await page.evaluate(()=>roomVoxels.state.count),0);
  await page.click('[data-panel="room-settings"]');
  await page.click('#enable-voxels');
  await page.waitForFunction(()=>window.roomVoxels?.state.count>0);
  await new Promise(r=>setTimeout(r,600));
  console.log(await page.evaluate(()=>({state:roomVoxels.state,status:document.querySelector('#voxel-status').textContent})));
  await page.screenshot({path:'/tmp/voxels-desktop.png'});
  const before=await page.evaluate(()=>roomVoxels.state.frameCount);
  await new Promise(r=>setTimeout(r,400));
  assert.equal(await page.evaluate(()=>roomVoxels.state.frameCount),before,'renderer must settle at idle');
  await page.click('#top');await new Promise(r=>setTimeout(r,400));
  await page.setViewport({width:390,height:844});
  await new Promise(r=>setTimeout(r,400));await page.screenshot({path:'/tmp/voxels-mobile.png'});
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth),390);
  assert.deepEqual(errors,[]);console.log('PASS: render, idle pause, top view, mobile, no page errors');
}finally{await browser.close();}
