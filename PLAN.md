# AIAD: AI-aided design

CAD for agents.

Master plan, written 2026-11-04. Read this and `CONTRACTS.md` before working on any sector. `CONTRACTS.md` is binding for interfaces; this file is binding for scope and priorities.

**Goal:** any agent can install AIAD, design real 2D and 3D parts through MCP, and prove they meet the request. AIAD runs in the background and has no interface of its own. When the user asks to see a part, the agent shows it inline in the chat.

**The bar for v1:** a user asks for a mechanical part or a 2D drawing, and gets an exact, editable, manufacturable result they can orbit in chat and export as STEP, STL, 3MF, GLB, DXF, SVG or PDF. Parity with Fusion, NX or SolidWorks is not a v1 goal. CAM, simulation, advanced surfacing and PLM are out of scope (see section 12).

**What makes AIAD different:** the geometry comes from an existing kernel, so that is not the moat. The moat is three things together: output that is verified rather than assumed, reliable edits, and CAD the user can see in the conversation when they ask for it.

---

## 1. What the user experiences

### Install

`aiad` has to exist before `aiad install` can run, so there are two steps. A user can paste them, or tell an agent "install AIAD":

```bash
uv tool install aiad        # puts `aiad` on the machine
aiad install                 # sets up everything else
```

`aiad install` does the following:

1. Downloads and pins the runtime (kernel, renderer, viewer).
2. Pre-warms it. First imports took about 60 s on this Mac and later ones about 1 s (section 5), and that wait must never land on an MCP startup.
3. Detects installed agents and writes each one's MCP config, with a backup. Writes are idempotent.
4. Starts the local daemon and runs `aiad doctor`: build a test part, render it, export it, check the viewer bundle. Nothing opens on screen.
5. Tells the user which clients need a new session, because most read MCP config at session start.

### Use

The user just talks to their agent: "Design a wall bracket for a 60 mm fan, 3 mm steel, four M4 holes." Then:

1. The agent writes `spec.yaml`, the request as checkable requirements.
2. The agent writes `model.py` and calls `aiad_build`. It gets back numbers, check results and exact failures.
3. The agent looks at its own renders and sections, then fixes what is wrong.
4. If the user asks to see the part, the agent calls `aiad_show`. A 3D viewer appears inline in the chat, with orbit, zoom, pan, section, measure and a fullscreen button. Nothing is shown otherwise.
5. The user says what to change in words, and the agent edits the named feature.
6. The agent calls `aiad_export`, and files land in the project folder.

### Seeing the output

| Host | What the user sees |
|---|---|
| Claude Desktop, VS Code Copilot, Cursor, Goose, MCPJam | Inline interactive viewer (MCP Apps) |
| claude.ai, ChatGPT, and web or enterprise hosts on the matrix | Same inline viewer, but web hosts can't reach a local process, so they need the remote server from milestone M3 |
| T3 Code with Codex | Inline viewer expected, not tested yet (section 5) |
| T3 Code with Claude, Claude Code, Codex CLI, any host without MCP Apps | PNG renders in the tool result. On request, a local URL from `aiad_show` or a standalone page from `aiad_export`. Nothing opens by itself. |

Hosts control iframe size. Fullscreen only works after a user click inside the viewer, so it is a button and never automatic.

`aiad config viewer=window` is a manual opt-in: `aiad_show` then opens the local page in a browser. Nothing else opens a window.

---

## 2. Architecture

```
 Agent + host (Claude Desktop, Cursor, Codex, T3 ...)
    │  MCP over stdio                        ▲ sandboxed iframe in chat
    ▼                                        │ postMessage (MCP Apps)
 ┌─────────────────┐   serves ui://viewer    │
 │ aiad mcp         │─────────────────────────┘
 │ thin adapter     │   no kernel import, starts in under 1 s
 └───────┬─────────┘
         │ JSON-RPC over unix socket
         ▼
 ┌─────────────────┐   HTTP + WebSocket on 127.0.0.1 ──► local page (only from aiad_show)
 │ aiadd daemon     │   projects, revisions, job queue, cache, scene store
 └───────┬─────────┘
         │ one job per worker, killable
         ▼
 ┌─────────────────┐
 │ kernel workers   │   OS sandbox: runs model.py, then checks, scene, renders
 │ (warm processes) │   build123d → OCCT; crash costs one job, never the model
 └─────────────────┘
```

