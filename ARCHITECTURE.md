# AAD architecture

AAD is CAD for agents. It runs in the background behind MCP, so any agent can design 2D and 3D parts and check them. It has no interface of its own: when a user asks to see a part, the agent shows it inline in the chat.

This document explains how AAD is built. `CONTRACTS.md` holds the exact interfaces and the repository workflow.

```
 Agent + host (Claude Desktop, Cursor, Codex, T3 ...)
    │  MCP over stdio                        ▲ sandboxed iframe in chat
    ▼                                        │ postMessage (MCP Apps)
 ┌─────────────────┐   serves ui://aad/viewer│
 │ aad mcp         │─────────────────────────┘
 │ thin adapter    │   no kernel import, so it starts fast
 └───────┬─────────┘
         │ JSON-RPC over unix socket
         ▼
 ┌─────────────────┐   HTTP + WebSocket on 127.0.0.1 ──► local page (only from aad_show)
 │ aadd daemon     │   projects, revisions, job queue, scene store
 └───────┬─────────┘
         │ sandboxed worker per build, then independent verifier
         ▼
 ┌─────────────────┐
         │ model worker    │   runs model.py and saves the BREP
         │ (fresh per job) │   build123d → OCCT; a crash costs one job, never the model
 └─────────────────┘
         │ worker exits; saved BREP is loaded once in a clean process
         ▼
         ┌──────────────────────────┐
         │ trusted BREP validator   │   hard gate + spec + process + geometry-only checks
         │ and scene generation     │   same loaded shape; no model code, sandboxed
         └──────────────────────────┘
```

The adapter, daemon, model worker and validator are separate processes because kernel crashes and hangs are expected, and the MCP handshake must not wait for kernel imports. The validator generates the scene from its already-loaded BREP shape.

Hosts that cannot reach a local process use `aad serve --http` instead of stdio. It speaks Streamable HTTP, authenticates with bearer tokens, and gives each user a private home and daemon. All daemons run as the account that started the server, so the worker sandbox is the boundary around model code.

## Packages

| Path | Distribution, import | Role |
|---|---|---|
| `packages/platform` | `aad-design`, `aad_platform` | `aad` CLI, MCP adapter, `aadd` daemon, remote server, client config writers, `doctor` |
| `packages/engine` | `aad-engine`, `aad` | Modeling API, gear library, build worker, OS sandbox, hard gate, selectors, measurement, file I/O |
| `packages/render` | `aad-render`, `aad_render` | Scene tessellation for the viewer, and VTK images for the agent |
| `packages/drafting` | `aad-drafting`, `aad_drafting` | 2D models, constraints, drawings, DXF/SVG/PDF |
| `packages/quality` | `aad-quality`, `aad_quality` | `spec.yaml` evaluator, process checks, reference comparison, parts and materials library |
| `viewer` | not a Python package | TypeScript viewer on three-cad-viewer, built into one HTML file that ships inside `aad_platform` |
| `skills` | packaged with AAD | Server instructions, tool descriptions, playbooks |
| `evals`, `fixtures`, `tests/e2e` | not shipped | Benchmarks, sample models, end-to-end smoke tests |

Kernel versions are pinned in `packages/engine/pyproject.toml`.

## Life of a build

1. The agent calls `aad_build` with full source, a patch, or param overrides, plus the `base_rev` it edited. A stale `base_rev` is refused.
2. The adapter forwards the call over the socket, and the daemon queues it per project.
3. The daemon starts a new Python model worker under the OS sandbox: `sandbox-exec` on macOS, bubblewrap on Linux. It has no network and can write only to its job directory. Without a working sandbox the build fails with `SANDBOX_DENIED`; there is no unsandboxed fallback.
4. The worker runs `model.py` and saves the BREP. It may also produce provisional checks, metrics, feature records, and a scene, but the validator does not trust those claims. Build metrics include worker per-phase elapsed times in milliseconds (`phase_timings_ms`), with one entry per hard-gate check plus model, spec, process, scene, render and write. The parent adds `other` as residual elapsed time not accounted for by those phases; it can include worker launch, protocol, parent validation and other uninstrumented work, so it does not identify a cause. A build over 20 seconds names its slowest measured phase in the summary. Assembly mesh checks mesh one private copy and validate each solid's triangle arrays separately. Scene generation builds assembly face and edge indexes once per scene.
5. After the worker exits, the parent starts one clean sandboxed validator with its own 600 s budget. It snapshots and loads the saved BREP once, reruns the hard gate, evaluates the saved spec and project process settings, runs registered geometry-only checks, and generates the viewer scene from that same loaded shape. It does not run model code or use worker metrics, checks, feature records or scenes as verification evidence. `validation_ms` includes scene generation; `validation_only_ms` excludes it. A timeout or scene failure keeps the BREP as draft with trusted geometry metrics and a reason.
6. The worker is killed at the model time limit: 60 s by default, up to 600 s with `timeout_s`. The validator uses its separate budget. If the build outlasts `wait_s`, the call returns `running` with a job id, which the agent polls through `aad_history`.
7. An accepted build becomes an immutable revision on disk. A failure never moves the head revision; a built candidate that failed a check is kept for inspection.
8. The agent gets a short text summary, plus an image only when requested.

