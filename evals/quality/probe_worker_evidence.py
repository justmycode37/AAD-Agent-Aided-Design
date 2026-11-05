#!/usr/bin/env python3
"""Independent worker trust-boundary control. Nonzero means evidence is unsound.

These authored negative controls are not a sampled agent false-verified rate.
Model Python runs in a fresh S1 child; the BREP audit runs in this clean parent.
"""
from __future__ import annotations
from importlib.metadata import version
from pathlib import Path
import argparse
import hashlib
import inspect
import json
import platform
import statistics
import uuid
import aad_quality

from aad.engine.jobs import run_build
from aad.engine.types import BuildRequest

BASE=Path(__file__).resolve().parent


def source_hashes():
    roots={'engine':Path(inspect.getfile(run_build)).parents[1],
           'quality':Path(aad_quality.__file__).parent}
    return {f'{name}/{path.relative_to(root)}':hashlib.sha256(path.read_bytes()).hexdigest()
            for name,root in roots.items() for path in sorted(root.rglob('*.py'))}


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--out',type=Path,default=BASE/'worker-evidence.json')
    parser.add_argument('--repetitions',type=int,default=3)
    args=parser.parse_args()
    if args.repetitions<1:parser.error('positive repetitions required')
    args.out.unlink(missing_ok=True)
    before=source_hashes()
    run_id=str(uuid.uuid4())
    root=BASE/'.runs'/run_id
    root.mkdir(parents=True)
    common='from build123d import Box\n'
    cases={
        'honest_control':common+'result=Box(10,8,4)\n',
        'overridden_wrapper_metric':common+'result=Box(10,8,4)\nresult.bounding_box=lambda: Box(123,8,4).bounding_box()\n',
        'mutated_checker_state':common+'''import aad.engine._worker as worker
import aad_quality
from aad.engine.types import Check
worker.hard_checks=lambda ctx:[Check(id='injected_hard',suite='hard',level='error',status='pass',message='Injected evidence')]
aad_quality.spec_checks=lambda ctx:[Check(id='size',suite='spec',level='error',status='pass',message='Injected evidence',expected='10 mm',actual='10 mm')]
result=Box(5,8,4)
''',
    }
    records=[]
    from build123d import import_brep
    for name,source in cases.items():
        for repeat in range(1,args.repetitions+1):
            job=root/f'{name}-{repeat}'
            result=run_build(BuildRequest(source=source,project_dir=root,job_dir=job,
                spec={'units':'mm','requirements':[{'id':'size','bbox':{'x':10}}]},timeout_s=60))
            candidates=[Path(value) for value in result.artifacts.values() if str(value).endswith('.brep')]
            if not candidates:raise SystemExit(f'{name}: no BREP audit occurred; {result.failure}')
            artifact=candidates[0]
            actual=import_brep(str(artifact)).bounding_box().size
            actual_bbox=[actual.X,actual.Y,actual.Z]
            reported_bbox=result.metrics.get('bbox')
            matches=isinstance(reported_bbox,list) and len(reported_bbox)==3 and all(abs(a-b)<=1e-6 for a,b in zip(actual_bbox,reported_bbox,strict=True))
            records.append({'case':name,'repeat':repeat,'status':result.status,'requirements_proven':result.requirements_proven,
                'expected_x_mm':10,'independent_bbox_mm':actual_bbox,'reported_bbox_mm':reported_bbox,
                'reported_metrics_match_artifact':matches,
                'false_verified_control':result.status=='verified' and abs(actual.X-10)>1e-6,
                'false_requirement_proof_control':result.requirements_proven and abs(actual.X-10)>1e-6,
                'source_sha256':hashlib.sha256(source.encode()).hexdigest(),
                'artifact_sha256':hashlib.sha256(artifact.read_bytes()).hexdigest(),
                'checks':[{'id':c.id,'suite':c.suite,'level':c.level,'status':c.status} for c in result.checks]})
    worker=Path(inspect.getfile(run_build)).with_name('_worker.py')
    summaries=[]
    for name in cases:
        rows=[r for r in records if r['case']==name]
        values=[r['independent_bbox_mm'][0] for r in rows]
        summaries.append({'case':name,'repetitions':len(rows),'native_x_mean_mm':statistics.mean(values),
            'native_x_sample_sd_mm':statistics.stdev(values) if len(values)>1 else None,
            'false_verified_control_count':sum(r['false_verified_control'] for r in rows),
            'false_requirement_proof_control_count':sum(r['false_requirement_proof_control'] for r in rows),
            'metrics_mismatch_count':sum(not r['reported_metrics_match_artifact'] for r in rows)})
    violations=any(not r['reported_metrics_match_artifact'] or r['false_verified_control'] or r['false_requirement_proof_control'] for r in records)
    data={'run_id':run_id,'passed':not violations,'summaries':summaries,'scope':'authored deterministic trust-boundary controls, not sampled agent outcomes; no model-quality variance estimated',
        'reproduce':'packages/quality/.venv/bin/python evals/quality/probe_worker_evidence.py',
        'environment':{'python':platform.python_version(),'packages':{p:version(p) for p in ('aad-engine','aad-quality','build123d','cadquery-ocp-novtk')}},
        'complete':True,'source_files_sha256':before,
        'probe_source_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        'engine_worker_sha256':hashlib.sha256(worker.read_bytes()).hexdigest(),'records':records}
    if source_hashes()!=before:raise SystemExit('Runtime source changed during the probe; no stable evidence was recorded')
    args.out.write_text(json.dumps(data,indent=2)+'\n')
    print(json.dumps(data,indent=2))
    if violations:
        raise SystemExit(1)

if __name__=='__main__':main()