**Life of a build:**

1. The agent calls `aiad_build` with source or a patch and a `base_rev`.
2. The adapter forwards it to the daemon, which queues it per project.
3. A warm worker runs `model.py` in the sandbox. `feature()` records which faces and edges each named feature produced.
4. The worker runs validity, spec and process checks, then tessellates a scene and optionally renders a preview sheet.
5. The daemon stores the result as an immutable revision if the build ran. A failure never moves the head revision.
6. The agent gets a short text summary, plus an image when requested.

**Life of a view:**

1. The user asks to see the part, and the agent calls `aiad_show`, which declares a `ui://` resource.
2. The host loads the single-file viewer into a sandboxed iframe and passes it the result, which holds a scene manifest but no mesh.
3. The viewer pulls the mesh through the app-only tool `aiad_scene_read` in chunks, then renders it with WebGL. The model never sees mesh bytes.

**Why only `aiad_show` has a UI, and not `aiad_build`:** agents build many times per task. A UI on `aiad_build` would drop a WebGL iframe into the chat on every iteration. The agent iterates using numbers and renders, and shows the result only when the user asks.

---

## 3. Decisions

| # | Decision | Why |
|---|---|---|
| D1 | **Kernel: OCCT through build123d 0.13.0** with `cadquery-ocp-novtk` 8.0.1.1.0, versions pinned | Mature B-rep and STEP. Writing a kernel takes years. Apache-2.0 bindings. |
| D2 | **Source of truth: `model.py`** (build123d plus a thin `aiad` helper layer), not a new DSL | Agents already know Python and build123d. In CADDesigner's 200-model test, build123d had the highest Pass@1 and the lowest latency against CadQuery, though with wider geometric variability. S5 tests this against real edit tasks. |
| D3 | **Adapter, daemon, workers** as separate processes | Kernel crashes and hangs are expected. The MCP handshake must not wait for kernel imports. |
| D4 | **All Python for v1**, TypeScript only in the viewer | Fastest path, because the kernel bindings are Python. A Rust daemon is only worth considering if a measured bottleneck demands it. |
| D5 | **Whole-script rebuild with a content-hash cache** first, feature-level memoization later | The 23-face test part built in 18 ms. Optimize once complex parts prove it matters. |
| D6 | **Viewer: three-cad-viewer 5.1.3 (MIT)**, bundled single-file with Vite | Already has clipping, explode, measure, properties, a tree, zebra and a studio mode, B-rep picking filters, and fast `updatePart`. Minified JS is 1.45 MB including three.js. |
| D7 | **Mesh transfer: app-only chunk tool**, not localhost fetch | The default MCP Apps CSP is `connect-src 'none'`, so a localhost fetch is fragile and breaks remotely. The official pdf-server example uses this same chunking pattern because hosts limit tool-call size. |
| D8 | **No WASM, no workers, no storage, no network in the viewer**; compress with gzip and native `DecompressionStream` | The default CSP does not list `wasm-unsafe-eval`, and iframes may have an opaque origin. T3 uses `sandbox="allow-scripts allow-forms"`. |
| D9 | **Always declare `_meta.ui` and always register app-only tools**, do not gate on the capability advert | The spec says servers should check the advert, but the installed Codex binary has MCP App fields and no `io.modelcontextprotocol/ui` string. Gating could hide the viewer from Codex. Hosts without support treat these as plain tools. |
| D10 | **Agent self-view: VTK offscreen** behind a pluggable interface, with a one-day try at something lighter | It works today: 4 views at 1024 px in 0.24–0.41 s. Its cost is about 640 MB on disk, so a lighter backend is worth a short test. build123d 0.13 does not depend on VTK, so it is an added install. |
| D11 | **Quality gates decide `verified` status**; export of an unverified revision requires an explicit override and is marked as such | Validity is not correctness, as section 6 shows. build123d-mcp reports its export gate and tooling took CAD validity from 88% to 100% on CADGenBench (their own figure). |
| D12 | **Sandbox model code at the OS level** | `model.py` is arbitrary Python and hosts may have no other guardrails. An AST lint alone is not a security boundary. |
| D13 | **Name, package and command: `aiad`** | `aad` is taken on PyPI (an Azure AD B2C SDK) and on npm. `aiad` was free on both on 2026-11-04. `aiad-cli` is a placeholder package someone else holds on both, so avoid it. This is a registry check only, not a trademark check. |
| D14 | **Constraint solver: our own layer on `scipy.optimize.least_squares`** | build123d has no global sketch solver. `python-solvespace` and `py-slvs` are GPL. SciPy is already a dependency. `@salusoft89/planegcs` (LGPL) is the fallback if convergence is poor. |
| D15 | **Stdio first; Streamable HTTP in M3** | Local hosts cover most of the matrix. claude.ai and ChatGPT need a remote server, which requires auth, so it comes later. |
| D17 | **Background MCP, with inline display only on request.** No automatic windows. No picking, sliders, Ask box or review tools. | The user's direction, recorded in issue #35. Agents do the work, and people see a part when they ask to. The app-only tools and selection types in `CONTRACTS.md` stay reserved and the viewer does not call them. |
| D16 | **Reuse and benchmark against agentcad and build123d-mcp** (both Apache-2.0) | They already cover install, CLI, validation and local viewing. The README summaries read so far mention no in-chat MCP App viewer. S5 tears them down in week 1, and anything they do better gets adopted with attribution. |

