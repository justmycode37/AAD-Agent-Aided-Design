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
         │ one sandboxed process per job, killable
         ▼
 ┌─────────────────┐
 │ kernel process  │   runs model.py, then checks, scene, renders
 │ (fresh per job) │   build123d → OCCT; a crash costs one job, never the model
 └─────────────────┘
```

The three layers are separate processes because kernel crashes and hangs are expected, and the MCP handshake must not wait for kernel imports.

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
3. The daemon starts a new Python process under the OS sandbox: `sandbox-exec` on macOS, bubblewrap on Linux. It has no network and can write only to its job directory. Without a working sandbox the build fails with `SANDBOX_DENIED`; there is no unsandboxed fallback.
4. That process runs `model.py`. `feature()` records which faces and edges each named feature produced. It then runs the hard gate, spec checks and process checks, tessellates a scene, and optionally renders a preview.
5. The daemon kills the process at the time limit: 60 s by default, up to 600 s with `timeout_s`. If the build outlasts `wait_s`, the call returns `running` with a job id, which the agent polls through `aad_history`.
6. A successful build becomes an immutable revision on disk. A failure never moves the head revision; a built candidate that failed a check is kept for inspection.
7. The agent gets a short text summary, plus an image only when requested.

Later operations such as render, check, measure, query, export, drawing and compare each run in their own sandboxed process through `aad_platform.ops`, against a stored revision.

### Status

Results report `failed`, `draft` or `verified`. The model and its checks share one process, so the parent does not trust that process's claim of `verified`: it returns `draft` with a `worker_verification_unattested` check. **So every successful build is `draft` today.** An independent validator exists in `aad.engine.validation`, but the build path does not use it yet. Exporting a draft requires `allow_unverified`, and its files are marked `UNVERIFIED`.

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
  exports/                 files for the user
  .aad/
    revs/000007/           model.py, spec, meta.json, part.brep, scene
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
- A memory limit on jobs. `aad.engine.memory` exists, but builds and operations do not apply it.
- Use of the content-addressed cache in `aad.engine.cache` by the daemon.
- The independent validator in the build path, so `verified` is not reachable.
- A sandbox on Windows, so builds fail there with `SANDBOX_DENIED`.
- Spec requirements on `kind="2d"` sheets. Sheets build and run their 2D checks, but a spec is not evaluated on them, so they stay `draft`.
- A global mate solver. Assemblies place parts explicitly with `assembly()` and `at=`.
