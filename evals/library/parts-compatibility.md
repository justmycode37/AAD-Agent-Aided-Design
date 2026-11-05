# `bd_warehouse` compatibility and standard-part checks

## Pin and upstream evidence

Use this exact optional dependency pin in AIAD's optional `parts` extra:

```toml
"bd_warehouse==0.4.0"
```

The upstream [v0.4.0 `pyproject.toml`](https://github.com/gumyr/bd_warehouse/blob/v0.4.0/pyproject.toml) declares `build123d >=0.12.0, <0.14.0`, which includes AIAD's contractual `build123d==0.13.0`; it also permits Python 3.12 and depends on `bd_materials >=0.2.0, <0.3.0`. The official tag points to commit `8e9be0645b53db3d7414f74828c2d67b0da21138`. The source is [Apache-2.0](https://github.com/gumyr/bd_warehouse/blob/v0.4.0/LICENSE).

The installed `bd_warehouse==0.4.0` files for `fastener.py`, `bearing.py`, `thread.py`, and the fastener and bearing parameter CSVs matched the corresponding v0.4.0 tag files byte for byte. The exercised constructors are [`SocketHeadCapScrew`](https://github.com/gumyr/bd_warehouse/blob/v0.4.0/src/bd_warehouse/fastener.py), [`SingleRowDeepGrooveBallBearing`](https://github.com/gumyr/bd_warehouse/blob/v0.4.0/src/bd_warehouse/bearing.py), and [`IsoThread`](https://github.com/gumyr/bd_warehouse/blob/v0.4.0/src/bd_warehouse/thread.py).

## Installed versions and geometry results

Environment: Python `3.12.10`, `build123d==0.13.0`, `cadquery-ocp-novtk==8.0.1.1.0`, `bd_warehouse==0.4.0`, `bd_materials==0.2.5`, and `cadquery-ocp-proxy==8.0.1.1.0`.

Each default adapter result was built through its pinned upstream constructor. `is_valid` is the build123d/Open CASCADE shape validity check. Bbox values are measured extents in millimeters.

| Family and constructor | Inputs / relevant dimensions | Valid | Solids | Bbox extents (X, Y, Z), mm |
|---|---|---:|---:|---:|
| Fastener — `SocketHeadCapScrew` | M6-1, 20 mm under-head length; 10.22 mm head diameter, 6 mm head height | yes | 1 | 10.22, 10.22, 26.00 |
| Bearing — `SingleRowDeepGrooveBallBearing` | SKT M8-22-7; 8 mm bore, 22 mm outside diameter, 7 mm width | yes | 14 | 22.00, 22.00, 7.00 |
| Thread — `IsoThread` | external M6-1, 8 mm length, square ends | yes | 9 | 6.00, 5.999901, 8.00 |

These checks establish that the selected API calls build valid shapes with the expected units and major dimensions. They do not establish manufacturing compliance, tolerance fit, bearing rating, or load capacity. The adapter uses full helical fastener threads and documents simplified bearing internals; the thread-only result has no shaft or mating part.

The parent strengthened the native BREP oracle after review: bounds alone admitted a smooth cylinder in place of the thread and an annulus in place of the bearing. It now checks helical edge lead/handedness, two sampled axial tooth profiles, and the bearing's separate races and twelve spherical rolling elements. The profile equations and rolling-element layout are scoped to the pinned default models, not a reproduction of a paid standard. Negative cylinder, annulus, wrong-pitch, and wrong-profile models are regression fixtures in `test_library_evidence.py`. These witnesses do not prove complete thread tolerances, interference-free bearing motion, or purchased-hardware compliance.

## Reproduce

From the AIAD worktree root:

```bash
uv venv --python 3.12 packages/quality/.parts-venv
uv pip install --python packages/quality/.parts-venv/bin/python \
  build123d==0.13.0 \
  cadquery-ocp-novtk==8.0.1.1.0 \
  bd_warehouse==0.4.0 \
  pytest==9.2.0 \
  pytest-timeout==2.4.0
PYTHONPATH=packages/quality/src \
  packages/quality/.parts-venv/bin/python -m pytest -q \
  packages/quality/tests/test_standard_parts.py
```

The adapter smoke checks can print the observed bboxes and validity with:

```bash
PYTHONPATH=packages/quality/src \
  packages/quality/.parts-venv/bin/python - <<'PY'
from aiad_quality.standard_parts import list_parts, make_part

for entry in list_parts():
    part = make_part(entry["id"])
    box = part.bounding_box()
    valid = part.is_valid() if callable(part.is_valid) else part.is_valid
    extents = tuple(
        round(value, 6)
        for value in (
            box.max.X - box.min.X,
            box.max.Y - box.min.Y,
            box.max.Z - box.min.Z,
        )
    )
    print(entry["family"], valid, len(part.solids()), extents)
PY
```

The installed adapter environment ran `test_standard_parts.py`: **14 passed**. The tests cover kernel-free lookup, defensive catalog copies, closed-registry and parameter rejection, supported size/length data against upstream tables, and real builds with dimension/validity checks, a single fused full-thread screw, alternate screw sizes, and rejection of construction inside an active builder.

The parent reran all adapter tests and separately repeated the native OCCT validity and optimal-bounds measurements above. These are deterministic construction controls, not sampled agent results or timing benchmarks; no model-quality variance is estimated. For repeated geometric variation evidence, use `evals/library/probe_parts.py` below.

Parent correction: the default screw now uses `simple=False`. In upstream 0.4.0, `simple=True` returns a minor-diameter thread core, which understates the external clearance envelope. The repeated probe isolates the shaft thread zone with a native boolean and requires an approximately 6 mm X/Y envelope; this would reject the simplified M6 core. Bearing bore occupancy is probed on both sides of the 4 mm radius. These are bounded geometric witnesses, not tolerance-fit certificates.

Repeated parent evidence: `PYTHONPATH=packages/quality/src packages/quality/.parts-venv/bin/python evals/library/probe_parts.py --repetitions 3` writes parts-evidence.json, invalidates prior evidence at startup, binds source/BREP hashes, and measures each freshly imported BREP using native OCCT optimal bounds and validity. Every default part was valid in all three repetitions. Per-axis sample SD was 0 mm for these deterministic extents; the full means and probe observations are in the JSON. No speed, model-quality, or population-variance claim follows from that repetition.