---

## 4. Tool surface

Thirteen model-visible tools and four app-only tools. Exact schemas are in `CONTRACTS.md` section 5.

| Tool | Purpose |
|---|---|
| `aiad_new` | Create a project: kind, units, process, optional spec |
| `aiad_build` | Run or patch `model.py` into a new revision, with gates |
| `aiad_inspect` | Summary, features, params, topology, or a diff between revisions |
| `aiad_query` | Resolve semantic selectors to geometry, with an expected count |
| `aiad_measure` | Distance, angle, radius, area, volume, mass, thickness, clearance |
| `aiad_check` | Run spec, DFM, interference or 2D suites |
| `aiad_render` | Images for the agent: 4-up sheet, sections, face labels, heatmaps |
| `aiad_show` | Show the part inline when the user asks to see it (the only UI tool) |
| `aiad_drawing` | Create a drawing from 3D or from a 2D model; returns a preview and files |
| `aiad_history` | List, diff, checkout, branch, job status |
| `aiad_import` | STEP, BREP, STL, DXF, SVG |
| `aiad_export` | STEP, STL, 3MF, GLB, BREP, DXF, SVG, PDF, standalone viewer HTML |
| `aiad_library` | Standard parts, materials, playbooks and API docs on demand |

App-only: `aiad_scene_read`, `aiad_set_param`, `aiad_pick_info`, `aiad_file_read`. The viewer uses `aiad_scene_read` only; the others stay registered per `CONTRACTS.md`.

Keep large data out of the model's context. Tool text stays near 200 tokens and images come only on request. Everything else is a handle.

---

## 5. Evidence

### Measured on this Mac (2026-11-04)

Single runs on a simple part with 23 faces and 60 edges. These show feasibility, not general performance.

| Step | Result |
|---|---|
| Import build123d, warm | 1.3–2.0 s |
| Build part, `is_valid` true | 18 ms |
| Export GLB with `binary=True` | 21 ms, 84 KB, real `glTF` magic. This fixes the broken `.glb` in the earlier spike, where `binary` defaulted to false. |
| Export STEP | 27 ms, 78 KB |
| Hidden-line projection to SVG | 10 ms, 46 visible and 67 hidden edges |
| Export DXF | 5 ms |
| VTK first import, then later imports | 59 s, then 0.8 s |
| VTK offscreen: 4 views at 1024 px | 0.24 s and 0.41 s, checked by viewing the image |
| Disk | 729 MB build123d environment, 640 MB VTK-only environment |

Not measured: complex parts, hard fillets, crash recovery, sandbox overhead, Linux or Windows, clean-machine install, any real host rendering the viewer. Earlier spike numbers and gaps are in `/Users/benajaheger/.t3/scratch/2026-11-04-i-want-to-write-a-0873c5b1/reports/Agent native CAD architecture.md`.

### Host support for MCP Apps

From the official client matrix on 2026-11-04.

