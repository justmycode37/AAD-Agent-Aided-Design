# S5 benchmark corpus

This directory contains the host-side task definitions, the deliberately small public projection, a declared public fit-reference asset, and positive-control builders. `tasks.json` contains hidden outcomes and oracle checks. Solvers receive `public.json` plus only the assets declared on each stage.

## Inventory

| Category | Tasks | Stages |
|---|---:|---:|
| Parts | 40 | 40 |
| Edit chains | 20 | 40 |
| Assemblies | 10 | 10 |
| 2D profiles | 15 | 15 |
| Drawings | 5 | 5 |
| Vague requests requiring clarification | 5 | 5 |
| Contradictory or infeasible requests | 5 | 5 |
| **Total** | **100** | **120** |

There are 110 artifact stages and 10 disposition-only stages. Each edit chain has two stages. The artifact tasks use 53 structural family labels across parts, assemblies, profiles, and drawings; no family appears more than four times. Coverage includes an inch-specified task whose hidden millimetre checks use the exact 25.4 mm/in conversion, and a two-stage STL import/edit chain. The corpus has not been run against real agents, so it has no benchmark result or quality claim.

## Reproduce corpus generation and checks

The commands below keep the environment and package cache inside `corpus/`. The versions match the pins in `CONTRACTS.md`.

```bash
uv venv evals/benchmark/corpus/.venv --python 3.12
UV_CACHE_DIR="$PWD/evals/benchmark/corpus/.uv-cache" \
  uv pip install --python evals/benchmark/corpus/.venv/bin/python \
  'build123d==0.13.0' 'cadquery-ocp-novtk==8.0.1.1.0' 'ezdxf==1.4.5'
evals/benchmark/corpus/.venv/bin/python -m evals.benchmark.corpus.generate_tasks
evals/benchmark/corpus/.venv/bin/python -m unittest evals.benchmark.tests.test_corpus -v
```

`generate_tasks` validates the task records before writing `tasks.json` and `public.json`. The test command checks schema, exact category counts, public/hidden separation, edit preservation, finite numbers, safe asset paths, and supported reference builds, while explicitly reporting unsupported drawings. `loader.py` imports neither build123d nor ezdxf; the reference builders load them only when called.

The reference builds are host-only positive controls. They are not solver inputs, sampled-agent results, AIAD quality-evaluator results, or manufacturing proof. The corpus tests and task-generation script do not constitute a benchmark run.

## Current local reference-build result

The parent's checks exposed placeholder drawing controls: labeled rectangles and typed dimension values do not establish projected geometry or measured dimensions. Those controls now raise an explicit unsupported error. The supported controls are 80 part/edit stages, 10 assemblies and 15 DXF profiles; the five drawings remain pending actual projected controls. Native OCCT bounds, solid counts and material classifications and independent DXF extents are compared to the definitions. This checks local reference witnesses, not complete request correctness or an independently imported solver artifact.

All artifact-stage coverage is bounded pending an independent whole-request oracle. Parent corrections preserve exact inch tolerances, interpret DXF extents as sizes, state profile placement and a tab notch explicitly, correct T/slot geometry, preserve wheel-lobe projection and angular bounds, remove an unrequested upper clearance limit, and include a width edit preserving bores. These changes predate real-agent trials. Re-run the commands above for current results; there is no agent-quality variance or false-verified denominator yet.

See [COVERAGE.md](COVERAGE.md) for the requirement-to-check map and bounded extensions.