Later operations such as render, check, measure, query, export, drawing and compare run in their own process through `aad_platform.ops`, against a stored revision. Check, measure, query, export, drawing and compare always use the OS sandbox. On local macOS and Linux 3D render, the worker loads the B-rep and computes inspection data inside that sandbox, then sends a versioned mesh and view payload to a clean one-shot VTK process outside it. The VTK process receives no project, revision or B-rep path. The sandbox policy is unchanged; 2D previews and all other operations still run entirely inside it. Remote mode continues to refuse render.

### Status

`aad_build` reports `failed`, `draft` or `verified`. The parent never accepts the model worker's `verified` claim. For 3D, its validator loads the saved BREP once, runs hard, spec, process and geometry-only checks, then generates the clean scene from the same shape. A 3D result also needs that scene to be complete before it can be verified. For 2D, a separate clean process recomputes supported checks and metrics from saved `sheet.json` and generates the SVG scene from that snapshot; 2D revisions remain draft until verification receipts support them. A 3D receipt binds the BREP, parsed spec, kernel versions, project settings, requirement results and proven IDs, saved metrics and checks, and scene to the revision. Export and status operations recheck these values before reporting `verified`. The receipt catches changes while intact; it is not signed against someone who can rewrite the whole project directory and receipt.

A build is `verified` only when the hard gate passes, at least one error-level requirement has an independent `pass`, every other error-level requirement is `pass` or `not_applicable`, every warning-level requirement was evaluated, and the clean 3D scene was generated. A warning-level requirement may report `warn` and still be verified; build and export summaries name the advisory requirements that did not meet their targets. A spec with only warning-level requirements stays `draft` because nothing required was proven. Build-time automatic assembly-interference warnings appear in the build summary. Builds with no requirements, unresolved selector-based measurements, error-level skips, or scene failures stay `draft`. Worker feature records are untrusted and are not used by the validator. `not_applicable` means the validator established from its own geometry that a check's subject is absent; it does not block verification when a required check independently passes. `skip` remains unknown and blocks at error level. `min_wall` and `symmetric` remain disproof-only and blocking when skipped. A verified summary and `aad_inspect` name the requirements the evaluator proved.

## Life of a view

1. The user asks to see the part, and the agent calls `aad_show`. It can also pass a presentation and named views, such as sections, isolated parts, or sourced analysis colors.
2. The host loads the single-file viewer into a sandboxed iframe and passes it the result, which holds a scene manifest but no mesh.
3. The viewer pulls the mesh through the app-only tool `aad_scene_read`, in 128 KiB chunks with at most 4 in flight, checks its hash, and renders with WebGL. The model never sees mesh bytes.
4. Changing a presentation keeps the same scene bytes, so the viewer does not fetch the mesh again.

Only `aad_show` declares a UI. Agents build many times per task, and a UI on `aad_build` would drop a viewer into the chat on every iteration.

The viewer displays and inspects only; it does not edit the model. In chat it shows simple controls and the agent's views. The local page keeps the full CAD panels.

Other hosts get the same viewer another way:

| Host | How the part appears |
|---|---|
| Supports MCP Apps | Inline viewer, as above |
| No MCP Apps | `aad_show` returns a local URL. The daemon serves it on `127.0.0.1` with a per-launch token and Host and Origin checks, and pushes new revisions over WebSocket. Nothing opens unless the user sets `aad config viewer=window` |
| Accepts only HTML | `aad_platform.viewer.bundle` writes a self-contained page, keeping the original scene bytes |
| No display | PNG images from `aad_render` |