- **Listed as supported:** Claude web, Claude Desktop, VS Code Copilot, Microsoft 365 Copilot, Goose, Postman, MCPJam, ChatGPT, Cursor, Archestra, PostHog Code, Kiro Crew, Glean.
- **Listed without support:** Claude Code, fast-agent, MCP Inspector.
- **Codex is not listed.** The installed binary (0.166.0-alpha.5.1) contains `mcpAppUi` and `mcpAppResourceUri`, so some support exists.

### T3 Code limits

Read from the installed app bundle, not tested, and may change between versions. Treat these as design limits.

- It is an MCP Apps host with `inline` and `fullscreen` modes, no `pip`.
- Only the Codex adapter calls the app capture code in this build, so Claude-provider threads probably will not render. S2 must test this.
- App HTML is limited to 5 MiB; larger documents do not render.
- Messages from app to host are limited to 256 KiB, with at most 16 requests in flight.
- File downloads are limited to 25 MiB and ask the user first.
- Fullscreen requires recent user activation inside the iframe.
- The iframe is `sandbox="allow-scripts allow-forms"`, with no same-origin access.
- It offers `openLinks`, `serverTools`, `serverResources`, `message`, `updateModelContext` and `downloadFile`.

### What the MCP Apps spec leaves open

There are no numeric size limits; hosts set their own. WebGL and WebAssembly are not addressed, although an official Three.js example exists. The example CSPs conflict on `connect-src`. Test on real hosts instead of trusting the document.

---

## 6. How output becomes good

Quality comes from a closed loop. No single step produces it.

```
Request → spec.yaml → model.py → build → hard gate → spec checks → process checks
                          ▲                                              │
                          └──── failures with feature, line, fix hint ◄──┤
                                                                         ▼
                                     agent reviews renders and sections against the request
                                                                         ▼
                                                        verified → aiad_show → export
```

**A concrete example of why every layer is needed.** The test bracket for section 5 reported `is_valid: true`. Its 4-up render immediately showed that one counterbore cuts into the upright wall, so a screw could not reach it. Kernel validity cannot see that. A render can, and so can a hole-access check, which now goes in the corpus.

| Layer | What it catches | Owner |
|---|---|---|
| Hard gate | Invalid solids, open shells, slivers, degenerate faces, STEP round-trip volume drift, non-watertight mesh | S1 |
| Spec checks | Wrong size, hole count, pattern, mass, clearance, symmetry | S5 |
| Process checks (heuristic) | Thin walls, overhangs, tool access, internal radii, bend radii, draft, blocked holes | S5 |
| Agent perception | Wrong intent and bad proportions. Sheets, sections, labels and heatmaps make these visible. | S3, S5 |
| Actionable failures | Vague kernel errors. Reports name the feature and line, tested values and repair options. Never invent a limit: report the "largest tested valid value" instead of a "maximum". | S1 |
| Stable edits | Rebuild-from-scratch regressions. Agents edit named features and params, and selectors stop when ambiguous. | S1 |
| Domain knowledge | Bad design habits. Playbooks for processes and part types, plus standard parts. | S5 |
| Evals | Regressions and false confidence. CADGenBench, our corpus, and baselines. | S5 |
| Visual finish | A result that looks like a real product: studio lighting, AO, clean edges, sensible materials | S3 |

`status` in tool results has three values: `failed`, `draft` and `verified`. A revision is `verified` when the hard gate passes, every `error`-level spec and process check passes, and at least one requirement exists. A `draft` means something was built but not proven. Agents must report this status honestly.

---

## 7. Sectors

Five threads run in parallel, each owning its own paths and its own git worktree, and each merging small PRs into `main` (`CONTRACTS.md` section 1). Each builds against the contracts and fixtures, stubbing what is not ready.

### S1: Engine

Owns `packages/engine/`, `fixtures/`.

