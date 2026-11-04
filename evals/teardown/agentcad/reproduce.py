#!/usr/bin/env python3
"""Fixed-source tasks through AgentCAD MCP; independent audit is ../audit.py."""
from __future__ import annotations
import argparse
import asyncio
import hashlib
from importlib.metadata import version
import json
import os
from pathlib import Path
import platform
import shutil
import statistics
import subprocess
import time
import uuid
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

BASE=Path(__file__).resolve().parent
OUT=BASE/'outputs'
PYTHON=BASE/'.venv/bin/python'
COMMIT='428abffa763c12882697de6449c59ea15c18c540'
TASKS=('plate','edit_plate','blocked_counterbore')


def payload(reply):
    if isinstance(reply.structuredContent,dict):
        return reply.structuredContent
    for item in reply.content:
        if item.type=='text':
            try:
                value=json.loads(item.text)
                if isinstance(value,dict):return value
            except ValueError:pass
    raise RuntimeError('No JSON result in MCP reply')


def environment():
    return {k:os.environ[k] for k in ('PATH','SYSTEMROOT') if k in os.environ}|{
        'TMPDIR':str(OUT/'tmp'),'MPLCONFIGDIR':str(OUT/'mpl'),
        'XDG_CACHE_HOME':str(OUT/'cache'),'PYTHONUNBUFFERED':'1'}


async def trial(task,repeat):
    # Every trial has a new project; stale successful outputs cannot be reused.
    cwd=OUT/'projects'/f'{task}-{repeat}-{time.time_ns()}'
    cwd.mkdir(parents=True)
    target=OUT/'step'/task/f'{task}-r{repeat}.step'
    target.parent.mkdir(parents=True,exist_ok=True)
    target.unlink(missing_ok=True)
    init=subprocess.run([str(BASE/'.venv/bin/agentcad'),'init','--name',task,'--runtime','build123d','--no-agent-setup'],
        cwd=cwd,env=environment(),capture_output=True,text=True,timeout=90)
    if init.returncode:raise RuntimeError(init.stdout+init.stderr)
    calls=[]
    async def call(session,name,args):
        start=time.perf_counter()
        reply=await session.call_tool(name,args)
        data=payload(reply)
        calls.append({'tool':name,'arguments':args,'elapsed_s':time.perf_counter()-start,
                      'reply':reply.model_dump(mode='json',exclude_none=True)})
        (cwd/f'call-{len(calls):02d}-{name}.json').write_text(json.dumps(calls[-1],indent=2)+'\n')
        if reply.isError or data.get('_exit_code',0):
            raise RuntimeError(f'{name} failed: {data}')
        return data
    params=StdioServerParameters(command=str(PYTHON),args=['-m','agentcad.mcp'],cwd=str(cwd),env=environment())
    start=time.perf_counter()
    async with stdio_client(params) as (read,write):
        async with ClientSession(read,write) as session:
            await session.initialize()
            inventory=await session.list_tools()
            async def build(name,label):
                source=BASE.parent/'tasks'/f'{name}.py'
                return await call(session,'run',{'script':str(source),'cwd':str(cwd),'output':label,
                    'preview':False,'view':False,'diff':False})
            if task=='edit_plate':await build('plate','before')
            built=await build(task,'after' if task=='edit_plate' else 'part')
            if task=='edit_plate':await call(session,'diff',{'ref1':'before','ref2':'after','cwd':str(cwd)})
            step=(cwd/built['outputs']['step']).resolve()
            if not step.is_relative_to(cwd) or not step.is_file():raise RuntimeError('No fresh returned STEP')
            measured=await call(session,'measure',{'file':str(step),'cwd':str(cwd),'cylinders_only':True})
            await call(session,'inspect',{'file':str(step),'cwd':str(cwd),'summary':True})
            features=[{'name':'through_holes','type':'cylinder','diameter_mm':4.4,'count':1 if task=='blocked_counterbore' else 4}]
            if task=='blocked_counterbore':features.append({'name':'counterbore','type':'cylinder','diameter_mm':9,'count':1})
            spec=cwd/'spec.json'
            spec.write_text(json.dumps({'expect_solids':1,'features':features}))
            checked=await call(session,'check_spec',{'file':str(step),'spec_file':str(spec),'cwd':str(cwd)})
            if checked.get('passed') is not True:raise RuntimeError(f'Partial cylinder spec failed: {checked}')
    elapsed=time.perf_counter()-start
    shutil.copyfile(step,target)
    record={'task':task,'repeat':repeat,'elapsed_total_s':elapsed,'source_sha256':hashlib.sha256((BASE.parent/'tasks'/f'{task}.py').read_bytes()).hexdigest(),
        'step':str(target.relative_to(BASE)),'step_sha256':hashlib.sha256(target.read_bytes()).hexdigest(),
        'build':built,'measure':measured,'partial_spec':checked,'calls':calls}
    raw=OUT/f'{task}-r{repeat}.json'
    raw.write_text(json.dumps(record,indent=2)+'\n')
    compact={k:v for k,v in record.items() if k!='calls'}
    compact['build']={k:built[k] for k in ('status','metrics','validation','outputs','promotion','current_advanced') if k in built}
    compact['measure']={k:measured[k] for k in ('status','metrics','validity') if k in measured}
    compact['partial_spec']={k:checked[k] for k in ('passed','structure_checks','validity','matched_features','missing_features') if k in checked}
    compact['tool_timings_s']=[{'tool':c['tool'],'elapsed_s':c['elapsed_s']} for c in calls]
    (OUT/'tool_inventory.json').write_text(json.dumps([t.model_dump(mode='json',exclude_none=True) for t in inventory.tools],indent=2)+'\n')
    return compact,[{'name':t.name,'inputSchema':t.inputSchema} for t in inventory.tools]


async def main(repetitions):
    run_id=str(uuid.uuid4())
    (BASE/'results.json').unlink(missing_ok=True)
    (BASE/'audit.json').unlink(missing_ok=True)
    actual=subprocess.check_output(['git','-C',str(BASE/'upstream'),'rev-parse','HEAD'],text=True).strip()
    if actual!=COMMIT:raise RuntimeError('Wrong upstream commit')
    subprocess.run(['git','-C',str(BASE/'upstream'),'diff','--exit-code','HEAD','--'],check=True,capture_output=True)
    for name in ('tmp','mpl','cache'): (OUT/name).mkdir(parents=True,exist_ok=True)
    records=[]
    inventory=[]
    for task in TASKS:
        for repeat in range(1,repetitions+1):
            record,inventory=await trial(task,repeat)
            records.append(record)
            print(f'saved {task} repetition {repeat}',flush=True)
    result={'complete':True,'run_id':run_id,'environment':{'python':platform.python_version(),'platform':platform.platform(),
        'upstream_commit':COMMIT,'packages':{p:version(p) for p in ('agentcad','build123d','cadquery-ocp','mcp')}},
        'scope':'fixed source over real MCP; no agent benchmark; partial cylindrical spec does not test access',
        'tool_inventory':inventory,'trials':records}
    (BASE/'results.json').write_text(json.dumps(result,indent=2)+'\n')
    for task in TASKS:
        times=[r['elapsed_total_s'] for r in records if r['task']==task]
        print(task,{'n':len(times),'mean_s':statistics.mean(times),'sample_sd_s':statistics.stdev(times) if len(times)>1 else None})

if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--repetitions',type=int,default=3)
    args=parser.parse_args()
    if args.repetitions<1:parser.error('positive repetitions required')
    asyncio.run(main(args.repetitions))
