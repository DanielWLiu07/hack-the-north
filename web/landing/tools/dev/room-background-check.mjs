import { createRequire } from 'node:module';
const puppeteer=createRequire('/Users/danielwliu/Dev/projects/2026/blender-to-threejs/package.json')('puppeteer');
const browser=await puppeteer.launch({executablePath:'/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',args:['--ignore-gpu-blocklist','--use-angle=metal','--enable-webgl']});
try {
 const page=await browser.newPage(),errors=[];page.on('pageerror',e=>errors.push(e.message));
 await page.setViewport({width:1440,height:810});
 await page.goto('http://127.0.0.1:8000/robot');
 await page.waitForFunction(()=>window.roomVoxels?.state.count>0,{timeout:60000});
 await new Promise(r=>setTimeout(r,3500));
 await page.screenshot({path:'/tmp/room-background-desktop.png'});
 const frameStart=await page.evaluate(()=>window.roomFrame?.frames);
 await new Promise(r=>setTimeout(r,400));
 const idleFrames=await page.evaluate(start=>window.roomFrame.frames-start,frameStart);
 const layout=await page.evaluate(()=>{
   const v=document.querySelector('.viewer').getBoundingClientRect(),p=document.querySelector('.agent-chat').getBoundingClientRect();
   const x=v.left+v.width/2,y=v.top+v.height/2;
   return {leftMargin:v.left,noDecorCanvas:!document.querySelector('.room-decoration'),chatSeparate:v.right<=p.left,hit:document.elementFromPoint(x,y).tagName,center:{x,y},initial:window.roomVoxels?.state.camera||window.roomPreview.state.yaw};
 });
 await page.mouse.move(layout.center.x,layout.center.y);await page.mouse.down();await page.mouse.move(layout.center.x+80,layout.center.y+25,{steps:12});await page.mouse.up();
 await new Promise(r=>setTimeout(r,700));
 const after=await page.evaluate(()=>({camera:window.roomVoxels?.state.camera||window.roomPreview.state.yaw}));
 await page.click('#agent-input');await page.keyboard.type('Layout check');
 const typing=await page.$eval('#agent-input',e=>e.value==='Layout check');
 await page.click('[data-panel="room-settings"]');await page.waitForFunction(()=>document.querySelector('#room-settings').open);
 await page.click('[data-panel="agent-chat"]');
 await page.setViewport({width:390,height:844});await new Promise(r=>setTimeout(r,300));
 const mobile=await page.evaluate(()=>({chatVisible:document.querySelector('.agent-chat').getBoundingClientRect().right<=innerWidth}));
 await page.screenshot({path:'/tmp/room-background-mobile.png'});
 console.log(JSON.stringify({layout,idleFrames,roomDragWorks:JSON.stringify(layout.initial)!==JSON.stringify(after.camera),typing,mobile,errors}));
}finally{await browser.close();}
