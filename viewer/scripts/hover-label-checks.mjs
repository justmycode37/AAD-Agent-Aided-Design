// Real GPU picking through the app: names, revision-local IDs and minimal layout.
import assert from 'node:assert/strict';
import { readFileSync, mkdirSync } from 'node:fs';
import { gzipSync, gunzipSync } from 'node:zlib';
import { createHash } from 'node:crypto';
import { openRefHost } from './ref-host.mjs';
import { fakeMcp, loadFixture } from './fake-mcp.mjs';
const fixture=loadFixture('tests/fixtures/bracket');
const tree=JSON.parse(gunzipSync(fixture.blob));
const root=tree.shapes??tree, first=root.parts[0];
root.parts=[-65,65].map((x,i)=>({...structuredClone(first),id:`/Group/motor-${i+1}`,name:`motor-${i+1}`,loc:[[x,0,0],first.loc[1]]}));
root.bb.xmin-=65;root.bb.xmax+=65;
const blob=gzipSync(JSON.stringify(tree));
const scene={blob,manifest:{...fixture.manifest,scene_id:createHash('sha256').update(blob).digest('hex'),total_bytes:blob.length,triangles:fixture.manifest.triangles*2,parts:root.parts.map(p=>({path:p.id,name:'Motor mount'}))}};
const html=readFileSync('dist/aad-viewer.html','utf8');
mkdirSync('test-output',{recursive:true});
for(const width of [390,900]){
 const mcp=fakeMcp(scene);
 mcp.toolResult.structuredContent.viewer={mode:'inline',title:'Hexa',views:[{id:'second',label:'Second motor',presentation:{isolate:['/Group/motor-2']}}]};
 const host=await openRefHost({html,toolResult:mcp.toolResult,onRequest:mcp.onRequest,width});
 try{
  assert.equal((await host.waitReady()).state,'ready');
  const frame=host.frame(), label=frame.locator('.aad-part-label');
  assert.equal(await label.isVisible(),false);
  const point=async(path)=>frame.evaluate(path=>{
   const v=globalThis.__viewer,rect=v.renderer.domElement.getBoundingClientRect();v.idPicker.setDirty();
   for(let y=70;y<rect.height-10;y+=6)for(let x=10;x<rect.width-10;x+=6){
    const h=v.idPicker.pickAt(x,y,{windowSize:1});
    if(h?.info.topo==='face'&&h.info.solidPath===path)return[x+rect.left,y+rect.top];
   }
   throw Error('No visible face for '+path);
  },path);
  for(const id of [1,2]){
   await host.page.mouse.move(...await point(`/Group/motor-${id}`));
   await frame.waitForFunction(text=>document.querySelector('.aad-part-label')?.textContent===text,`Motor mount · #${id}`);
   assert.equal(await label.isVisible(),true);
  }
  const layout=await label.evaluate(el=>{const b=el.getBoundingClientRect(),t=document.querySelector('.aad-caption').getBoundingClientRect(),s=document.querySelector('.aad-stage').getBoundingClientRect(),c=getComputedStyle(el);return{below:b.top>=t.bottom,inside:b.left>=s.left&&b.right<=s.right,font:c.fontSize,events:c.pointerEvents};});
  assert.deepEqual(layout,{below:true,inside:true,font:'12px',events:'none'});
  await host.page.mouse.move(1,1);
  await label.waitFor({state:'hidden'});
  await frame.getByRole('button',{name:'Views',exact:true}).click();
  await frame.getByRole('menuitemradio',{name:'Second motor',exact:true}).click();
  await host.page.mouse.move(...await point('/Group/motor-2'));
  await frame.waitForFunction(()=>document.querySelector('.aad-part-label')?.textContent==='Motor mount · #2');
  await host.page.screenshot({path:`test-output/hover-label-${width}.png`});
  assert.equal(host.logs.filter(l=>/\[(error|pageerror)\]/.test(l)).length,0,host.logs.join('\n'));
  console.log(`PASS ${width}px: hover name/ID, leave, isolated-view numbering and minimal layout`);
 }finally{await host.close();}
}