1. **Modeling API.** `from aiad import *` re-exports build123d and adds `params()`, `part()`, `feature()`, selectors, `assembly()` and `meta()`.
2. **Feature tracking.** `feature()` diffs topology before and after, and assigns created and modified faces and edges to the feature name. Builds the feature DAG.
3. **Selectors.** `face(feature=..., kind=..., where=..., nth=...)` with an expected count. Ambiguity returns candidates instead of a guess. Raw indices never persist.
4. **Worker.** Warm process pool, JSON over pipes, per-job timeout (default 60 s), memory limit, clean kill, automatic replacement after a crash.
5. **Sandbox.** macOS `sandbox-exec`, Linux bubblewrap or landlock, Windows Job Objects. No network, writes only to the job dir. Measure overhead. Document exactly what is and is not protected.
6. **Revisions.** Immutable and content-addressed. The cache key covers source, params, spec, kernel version and tolerances. Undo, branch, checkout. Diff reports params, source, features, and shared and unique volume.
7. **Query and measure.** Exact values with units and the entities used: distances, angles, radii, inertia, clearance, interference.
8. **Diagnostics.** Turn kernel exceptions into the `Failure` object. Bounded bisection probes for fillet, chamfer and shell. Point at the feature and line.
9. **Hard gate.** `BRepCheck_Analyzer`, closed shells, body count, small edges and slivers, STEP round-trip volume, mesh watertightness.
10. **File I/O.** STEP AP214 and AP242, BREP, STL, 3MF, binary GLB. Import STEP, BREP and STL. An imported STEP becomes a base feature with no history, and the tool says so.
11. **Assemblies (M3).** Named parts, build123d joints, BOM, exploded transforms, interference.
12. **Regression corpus.** At least 30 parts covering edits, hard fillets, shells, sweeps, near-tangent cases and imports.

**Done when:**
- Edit retention holds across the corpus: named selectors survive parameter changes, or fail with candidates.
- A worker crash or hang never loses the head revision.
- A sandbox escape test suite passes on macOS.
- Every failure in the corpus names a feature or states `unknown`.

### S2: Platform

Owns `packages/platform/`, root config, `.github/`, `tests/e2e/`, `README.md`. Also the integrator: S2 owns CI and the end-to-end smoke test, and calls out contract breaks.

0. **Repo setup, first PR.** Root uv workspace with `members = ["packages/*"]`, so that new packages need no root edit. CI on every PR: each package's tests, plus the smoke test once it exists.

1. **CLI.** `install`, `uninstall`, `doctor`, `mcp`, `daemon`, `serve`, `build`, `show`, `export`, `config`, `update`, `version`. Every command supports `--json`.
2. **Client config writers.** Claude Code, Claude Desktop, Codex (also used by T3), Cursor, VS Code, Windsurf, Goose, Gemini CLI, Zed, Cline. Use absolute paths because GUI apps do not inherit `PATH`. Back up and write idempotently. Support `--client` and `--dry-run`. `uninstall` restores configs.
3. **Pre-warm.** Run every heavy first import during install, with progress output. Measure before and after.
4. **MCP adapter.** Official `mcp` Python SDK (2.3.0 today). Implement all tools and resources from the contract. Must work with both the pre-2026-07-28 `initialize` handshake and the stateless protocol; verify this against the SDK and real hosts. Serve `ui://aiad/viewer` with `text/html;profile=mcp-app`. Write the tool descriptions with S5.
5. **Long jobs.** `aiad_build(wait_s)` returns `running` plus a job id when it takes too long, and polling goes through `aiad_history`. Add the Tasks extension later if hosts support it.
6. **Daemon.** Auto-start, single-instance lock, unix socket or named pipe, per-project queue, idle exit, logs at `~/.aiad/logs`, version handshake with the adapter.
7. **Viewer modes.** `aiad config viewer=auto|inline|window|off`. `auto` means inline on hosts that advertise MCP Apps. Elsewhere `aiad_show` serves the viewer on `127.0.0.1` with a per-launch token, Origin checks and DNS-rebinding protection, and returns the URL; nothing opens. `window` is the manual opt-in that also opens a browser. The server starts only when `aiad_show` asks for it.
8. **Project location.** MCP roots when available, else `~/AIAD/<project>`, never an arbitrary working directory for GUI hosts.
9. **Packaging.** `uv tool` install on macOS arm64 first, Linux x86_64 and arm64, Windows and Intel Mac by M3. Report install size and time. Target at most 1 GB and 3 minutes. Build CI.
10. **Remote mode (M3).** `aiad serve --http`, Streamable HTTP, authentication, per-user isolation, for claude.ai and ChatGPT.
11. **Host test matrix.** Run the real flow on T3 + Codex, T3 + Claude, Claude Desktop, Claude Code, Codex CLI, Cursor, VS Code Copilot, Goose and MCPJam. Publish the results as `docs/host-matrix.md`, with screenshots.

