// Verify the minimal loader across delayed transport, actual download, preparation and failure.
import assert from 'node:assert/strict';
import {readFileSync,mkdirSync} from 'node:fs';
import {openRefHost} from './ref-host.mjs';
import {fakeMcp,loadFixture} from './fake-mcp.mjs';
const html=readFileSync('dist/aad-viewer.html','utf8'),fixture=loadFixture('tests/fixtures/bracket');
mkdirSync('test-output',{recursive:true});
for(const theme of ['light','dark']){
 let release;const gate=new Promise(resolve=>release=resolve);
 const mcp=fakeMcp(fixture);
 const host=await openRefHost({html,toolResult:mcp.toolResult,theme,width:728,onRequest:async(method,params)=>{if(method==='tools/call')await gate;return mcp.onRequest(method,params);}});
 try{
  const frame=host.frame(),bar=frame.locator('.aad-progress');
  await bar.waitFor({state:'visible'});
  await frame.waitForFunction(()=>document.querySelector('.aad-progress')?.getAttribute('aria-valuenow')==='0');
  const state=await bar.evaluate(el=>{const b=el.getBoundingClientRect();return{width:b.width,height:b.height,text:el.textContent,role:el.getAttribute('role')};});
  assert.deepEqual(state,{width:180,height:2,text:'',role:'progressbar'});
  await frame.evaluate(()=>{
   globalThis.__loadingStates=[];
   const bar=document.querySelector('.aad-progress');
   new MutationObserver(records=>{for(const r of records)if(r.attributeName==='aria-label')globalThis.__loadingStates.push({label:bar.getAttribute('aria-label'),value:bar.getAttribute('aria-valuenow')});}).observe(bar,{attributes:true});
  });
  await host.page.screenshot({path:`test-output/loading-${theme}.png`});
  release();assert.equal((await host.waitReady()).state,'ready');
  assert.equal(await bar.isVisible(),false);
  assert(await frame.evaluate(()=>globalThis.__loadingStates.some(s=>s.label==='Preparing model'&&s.value===null)),'preparation remains active instead of sitting at 100%');
  assert.equal(host.logs.filter(l=>/\[(error|pageerror)\]/.test(l)).length,0,host.logs.join('\n'));
  console.log(`PASS ${theme}: 2px loader, real transfer progress, indeterminate preparation, hidden when ready`);
 }finally{release();await host.close();}
}
const mcp=fakeMcp(fixture);
const host=await openRefHost({html,toolResult:mcp.toolResult,onRequest:async(method,params)=>{if(method==='tools/call')throw Error('injected failure');return mcp.onRequest(method,params);}});
try{
 const ready=await host.waitReady();assert.equal(ready.state,'error');
 assert.equal(await host.frame().locator('.aad-progress').isVisible(),false);
 assert.equal(await host.frame().locator('.aad-error').isVisible(),true);
 console.log('PASS failed load: loader stops and error remains visible');
}finally{await host.close();}
