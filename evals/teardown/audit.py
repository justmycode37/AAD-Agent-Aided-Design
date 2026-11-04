#!/usr/bin/env python3
"""Independent artifact checks; no competitor recognizer or validation result used."""
from __future__ import annotations
import argparse
import hashlib
import json
import math
import platform
from importlib.metadata import version
from pathlib import Path


def audit(path: Path, task: str) -> dict:
    from build123d import Cylinder, Pos, Vector, Align, import_step
    from OCP.BRepCheck import BRepCheck_Analyzer
    shape = import_step(str(path))
    width = 80 if task == 'edit_plate' else 60
    expected = [width, 40, 22 if task == 'blocked_counterbore' else 6]
    size = shape.bounding_box().size
    bbox = [size.X, size.Y, size.Z]
    checks = {
        'brep_valid': bool(BRepCheck_Analyzer(shape.wrapped).IsValid()),
        'one_solid': len(shape.solids()) == 1,
        'bbox': all(abs(a-b) < 1e-5 for a,b in zip(bbox, expected, strict=True)),
    }
    evidence = {'bbox_mm': bbox, 'volume_mm3': shape.volume}
    if task != 'blocked_counterbore':
        spacing = 66 if task == 'edit_plate' else 46
        centers = [(x*spacing/2, y*13) for x in (-1,1) for y in (-1,1)]
        # Source frame is part of these tasks. Sample bore axes and adjacent stock
        # at three heights; compare analytic volume to catch added/removed stock.
        checks['four_through_axes_void'] = all(not shape.is_inside(Vector(x,y,z)) for x,y in centers for z in (-2,0,2))
        checks['diameter_bracketed_4_4'] = all(
            not shape.is_inside(Vector(x+2.19*dx,y+2.19*dy,z))
            and shape.is_inside(Vector(x+2.21*dx,y+2.21*dy,z))
            for x,y in centers for dx,dy in ((1,0),(-1,0),(0,1),(0,-1)) for z in (-2,0,2))
        expected_volume = width*40*6 - 4*math.pi*2.2**2*6
        checks['analytic_volume'] = abs(shape.volume-expected_volume) < 1e-5
        evidence['expected_volume_mm3'] = expected_volume
    else:
        directions=((1,0),(-1,0),(0,1),(0,-1))
        checks['through_bore_4_4'] = all(
            not shape.is_inside(Vector(2.19*dx,2.19*dy,z))
            and shape.is_inside(Vector(2.21*dx,2.21*dy,z))
            for dx,dy in directions for z in (0.5,1.5,2.5))
        checks['counterbore_9_0'] = all(
            not shape.is_inside(Vector(4.49*dx,4.49*dy,z))
            and shape.is_inside(Vector(4.51*dx,4.51*dy,z))
            for dx,dy in directions for z in (3.5,4.5,5.5))
        checks['counterbore_depth_3_0'] = all(
            shape.is_inside(Vector(3*dx,3*dy,2.99))
            and not shape.is_inside(Vector(3*dx,3*dy,3.01))
            for dx,dy in directions)
        expected_volume=60*40*6+2*4*16*16+22*16*4-2*4*16*4-math.pi*(2.2**2+4.5**2)*3
        checks['analytic_volume']=abs(shape.volume-expected_volume)<1e-5
        evidence['expected_volume_mm3']=expected_volume
        # A 2 mm radius coaxial approach probe above the plate is sufficient to
        # disprove this task's requested access. It is not a general access proof.
        probe = Pos(0,0,6.001)*Cylinder(2,18,align=(Align.CENTER,Align.CENTER,Align.MIN))
        overlap = shape.intersect(probe)
        overlap_volume = sum(s.volume for s in overlap) if isinstance(overlap,list) else overlap.volume
        evidence['approach_probe_intersection_mm3'] = overlap_volume
        evidence['obstruction_point_inside'] = bool(shape.is_inside(Vector(0,0,19)))
        checks['coaxial_access'] = overlap_volume < 1e-6
    return {'file': str(path), 'task': task, 'step_sha256':hashlib.sha256(path.read_bytes()).hexdigest(),
            'checks':checks, 'evidence':evidence, 'requirements_pass':all(checks.values())}


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('root', type=Path, help='Adapter outputs/step directory, paired with its results.json manifest')
    parser.add_argument('--out', type=Path, required=True)
    args=parser.parse_args()
    base=args.root.resolve().parent.parent
    manifest=json.loads((base/'results.json').read_text())
    if manifest.get('complete') is not True or not manifest.get('run_id'):
        raise SystemExit('Run is incomplete or has no run ID')
    entries=manifest.get('trials')
    if entries is None:
        entries=[dict(entry,task=task) for task,data in manifest['task_results'].items() for entry in data['repetitions']]
    records=[]
    for entry in entries:
        path=(base/(entry.get('step_path') or entry['step'])).resolve()
        if not path.is_relative_to(args.root.resolve()):
            raise SystemExit('Manifest artifact is outside the requested artifact directory')
        if hashlib.sha256(path.read_bytes()).hexdigest()!=entry['step_sha256']:
            raise SystemExit('STEP hash differs from the current run manifest: '+str(path))
        task=entry['task']
        source=Path(__file__).parent/'tasks'/f'{task}.py'
        if hashlib.sha256(source.read_bytes()).hexdigest()!=entry['source_sha256']:
            raise SystemExit('Task source differs from the run manifest: '+str(source))
        records.append(audit(path,task))
    if not records:
        raise SystemExit('No task STEP artifacts found; no audit was performed')
    args.out.write_text(json.dumps({'manifest_run_id':manifest['run_id'],
                                  'environment':{'python':platform.python_version(), 'build123d':version('build123d'),
                                  'cadquery-ocp-novtk':version('cadquery-ocp-novtk')},
                                  'method':'fresh STEP import, BRepCheck, occupancy and analytic volume; no competitor recognizer',
                                  'scope':'deterministic teardown artifacts, not an independent sampled agent audit',
                                  'records':records},indent=2)+'\n')
    print(json.dumps([{'task':r['task'],'file':r['file'],'checks':r['checks']} for r in records],indent=2))
    mismatches=[r['file'] for r in records if any(
        passed != (key != 'coaxial_access') for key,passed in r['checks'].items())]
    if mismatches:
        raise SystemExit('Unexpected fixture outcomes: '+', '.join(mismatches))

if __name__=='__main__':main()