## Tools

Thirteen model-visible tools, defined in `aad_platform/tools.py`:

| Tool | Purpose |
|---|---|
| `aad_new` | Create a project: kind, units, process, optional spec |
| `aad_build` | Run or patch `model.py` into a new revision, with checks |
| `aad_inspect` | Summary, features, params, topology, or a diff between revisions |
| `aad_query` | Resolve semantic selectors to geometry |
| `aad_measure` | Distance, angle, radius, area, volume, inertia, clearance |
| `aad_check` | Run the hard, spec, process, interference or 2D suites |
| `aad_render` | Images for the agent: sheets, sections, labels, heatmaps |
| `aad_show` | Show the part when the user asks; the only tool with a UI |
| `aad_drawing` | Create a drawing from 3D or from a 2D model |
| `aad_history` | List, diff, checkout, branch, job status |
| `aad_import` | Bring in STEP, IGES, BREP, STL, DXF or SVG |
| `aad_export` | STEP, IGES, STL, OBJ, PLY, 3MF, GLB, BREP, DXF, SVG, PDF, standalone viewer HTML |
| `aad_library` | Standard parts, materials, playbooks and API docs on demand |

Four app-only tools are for the viewer: `aad_scene_read`, `aad_set_param`, `aad_pick_info` and `aad_file_read`. Only `aad_scene_read` is needed to display a part.

Tool results keep large data out of the model's context. The text is short, images come only on request, and everything else is a handle.

The guide agents read is `skills/aad/SKILL.md`, which `aad_library` also serves as playbook `aad.guide.v1`.

## On disk

```
<root>/<project>/          <root> is the MCP root, else ~/AAD
  project.json             name, kind, units, process, head revision
  model.py                 current source
  spec.yaml                requirements
  request.json             original wording, assumptions, questions, unchecked items
  exports/                 files for the user
  .aad/
    revs/000007/           model.py, spec, request.json, meta.json, part.brep, scene
    jobs/                  job directories, the only place sandboxed code may write

~/.aad/                    config, daemon socket and token, logs, project index
```

`model.py` is the source of truth. It is ordinary build123d Python plus AAD's helpers (`params`, `feature`, `part`, `meta`, selectors, gears), and it must not do I/O. Each build reruns the whole script.

## Why it is built this way

- **Geometry comes from an existing kernel.** OCCT through build123d is mature. What AAD adds is checked output, reliable edits, and CAD the user can see in the conversation.
- **Python everywhere except the viewer,** because the kernel bindings are Python.
- **A viewer with no network, WASM, workers, `eval` or storage,** because hosts apply strict CSPs and may give iframes an opaque origin. Its bundle audit enforces this.
- **Mesh transfer through an app-only tool rather than a localhost fetch,** because the default MCP Apps CSP blocks connections, and a fetch would fail remotely.
- **`_meta.ui` always declared, not gated on what a host advertises,** because some hosts render apps without advertising support. Hosts without support treat these as plain tools.
- **OS sandboxing rather than code inspection,** because `model.py` is arbitrary Python. An AST filter is not a security boundary.

## Not built yet

- A warm worker pool. Every job starts a new process.
- An aggregate resident-memory ceiling for each whole job. Builds and operations now apply `aad.engine.memory` as a per-process `RLIMIT_AS` growth budget (`auto`: 16 GiB on Linux and macOS), but it is virtual address space rather than RSS; Linux child processes each get their own budget.
- Use of the content-addressed cache in `aad.engine.cache` by the daemon.
- Named-body `fit` and feature-bound `position` requirements remain unproven when they depend on model-written component names or feature records. Component labels do not survive saved BREP import, and the validator does not trust worker metadata as a name-to-body map.
- `min_wall` and `symmetric` remain disproof-only and keep a build in draft when skipped. Validator timeouts or scene failures retain a successful model build as draft and include trusted metrics; rerun `aad_build` to try again.
- A sandbox on Windows, so builds fail there with `SANDBOX_DENIED`.
- Spec requirement types without a 2D meaning remain explicitly skipped on `kind="2d"` sheets.
- A global mate solver. Assemblies place parts explicitly with `assembly()` and `at=`.
