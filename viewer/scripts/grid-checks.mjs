import assert from 'node:assert/strict';
import {readFileSync,mkdirSync} from 'node:fs';
import {openRefHost} from './ref-host.mjs';
import {fakeMcp,loadFixture} from './fake-mcp.mjs';
const fixture=loadFixture('tests/fixtures/bracket');
const html=readFileSync('dist/aad-viewer.html','utf8');
mkdirSync('test-output',{recursive:true});
for(const width of [390,900])for(const ortho of [false,true])for(const theme of ['dark','light']){
 const mcp=fakeMcp(fixture);mcp.toolResult.structuredContent.viewer={mode:'inline',presentation:{grid:true,projection:ortho?'orthographic':'perspective'}};
 const host=await openRefHost({html,toolResult:mcp.toolResult,onRequest:mcp.onRequest,width,theme});
 try{
  const ready=await host.waitReady();assert.equal(ready.state,'ready',ready.error+'\n'+host.logs.join('\n'));
  const frame=host.frame();
  const inspect=async(zoom)=>frame.evaluate(zoom=>{
   const v=globalThis.__viewer;v.setCameraZoom(zoom);v.update(true,false);
   const g=v.rendered.gridHelper,c=g.getCamera(),boxes=[];
   for(const group of g.children)if(group.visible)for(const s of group.children)if(s.isSprite&&s.visible){
    const p=s.getWorldPosition(s.position.clone()).project(c);
    boxes.push({x:(p.x+1)*g.cadWidth/2,y:(1-p.y)*g.height/2,w:18*s.userData.aspectRatio,h:18});
   }
   return{step:g.delta/2,count:boxes.length,boxes,ticks:g.ticks,flags:v.getGrids()};
  },zoom);
  const base=await frame.evaluate(()=>globalThis.__viewer.getCameraZoom());
  const close=await inspect(base*3),far=await inspect(base/3);
  assert(close.step<far.step,JSON.stringify({close:close.step,far:far.step}));
  assert(far.count>0 && far.count<40,JSON.stringify(far));
  const fine=await inspect(base*100);
  assert(fine.step<close.step && fine.ticks<40,JSON.stringify(fine));
  for(const state of [close,far])for(let i=0;i<state.boxes.length;i++)for(let j=i+1;j<state.boxes.length;j++){
   const a=state.boxes[i],b=state.boxes[j];
   assert(Math.abs(a.x-b.x)>=(a.w+b.w)/2+7.9||Math.abs(a.y-b.y)>=(a.h+b.h)/2+7.9,'overlapping coordinate labels');
  }
  await inspect(base);
  await host.page.screenshot({path:`test-output/grid-${width}-${ortho?'ortho':'perspective'}-${theme}.png`});
  await frame.getByRole('button',{name:'Hide grid',exact:true}).click();
  assert.deepEqual(await frame.evaluate(()=>globalThis.__viewer.getGrids()),[false,false,false]);
  assert.equal(host.logs.filter(l=>/\[(error|pageerror)\]/.test(l)).length,0,host.logs.join('\n'));
  console.log(`PASS ${width}px ${ortho?'ortho':'perspective'} ${theme}: intervals ${close.step} → ${far.step}, ${far.count} sparse labels, no overlaps, grid toggle preserved`);
 }finally{await host.close();}
}
