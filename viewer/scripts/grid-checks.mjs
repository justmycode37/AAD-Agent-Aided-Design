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
    boxes.push({x:(p.x+1)*g.cadWidth/2,y:(1-p.y)*g.height/2,w:18*(s.userData.aspectRatio*Math.abs(Math.cos(s.material.rotation))+Math.abs(Math.sin(s.material.rotation))),h:18*(s.userData.aspectRatio*Math.abs(Math.sin(s.material.rotation))+Math.abs(Math.cos(s.material.rotation)))});
   }
   return{camera:{type:c.type,near:c.near,far:c.far,position:c.position.toArray(),zoom:c.zoom},ppu:g.height*Math.abs(c.projectionMatrix.elements[5])/(2*(c.isPerspectiveCamera?c.position.distanceTo(g.position.clone().set(...v.getCameraTarget())):1)),modelDepth:g.position.clone().set(...v.bbox.center()).project(c).z,step:g.delta/2,count:boxes.length,boxes,ticks:g.ticks,flags:v.getGrids(),anchors:g.children.map(p=>p.position.toArray()),size:g.size,worldLabels:g.children.flatMap(p=>p.children.filter(s=>s.isSprite).map(s=>[s.userData.aadLabelId,s.getWorldPosition(s.position.clone()).toArray()]))};
  },zoom);
  const base=await frame.evaluate(()=>globalThis.__viewer.getCameraZoom());
  const close=await inspect(base*3),far=await inspect(base/3);
  assert(close.step<far.step,JSON.stringify({close:close.step,far:far.step}));
  assert(far.count>0 && far.count<40,JSON.stringify(far));
  const farther=await inspect(base/30);
  assert(farther.step>far.step && farther.size>far.size,'grid must keep expanding and coarsening beyond model bounds');
  assert(farther.count>0 && farther.count<40,JSON.stringify(farther));
  assert(farther.modelDepth>=-1 && farther.modelDepth<=1,'model must remain inside camera depth range');
  assert.deepEqual(farther.anchors,far.anchors,'range expansion must not shift coordinate planes');
  if(width===900&&!ortho&&theme==='light')await host.page.screenshot({path:'test-output/grid-far-zoom.png'});
  // Shared coordinates retain exactly the same world positions across a range change.
  const world=new Map(far.worldLabels);
  const shared=farther.worldLabels.filter(([key])=>world.has(key));
  for(const [key,position] of shared)assert.deepEqual(position,world.get(key),'coordinate drift: '+key);
  const fine=await inspect(base*100);
  assert(fine.step<close.step && fine.ticks<=128,JSON.stringify(fine));
  for(const state of [close,far,farther])for(let i=0;i<state.boxes.length;i++)for(let j=i+1;j<state.boxes.length;j++){
   const a=state.boxes[i],b=state.boxes[j];
   assert(Math.abs(a.x-b.x)>=(a.w+b.w)/2+7.9||Math.abs(a.y-b.y)>=(a.h+b.h)/2+7.9,'overlapping coordinate labels');
  }
  assert.deepEqual(close.anchors,far.anchors,'zoom must not move the grid planes');
  assert.deepEqual(fine.anchors,far.anchors,'close zoom must not move the grid planes');
  assert(far.size>close.size,'zooming out must increase drawn coverage');
  await inspect(base);
  const beforeMotion=await frame.evaluate(()=>{
   const v=globalThis.__viewer,g=v.rendered.gridHelper;
   return {anchors:g.children.map(p=>p.position.toArray()),size:g.size,worldLabels:g.children.flatMap(p=>p.children.filter(s=>s.isSprite).map(s=>[s.userData.aadLabelId,s.getWorldPosition(s.position.clone()).toArray()]))};
  });
  await frame.evaluate(()=>{
   const v=globalThis.__viewer,c=v.rendered.camera.getCamera(),t=v.getCameraTarget();
   const shift=[13,-17,9];
   v.setCameraPosition(c.position.toArray().map((n,i)=>n+shift[i]),false,false);
   v.setCameraTarget(t.map((n,i)=>n+shift[i]),false);v.update(true,false);
   v.presetCamera('front');v.update(true,false);
  });
  const afterMotion=await frame.evaluate(()=>{const g=globalThis.__viewer.rendered.gridHelper;return{anchors:g.children.map(p=>p.position.toArray()),size:g.size,worldLabels:g.children.flatMap(p=>p.children.filter(s=>s.isSprite).map(s=>[s.userData.aadLabelId,s.getWorldPosition(s.position.clone()).toArray()]))};});
  assert.deepEqual(afterMotion.anchors,beforeMotion.anchors,'pan and orbit must keep the coordinate planes fixed');
  const oldCoordinates=new Map(beforeMotion.worldLabels);
  for(const [key,position] of afterMotion.worldLabels)if(oldCoordinates.has(key))assert.deepEqual(position,oldCoordinates.get(key),'pan/orbit coordinate drift');
  await frame.evaluate(()=>{globalThis.__viewer.presetCamera('iso');globalThis.__viewer.update(true,false);});
  await host.page.screenshot({path:`test-output/grid-${width}-${ortho?'ortho':'perspective'}-${theme}.png`});
  await frame.getByRole('button',{name:'Hide grid',exact:true}).click();
  assert.deepEqual(await frame.evaluate(()=>globalThis.__viewer.getGrids()),[false,false,false]);
  assert.equal(host.logs.filter(l=>/\[(error|pageerror)\]/.test(l)).length,0,host.logs.join('\n'));
  console.log(`PASS ${width}px ${ortho?'ortho':'perspective'} ${theme}: intervals ${close.step} → ${far.step} → ${farther.step}, ${far.count} sparse labels, no overlaps, fixed world anchors through zoom/pan/orbit, grid toggle preserved`);
 }finally{await host.close();}
}
