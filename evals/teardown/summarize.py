#!/usr/bin/env python3
"""Report sample variance without equating partial specs with task compliance."""
import json
import statistics
import hashlib
from pathlib import Path

root=Path(__file__).resolve().parent
reports=[]
for adapter,directory in [('build123d-mcp','build123d_mcp'),('agentcad','agentcad')]:
    data=json.loads((root/directory/'results.json').read_text())
    if 'trials' in data:
        records=data['trials']
    else:
        records=[dict(r,task=task,elapsed_total_s=r['elapsed_total_ms']/1000)
                 for task,task_data in data['task_results'].items() for r in task_data['repetitions']]
    if data.get('complete') is not True or not data.get('run_id'):
        raise SystemExit('Incomplete run: '+directory)
    audit=json.loads((root/directory/'audit.json').read_text())
    if audit.get('manifest_run_id')!=data['run_id']:
        raise SystemExit('Audit is from another run: '+directory)
    if sorted(r['step_sha256'] for r in audit['records'])!=sorted(r['step_sha256'] for r in records):
        raise SystemExit('Audit artifacts differ from the run: '+directory)
    for record in audit['records']:
        if any(value != (key!='coaxial_access') for key,value in record['checks'].items()):
            raise SystemExit('Unexpected fixture outcome: '+directory)
    for record in records:
        source=root/'tasks'/f"{record['task']}.py"
        if hashlib.sha256(source.read_bytes()).hexdigest()!=record['source_sha256']:
            raise SystemExit('Current input differs from run: '+directory)
    for task in ('plate','edit_plate','blocked_counterbore'):
        times=[r['elapsed_total_s'] for r in records if r['task']==task]
        if not times:raise SystemExit(f'No observations for {adapter}/{task}')
        reports.append({'adapter':adapter,'task':task,'n':len(times),
            'mean_s':statistics.mean(times),'sample_sd_s':statistics.stdev(times) if len(times)>1 else None,
            'min_s':min(times),'max_s':max(times),
            'scope':'whole MCP sequence; different call sequences are not a speed ranking',
            'agent_false_verified_rate':None,'reason':'no sampled agent claims or independent agent audit'})
for report in reports:print(json.dumps(report))