**Done when:**
- From a clean macOS account, one paste to an agent results in a working AIAD on at least 5 clients.
- `aiad doctor` reports exactly what is wrong when it isn't working.
- `aiad mcp` responds to its first request in under 1 s, even with a cold kernel.
- `uninstall` leaves client configs exactly as they were.

### S3: Viewer and rendering

Owns `viewer/`, `packages/render/`.

1. **Bundle.** Vite + `vite-plugin-singlefile` + three-cad-viewer + `@modelcontextprotocol/ext-apps`. At most 3 MiB single HTML (T3's limit is 5 MiB). Audit dependencies for `eval`, `new Function`, WASM, workers and network use. Patch or configure them out.
2. **One app, three transports.** MCP App (postMessage), opt-in local page (HTTP), standalone exported HTML with an embedded scene. Same UI code.
3. **Tessellation.** `make_scene()` using ocp-tessellate where it fits. Deflection scales with the part's size. Each face and edge records its feature, so a later feature can use it. Gzip. Coarse level first for large models.
4. **Chunk loader.** Per contract: 128 KiB raw chunks, at most 4 in flight, retries, progress bar. Never put mesh bytes in `content` or `structuredContent` of a model-visible tool.
5. **Interaction.** Orbit, zoom, pan, view cube, ortho and perspective, section planes, explode, measure, tree, edges, studio mode: what three-cad-viewer already provides. Fullscreen button via `requestDisplayMode` on a user click only. Reports size changes. Follows host theme. Pauses rendering and releases WebGL when off-screen, since browsers cap contexts. Picking, sliders, an Ask box and review tools are not built (D17).
6. **2D mode.** Same app shows SVG sheets: pan, zoom, layers, measure.
7. **Agent renders.** `render()` for the agent: deterministic cameras, 4-up orthographic and iso sheet with labels, scale bar and axis triad. Sections with hatching, ephemeral face and edge labels, thickness and overhang heatmaps. Backend is VTK first, with a one-day test of lighter options (OCCT V3d offscreen, moderngl). Keep VTK if nothing beats it. Check Linux headless.
8. **Visual finish.** Good defaults so a first render looks professional: lighting, AO, edges, ground shadow, materials from the part's process.
9. **Export.** Standalone `viewer.html`. Turntable GIF or MP4 later.

**Done when:**
- The viewer renders and responds on MCPJam, Goose and T3 + Codex (screenshots required).
- A 5 MB scene loads within 2 s of the tool result, on this Mac, in a real host.
- A 500k-triangle scene orbits smoothly on this Mac.
- Render output is byte-stable for the same revision and camera, on one machine.

### S4: 2D and drawings

Owns `packages/drafting/`.

1. **2D model kind.** `kind: "2d"` projects with sketch primitives, offsets, booleans, text, patterns, and layers such as cut, score, engrave. Extrude into 3D with history retained.
2. **Constraints.** Named constraints: coincident, distance, angle, parallel, perpendicular, tangent, equal, symmetric, fixed. Report degrees of freedom. Report over- and under-constrained sketches by constraint name.
3. **Views from 3D.** Orthographic, iso, section with hatching, detail. Hidden lines use the kernel projection. Preserve true arcs and circles for DXF rather than polylines.
4. **Dimensioning.** Auto-dimensioning from features (hole callouts, overall sizes) and explicit dimensions. All values derive from geometry, never typed in. Layout avoids collisions. Tolerances. ISO and ASME style presets.
5. **Sheets.** A and ANSI sizes, scale selection, title block, revision table, BOM, notes.
6. **Exports.** SVG, DXF (R2013, with `ezdxf` audit), vector PDF with a permissive library, PNG preview for the agent.
7. **2D checks.** Closed contours, self-intersection, overlaps, dimension collisions, text overflow, margins, undimensioned features. Laser limits such as minimum slot and kerf.
8. **Import.** DXF and SVG into a 2D model.
9. **Golden set.** 15 reference drawings with snapshot tests, covering laser parts, turned parts, plates and assemblies.

Write style presets from general drafting practice. Do not copy text or tables from paid standards.

**Done when:**
- Each drawing's dimensions match 3D measurements within tolerance, enforced by test.
- All 15 golden drawings pass with zero collisions and zero overflow.
- All exported DXFs pass `ezdxf.audit` and open in an independent viewer such as LibreCAD.
- A person who has not seen the model could machine or cut the part from the sheet, by review of at least 3 drawings.

### S5: Quality and evals

Owns `packages/quality/`, `skills/`, `evals/`.

1. **Teardown first (week 1).** Install and run build123d-mcp and agentcad on the same three tasks. Write `docs/teardown.md`: what is better than our plan, and what to adopt. Send specifics to the owning sectors as GitHub issues.
2. **Spec evaluator.** `spec.yaml` per contract. Each requirement yields a check with expected, actual, tolerance and a fix hint.
3. **Process checks.** FDM, SLA, CNC 3-axis, sheet metal, laser, injection molding. Wall thickness, overhangs, tool access, internal radii, bend radii, draft, hole access. Each check says it is a heuristic and states the assumptions behind it.
4. **Playbooks.** Short and loaded on demand through `aiad_library`: the core modeling workflow, process guides, part families (brackets, enclosures, gears, threads, hinges, snap fits), failure recipes, and common build123d mistakes. Keep server `instructions` to around 300 tokens.
5. **Parts and materials.** `bd_warehouse` (Apache-2.0) for fasteners, bearings, threads. Material table with sourced densities. Mass is reported only with a material set.
6. **Review rubric.** A checklist for agents to compare renders to the request. Check whether it measurably improves results; delete it if it doesn't.
7. **Tool descriptions.** Tune against evals with S2. Measure wrong-tool rate, tokens per task and turns per task.
8. **Eval corpus.** At least 100 tasks with hidden checks: simple to hard parts, edit chains, assemblies, 2D, drawings, vague and contradictory requests. Include the blocked-counterbore case from section 6.
9. **Harness.** Run real agents through MCP. Compare AIAD, build123d-mcp, agentcad and plain build123d with the same model, budget and task. Use at least 2 model families. Repeat each task enough to report variance.
10. **CADGenBench.** Run the public scoring code (0.4 shape, 0.4 interface, 0.2 topology, behind a validity gate) on data available locally. Ground truth for the leaderboard is private and scored on their Space. **Do not submit publicly without the user's approval.**
11. **CI gates.** A fast subset runs on every change. The full run happens nightly. A drop in any headline metric blocks release.

**Metrics:** first-pass validity, requirement pass rate, edit retention, false-`verified` rate (the most important one), tokens per task, time per task.

**Done when:**
- AIAD beats the best baseline on requirement pass rate and edit retention on the corpus, at no more than 1.25× the tokens, with variance reported.
- False-`verified` rate is under 5% in an independent audit of sampled results.
- Every number has a reproducible command.

---

## 8. Milestones

Target durations are guesses, not derived from data.

| Milestone | Target | Must be true |
|---|---|---|
| **M0: walking skeleton** | Day 1–2 | `aiad_build` runs a build123d script in a worker, `aiad_show` displays the real part in an inline viewer on MCPJam or Goose and T3 + Codex, and the viewer loads by chunks. Everything else can be stubbed. Proves the riskiest assumption first. |
| **M1: one good part** | Week 1 | Full part loop on macOS: gates, failures, sandbox, renders, STEP/STL/GLB, install on 5 clients. First eval baseline. |
| **M2: reliable edits and 2D** | Weeks 2–3 | Selectors and edit chains, spec and process checks, drawings v1, 2D v1, playbooks, comparison with baselines. |
| **M3: reach** | Weeks 4–6 | Assemblies, BOM, drawings v2, standard parts, Linux and Windows, remote mode for claude.ai and ChatGPT. |
| **M4: public 0.1** | When gates pass | All done-when criteria met, docs and demo. License review complete. Public package and any leaderboard submission need the user's sign-off. |

**CI runs the smoke test on every PR and on `main`.** If `main` breaks, whoever broke it fixes or reverts it before any new feature work.

---

## 9. Performance budgets

On this Mac, for a typical part (at most 200 faces).

| Operation | Budget |
|---|---|
| `aiad mcp` first response, cold kernel | under 1 s |
| Build overhead beyond geometry time, warm worker | under 150 ms |
| Scene generation | under 300 ms |
| 4-up render sheet at 1024 px | under 500 ms |
| Viewer visible after tool result, 2 MB scene | under 1 s |
| Viewer HTML | at most 3 MiB |
| Tool result text | around 200 tokens |
| Full install on a fast connection | under 3 min |
| Disk | at most 1 GB (today about 1.4 GB if both environments ship as measured) |

---

## 10. Risks and tests that kill them

| Risk | Test | When |
|---|---|---|
| Inline viewer fails on real hosts (WebGL, CSP, sizes, app-only tools) | M0 on real hosts. If it fails everywhere, users rely on agent renders and a standalone `viewer_html` export. | Day 1 |
| Codex or T3 path behaves differently from the spec | S2 matrix. Also check whether Codex needs an `openai/outputTemplate` alias (the string is in its binary). | Day 1–2 |
| Stable references break under edits | S1 corpus: edit retention | M2 |
| Agents still produce wrong designs | S5 evals against baselines | M1 onward |
| `verified` gives false confidence | S5 independent audit of false-`verified` rate | M2 onward |
| Install is too large or slow | Clean-machine measurement, lighter render backend, trimming dependencies | M1 |
| `sandbox-exec` is deprecated on macOS | Escape tests. If it stops working, fall back to a container or VM option and say so in the docs. | M1, each macOS release |
| Competitors already satisfy the need | S5 teardown and head-to-head | Week 1 |
| MCP changes under us | Pin SDK versions, track the matrix, S2 e2e runs on real hosts | Ongoing |

---

## 11. Security and licensing

**Security:**
- Model code and imported files run only in sandboxed workers.
- All file paths are constrained to the project directory.
- The local server binds to `127.0.0.1`, requires a per-launch token, and checks Origin.
- The viewer loads nothing remote.
- Treat text in imported files as data, not instructions.
- Remote mode requires authentication and per-user isolation before anyone uses it.

**Licensing (research, not legal advice; get review before public release):**
- OCCT is LGPL-2.1 with an exception. Keep it as replaceable dynamic libraries and retain notices, even though the Python bindings are listed as Apache-2.0.
- Do not ship GPL components by default: `python-solvespace`, `py-slvs` and `gmsh` are out.
- LGPL components such as `cairosvg` and `planegcs` need a deliberate decision.
- Prefer MIT, BSD and Apache dependencies. Keep an SBOM from M1.
- Copy behavior, never code, UI, manuals or assets from commercial CAD. Native formats such as DWG, SLDPRT and F3D need a licensed SDK.

---

## 12. Not in v1

CAM and toolpaths, FEA and CFD, advanced surfacing, PMI and semantic GD&T, large assemblies (thousands of parts), PDM and PLM, native CAD formats, collaboration, a hosted cloud, a dedicated desktop app, automatic viewer windows, picking, parameter sliders, an Ask box, revision-compare and other review tools. Each one needs a real user asking for it first.

---

## 13. Sources

MCP: [MCP Apps overview](https://modelcontextprotocol.io/docs/extensions/apps), [spec 2026-01-26](https://github.com/modelcontextprotocol/ext-apps/blob/main/specification/2026-01-26/apps.mdx), [client matrix](https://modelcontextprotocol.io/extensions/client-matrix.md), [pdf-server example](https://github.com/modelcontextprotocol/ext-apps/tree/main/examples/pdf-server), [threejs-server example](https://github.com/modelcontextprotocol/ext-apps/tree/main/examples/threejs-server), [2026-07-28 release](https://blog.modelcontextprotocol.io/posts/2026-07-28-release-candidate/).

CAD: [build123d](https://github.com/gumyr/build123d), [build123d constraints](https://build123d.readthedocs.io/en/latest/tutorial_constraints.html), [three-cad-viewer](https://github.com/bernhard-42/three-cad-viewer), [ocp-tessellate](https://github.com/bernhard-42/ocp-tessellate), [bd_warehouse](https://github.com/gumyr/bd_warehouse).

Competitors and benchmarks: [agentcad](https://github.com/jdilla1277/agentcad), [build123d-mcp](https://github.com/pzfreo/build123d-mcp), [CADGenBench](https://github.com/huggingface/cadgenbench), [CADDesigner](https://arxiv.org/pdf/2508.01031), [Text2CAD-Bench](https://arxiv.org/pdf/2605.18430), [P3D-Bench](https://arxiv.org/pdf/2606.11152), [CADTestBench](https://arxiv.org/pdf/2605.07807).
