# AAD contracts

These interfaces let five threads work in parallel without talking to each other. Build against them and stub what is not ready.

**Changing a contract:** make the smallest additive change that works, in its own small PR that edits this file and adds a line to the changelog at the bottom with the date, sector and reason. Merge it quickly so others can rebase. A breaking change also needs a GitHub issue labelled for each affected sector. S2 arbitrates conflicts.

---

## 1. Repository and ownership

```
ARCHITECTURE.md, CONTRACTS.md        shared; edit only per the rule above
pyproject.toml, README.md, .github/  S2 (uv workspace root, CI)
uv.lock                              generated; any sector may regenerate it
docs/                                each sector's own named files
packages/engine/src/aad/             S1  public modeling API and engine
packages/platform/src/aad_platform/  S2  CLI, daemon, MCP adapter
packages/render/src/aad_render/      S3  tessellation and agent renders
packages/drafting/src/aad_drafting/  S4  2D, constraints, drawings
packages/quality/src/aad_quality/    S5  spec, process checks
viewer/                              S3  TypeScript; builds dist/aad-viewer.html
skills/                              S5  playbooks
evals/                               S5  corpus, harness, results
fixtures/                            S1  sample models, scenes, revisions
tests/e2e/                           S2  end-to-end smoke tests
```

**Git workflow (GitHub, PRs into `main`):**
- **Use your own worktree.** Threads do not share a checkout. From the main checkout, run `git fetch origin && git worktree add "../AAD-worktrees/<sector>" -b <sector>/<topic> origin/main` and do all work there. Skip this if your thread already runs in its own worktree. Never switch branches in the main checkout.
- **Keep branches small.** One topic per branch, such as `s3/chunk-loader`, merged within hours rather than days. Run `git fetch origin && git rebase origin/main` often to pick up other sectors' work.
- **Open a PR for every change:** `gh pr create --base main`, titled `s3: ...`. The body says what changed, how it was tested, and any contract changes.
- **Merge your own PRs.** Merge when the branch is rebased on the latest `main`, your tests pass and CI is green. For anything beyond a small change, have a sub-agent review the diff first and fix what it finds. Then run `gh pr merge --squash --delete-branch`. If branch protection requires a human, tell the user rather than bypassing it.
- **Never** push directly to `main`, force-push `main`, merge with failing checks, bypass protection with `--admin`, or rewrite another sector's branch. `--force-with-lease` is fine on your own branch after a rebase.
- **Stay in your paths.** Write only under paths you own. If another sector's code blocks you, open an issue labelled for that sector. A minimal fix in their path is allowed only when it is needed to unbreak `main`, and the PR must say so.
- **Message other sectors with GitHub issues** labelled `s1` to `s5`. Check yours when you start and between PRs.
- **Lock file conflicts:** rebase, run `uv lock`, and commit the result.
- **Never** change repo settings or visibility, publish packages, or create releases without asking the user.
- **Tooling:** use Python 3.12 and `uv`. If `uv` is missing, run `brew install uv`. The root workspace uses `members = ["packages/*"]`, so a new `packages/<name>/pyproject.toml` needs no root edit. Develop in a local venv until S2's root workspace is on `main`.

**Distribution names:** `aad-design` (`packages/platform`, provides the `aad` command and depends on the others), `aad-engine` (imports as `aad`), `aad-render`, `aad-drafting`, `aad-quality`. To depend on another workspace package, list it in `dependencies` and add `[tool.uv.sources] <name> = { workspace = true }`.

**Pinned versions** (raise only with a changelog entry): `build123d==0.13.0`, `cadquery-ocp-novtk==8.0.1.1.0`, `mcp==2.3.0`, `ezdxf==1.4.5`, `three-cad-viewer@5.1.3`, `@modelcontextprotocol/ext-apps@2.0.4`.

---

## 2. Project on disk

```
<root>/<project>/
  project.json     {id, name, kind, units, process, head_rev, created, aad_version}
  model.py         current source
  spec.yaml        requirements
  exports/         files for the user
  .aad/
    revs/000007/   model.py, spec.yaml, meta.json, part.brep, scene.bin.gz, scene.json, sheet.png
    cache/         content-addressed, safe to delete
    jobs/          job dirs, the only place sandboxed code may write
```

`<root>` comes from MCP roots, else `~/AAD`. `model.py` and `spec.yaml` should work well in git. `.aad/` is a cache plus history and is listed in a generated `.gitignore`.

Global state: `~/.aad/` containing `config.toml`, `run/aadd.sock`, `run/aadd.json` (pid, http port, token, version), `logs/`.

---

## 3. `model.py`

```python
from aad import *                 # build123d plus the helpers below

P = params(width=60.0, depth=40.0, t=6.0, hole_d=4.4, spacing=(46.0, 26.0))
meta(name="fan_bracket", units="mm", material="steel_1018", process="cnc_3axis")

with part("bracket") as bp:
    with feature("plate"):
        Box(P.width, P.depth, P.t)

    with feature("mount_holes", intent="M4 clearance"):
        top = face(feature="plate", where="max_z", expect=1)
        with Locations(top):
            with GridLocations(*P.spacing, 2, 2):
                Hole(radius=P.hole_d / 2)

    with feature("edge_round"):
        fillet(edge(feature="plate", where="parallel_z", expect=4), radius=2.0)
```

The wildcard modeling API also exports `gear_profile`, `gear_solid`,
`gear_center_distance`, `helix_angle_for_center_distance`, `gear_mesh_phase`,
and `tapered_bevel_standin`. Their angles are in degrees; gear module is the
normal module. Geometry assumptions and limits are documented in
`packages/engine/modeling.md`.

- `params()` declares every adjustable number; overrides come from tools. A param may be a `Param(value, min, max, unit)` so that sliders get ranges.
- `feature(name)` must be unique. It records created and modified faces and edges, source lines and dependencies.
- `face()`, `edge()`, `vertex()` resolve semantically. `expect=n` turns a count mismatch into a `SELECTOR_AMBIGUOUS` or `SELECTOR_EMPTY` failure with candidates.
- A script produces one `part`, one `assembly` (assign `assembly()` to `result`), or (for `kind: "2d"`) one `sheet2d`.
- The script must not do I/O. Files come in through `asset("name")`, which reads from the project's imports.
- Plain build123d is valid, and everything lands in an implicit `main` feature. Named features are what make edits reliable.

---

## 4. `spec.yaml`

```yaml
units: mm
process: fdm_pla              # optional: fdm_pla, sla, cnc_3axis, sheet_metal, laser, injection
material: pla                 # optional
requirements:
  - id: overall_size
    bbox: {x: 60 ± 0.1, y: 40 ± 0.1}
  - id: one_body
    solids: 1
  - id: mount_holes
    holes: {count: 4, diameter: 4.4 ± 0.1, pattern: {grid: [46, 26]}}
  - id: stiffness_wall
    min_wall: 2.0
  - id: motor_clearance
    clearance: {with: assets/motor.step, min: 0.3}
  - id: matches_reference
    matches_reference: {path: assets/ref.step, f_score: {min: 0.99, tau_pct_of_diagonal: 0.1}}
  - id: mass_limit
    mass_g: {max: 120}
  - id: mirror_x
    symmetric: {plane: YZ, tolerance: 0.01}
  - id: custom_fit
    level: warn               # error (default) or warn
    measure: {kind: distance, a: "face(feature='a', where='max_z')", b: "face(feature='b', where='min_z')"}
    equals: 12 ± 0.05
  - id: no_interference
    interference: {volume_threshold_mm3: 1e-6}
```

`matches_reference` compares the built model to a project-contained STEP, STL, PLY, or OBJ surface. It uses area-weighted fixed-seed samples and exact point-to-triangle distances after proper rigid registration (determinant +1; no scaling or mirroring). The default threshold is F@0.1% of the reference bbox diagonal; `f_score.min` is required and `tau_pct_of_diagonal` defaults to `0.1`. `aad compare <project> <reference> [--rev N] [--tau-pct 0.1] [--json]` is a CLI operation through the sandboxed worker, not a model-visible MCP tool.

S5 owns the evaluator and may add requirement types. Unknown types return a `skip` check, never a silent pass.
The optional `interference` requirement runs the same exact solid-pair check as
`aad_check(suites=["interference"])`. It accepts an optional
`volume_threshold_mm3` (default `1e-6`); a common volume must be strictly above
that threshold to count as interference. The provider uses a 60-second default
pair-check budget, reports at most the 20 largest overlaps plus bounded unknown
pair details, and identifies any unchecked pairs. A boolean failure or an
exhausted budget cannot produce a pass.

---

## 5. MCP tools

All tools accept `project` unless noted and return the envelope in section 6. Inputs are shown as TypeScript types. Names are final; adding optional fields is fine.

### Model-visible

```ts
aad_new({ name: string, kind?: "part"|"assembly"|"2d", units?: "mm"|"in",
          process?: string, material?: string, spec?: string /*yaml*/, source?: string })

aad_build({ project, base_rev?: number,
            source?: string,                       // full model.py, or
            patch?: string,                        // unified diff against base_rev, or
            params?: Record<string, number|string>,// overrides, or combine with the above
            spec?: string,
            preview?: boolean,                     // adds a 4-up sheet image, default false
            dry_run?: boolean,                     // full build and checks, no new head
            wait_s?: number,                       // default 30; returns status "running" with job id
            timeout_s?: number })                  // worker kill limit, default 60, max 600

aad_inspect({ project, rev?: number,
              what?: "summary"|"features"|"params"|"topology"|"bom"|"diff", against?: number })

aad_query({ project, rev?: number, selector: string, expect?: number, limit?: number })

aad_measure({ project, rev?: number,
              kind: "distance"|"angle"|"radius"|"length"|"area"|"volume"|"bbox"|"center_of_mass"|"mass"|"inertia"|"principal_inertia"|"thickness"|"clearance",
              a?: string, b?: string })             // selectors

aad_check({ project, rev?: number,
            suites?: ("hard"|"spec"|"process"|"interference"|"2d")[] })

aad_render({ project, rev?: number,
             views?: ("sheet"|"iso"|"front"|"back"|"left"|"right"|"top"|"bottom"|"custom")[],
             style?: "shaded"|"lines"|"xray",
             section?: { plane: "XY"|"XZ"|"YZ"|string, offset?: number },
             labels?: "none"|"features"|"faces"|"edges",
             heatmap?: "thickness"|"overhang"|"draft"|"curvature",
             highlight?: string[],                  // selectors
             camera?: { direction: number[3], up?: number[3], target?: number[3], span?: number },
             focus?: string|string[]|{ selector?: string|string[], min?: number[3], max?: number[3],
                                      margin?: number, clip?: boolean },
             dimensions?: "none"|"overall"|"auto",
             probes?: ({ image: string, x: number, y: number }|
                       { point: number[3], direction: number[3] })[],
             measure?: { a: string, b: string }[],
             heatmap_range?: number[2], min_wall?: number, direction?: number[3],
             size?: number })                       // default 1024, max 2048

aad_show({ project, rev?: number, compare_to?: number, title?: string,
           view?: "iso"|"front"|"rear"|"left"|"right"|"top"|"bottom",
           presentation?: ViewerPresentation, views?: NamedView[] }) // the only tool with _meta.ui

aad_drawing({ project, rev?: number, sheet?: "A4"|"A3"|"A2"|"A1"|"letter"|"tabloid",
              views?: DrawingView[], dimensions?: "auto"|"none"|DimensionSpec[],
              style?: "iso"|"asme", title_block?: Record<string,string>, formats?: ("svg"|"dxf"|"pdf")[],
              notes?: string[], revisions?: Record<string,string>[], bom?: boolean })

aad_history({ project, action?: "list"|"diff"|"checkout"|"branch"|"job",
              rev?: number, against?: number, name?: string, job?: string })

aad_import({ project?: string, path: string, name?: string, as?: "base"|"reference" })

aad_export({ project, rev?: number,
             formats: ("step"|"iges"|"stl"|"obj"|"ply"|"3mf"|"glb"|"brep"|"dxf"|"svg"|"pdf"|"viewer_html")[],
             options?: Record<string, unknown>,     // e.g. tolerance
             allow_unverified?: boolean })          // otherwise only verified revisions export

aad_library({ action: "search"|"get", kind?: "part"|"material"|"playbook"|"api",
              query?: string, id?: string })        // no project
```

### App-only

These use `_meta.ui.visibility: ["app"]`. Their descriptions start with "Internal viewer tool. Do not call." in case a host ignores visibility.

```ts
aad_scene_read({ scene_id: string, offset: number, length?: number })
  // -> structuredContent {data_b64, offset, byte_count, total_bytes, has_more}

aad_set_param({ project, base_rev: number, name: string, value: number|string })
  // -> a new draft revision and its SceneManifest

aad_pick_info({ project, rev: number, part: string, kind: "face"|"edge"|"vertex", id: number })
  // -> {selector, feature, geometry: {type, center, normal, radius, area, length}}

aad_file_read({ project, path: string, offset: number, length?: number })
  // -> same chunk format; only files under exports/
```

### UI declaration

```jsonc
// on aad_show
"_meta": { "ui": { "resourceUri": "ui://aad/viewer" } }

// resources/read of ui://aad/viewer
{ "uri": "ui://aad/viewer", "mimeType": "text/html;profile=mcp-app", "text": "<single-file html>" }
```

Always register these. Do not gate on the host's capability advert. Omit `_meta.ui.csp` so that the strict default applies.

---

## 6. Result envelope

`content[0]` is text that makes sense alone, around 200 tokens. It holds status, the key numbers, failed checks and the next step. Images go in `content` only when requested. `structuredContent` holds this envelope, and it must stay small: no meshes, no base64.

```ts
type Envelope = {
  ok: boolean, project: string, rev?: number, base_rev?: number, head_rev?: number,
  status: "failed"|"draft"|"verified"|"running",
  job?: string,
  summary: string,
  metrics?: {
    units: "mm"|"in", bbox: [number, number, number], volume?: number, area?: number,
    center_of_mass?: [number, number, number], mass_g?: number,
    solids: number, faces: number, edges: number, valid: boolean, build_ms: number
  },
  checks?: Check[],                 // failures and warnings first, passes collapsed to counts
  checks_summary?: { pass: number, fail: number, warn: number, skip: number },
  failure?: Failure,
  artifacts?: Record<string, string>,  // aad://<project>/<rev>/<name>, plus file paths for exports
  scene?: SceneManifest,               // aad_show, aad_set_param
  viewer?: { mode: "inline"|"window"|"none", url?: string, view?: string,
             title?: string, presentation?: ViewerPresentation, views?: NamedView[] },
  user_selection?: Selection[],        // picks from window mode since the last call
  next?: string[],                     // at most 3 short hints
  // Tool-specific payloads, each small and only on its own tool's result:
  entities?: unknown[],                // aad_query
  measurement?: unknown,               // aad_measure
  topology?: unknown,                  // aad_inspect what: "topology"
  features?: unknown[],                // aad_inspect what: "features"
  params?: unknown,                    // aad_inspect what: "params"
  diff?: { source: string, params: unknown, metrics: unknown },  // aad_history, aad_inspect
  imported?: unknown,                  // aad_import
  library?: unknown                    // aad_library
}

type Check = {
  id: string, suite: "hard"|"spec"|"process"|"interference"|"2d",
  level: "error"|"warn", status: "pass"|"fail"|"warn"|"skip",
  message: string, expected?: string, actual?: string,
  feature?: string, selector?: string, heuristic?: boolean, assumptions?: string, fix_hint?: string
}

type Failure = {
  code: string,
  phase: "input"|"sandbox"|"syntax"|"runtime"|"selector"|"kernel"|"validate"|"timeout"|"oom"|"crash",
  message: string,                  // our words, says what to do
  kernel_message?: string,          // verbatim
  feature?: string, file?: string, line?: number,
  selector?: string, candidates?: Candidate[],
  probes?: { param: string, value: number, ok: boolean }[],  // actually tested values only
  repair?: string[],
  last_good_rev?: number
}
```

**Failure codes:** `SYNTAX_ERROR`, `RUNTIME_ERROR`, `SANDBOX_DENIED`, `SELECTOR_EMPTY`, `SELECTOR_AMBIGUOUS`, `KERNEL_BOOLEAN_FAILED`, `KERNEL_FILLET_FAILED`, `KERNEL_CHAMFER_FAILED`, `KERNEL_SHELL_FAILED`, `KERNEL_SWEEP_FAILED`, `KERNEL_OTHER`, `INVALID_SOLID`, `TIMEOUT`, `OUT_OF_MEMORY`, `WORKER_CRASH`, `BASE_REV_CONFLICT`, `NOT_VERIFIED`, `BAD_INPUT`, `IMPORT_FAILED`, `EXPORT_FAILED`.

**Honesty rules:** unknown stays `unknown` or is omitted. Never invent a limit, an edge name or a cause. `probes` holds only values actually tested. A high reference F-score measures geometric closeness to that reference only; it proves nothing about function and is meaningless if the reference was traced into the model. Two scans of one object scored F@0.1%D = 0.958 with volumetric IoU = 0.925, showing that scan noise can keep the score below 0.99. Scores also depend on which surfaces each side contains, including internal parts, cast detail, and scan coverage. Closed-solid-only metrics such as IoU are reported as unavailable when closure is not established.

**`verified`** means the hard gate passes, at least one requirement exists, and no `error`-level check fails. Every `error`-level requirement must also have been evaluated: an `error`-level `skip` or `warn`, a missing result, a duplicate requirement ID, a malformed spec, a non-finite measurement or an evaluator exception leaves the revision `draft`.

---

## 7. Scene

```ts
type SceneManifest = {
  scene_id: string,                 // sha256 of the compressed payload
  project: string, rev: number, kind: "3d"|"2d",
  format: "tcv-5"|"svg-1",
  encoding: "gzip",
  units: "mm"|"in", bbox: [number, number, number, number, number, number],
  total_bytes: number, triangles?: number,
  parts: { path: string, name: string, feature?: string, color?: string }[],
  params?: { name: string, value: number|string, min?: number, max?: number, unit?: string }[],
  checks_summary?: { pass: number, fail: number, warn: number, skip: number },
  failed_features?: string[],
  status: "draft"|"verified",
  compare?: SceneManifest
}
```

- **`tcv-5`:** gzip of the JSON shapes tree that three-cad-viewer 5.x takes, plus `feature_map: { [partPath]: { faces: string[], edges: string[] } }` mapping each face and edge index to a feature name.
- **`svg-1`:** gzip of JSON `{ svg: string, sheet: {w, h, units}, layers: string[], entities: Entity[] }`.

**Transfer:** the viewer calls `aad_scene_read`. Chunks are 128 KiB of compressed bytes before base64, at most 4 in flight. The viewer checks `total_bytes` and `scene_id` and supports retrying a chunk. S1 ships sample scenes under `fixtures/scenes/` as soon as S3 needs them; until then S3 generates its own with ocp-tessellate.

---

## 8. Viewer behavior

The viewer only displays. Chat supports whole-part hover and click selection;
clicking an already selected part deselects it. Highlighting preserves the
part's material and visible detail. Double-click prepares an exact part reference
for the user's real chat prompt: append an unsent draft item only when the host
advertises a draft-insertion capability; otherwise leave prompt insertion
unavailable. Do not offer a clipboard workflow, open a separate editor, or
automatically send a message.
Native CAD picking and mesh measurement remain local inspection tools.
Model-editing sliders, `aad_set_param`, `aad_pick_info`, and `POST /context` stay
reserved.

**User controls:** pull apart/put together, XYZ grid, reset to the agent-prepared
view, fullscreen, and a wider Views menu beside the small title-row icons. In
chat, CAD panels and action shortcuts stay hidden. The local CAD window retains
the native engine's tree, visibility, clipping/caps, measurements/properties,
selection, cameras, animation, zebra, materials, studio, and Z scale tools.
Standalone exports can select `controls: "cad"`; HTML chat exports always use
simple controls. No model-editing tools are called by these controls.

```ts
type ViewerPresentation = {
  isolate?: string[]; hide?: string[]; // exact part names or leaf/group paths from scene.parts
  style?: "shaded-edges"|"shaded"|"wireframe"|"transparent";
  projection?: "orthographic"|"perspective";
  section?: { planes: (({ plane: "XY"|"XZ"|"YZ" }|{ normal: [number,number,number] }) &
                      { offset?: number, flipped?: boolean })[],
              intersection?: boolean }; // 1–3 planes
  explode?: boolean; grid?: boolean;
  inside?: { hide?: string[], section?: ViewerPresentation["section"] };
  // At least one of hide/section. Declare only for actual internal components or a cavity.
};
type NamedView = {
  id: string; label: string; description?: string;
  view?: "iso"|"front"|"rear"|"left"|"right"|"top"|"bottom";
  presentation?: Omit<ViewerPresentation,"inside">;
  analysis?: { quantity: string, units: string, source: string,
               values: {part: string, value: number}[], range?: [number,number] };
};
```

Plane offsets are signed distances from the model center in model units.
Invalid/ambiguous part names, zero normals, and non-finite offsets return
`BAD_INPUT`. Presentation changes keep the original scene ID and bytes. A viewer
must apply new settings for the same scene without fetching its mesh again.
Reset restores the selected recipe's part filter and section. Local windows receive
presentation updates through the existing WebSocket result envelope.
Each named recipe is independent of the base presentation and other recipes;
missing fields use normal whole-model defaults. Original restores the base.
Up to 24 recipes and 64 KiB of normalized metadata are accepted. IDs/labels are
unique; `original`/Original are reserved. An empty list clears recipes. New
results preserve the selected ID if it still exists. Legacy `inside` metadata
becomes one Interior menu entry; the separate Inside button is removed.

Analysis is supplied component-level scalar data, not a solver or pointwise
field. Require quantity, units, actual source, finite unique part values, and a
range containing all values (inferred when omitted). The viewer changes material
colors and shows a legend; switching back restores materials without changing
geometry. Agent skill/playbook and MCP instructions describe both interfaces,
all native capabilities, and analysis provenance requirements.

**Transport interface** (S3 defines it in TypeScript; there is one UI over three implementations):

```ts
interface Transport {
  readonly controls?: "simple"|"cad";
  readonly caps: { fullscreen: boolean, modelContext?: boolean, composerReferences?: boolean };
  getInitialResult(): Promise<Envelope>;
  readChunk(sceneId: string, offset: number, length: number): Promise<Chunk>;
  callTool(name: string, args: object): Promise<Envelope>;
  setModelContext(ctx: object): Promise<void>;   // window mode posts to the daemon
  sendMessage(text: string): Promise<void>;      // hidden if the host lacks it
  requestFullscreen(): Promise<void>;            // only from a user gesture
  onUpdate(cb: (e: Envelope) => void): void;     // new revisions, window mode
  stageSelection?(references: PartReference[]): Promise<void>; // negotiated MCP context only
  insertPartReference?(reference: PartReference): Promise<void>; // negotiated unsent active-composer append only
}
```

**MCP App constraints:**
- One HTML file, at most 3 MiB.
- No network, WASM, Web Workers, `eval`, `new Function`, `localStorage`, `sessionStorage` or cookies. Assume an opaque origin.
- Each app-to-host message must stay under 256 KiB.
- Request sizing via `ui/notifications/size-changed`. Declare `availableDisplayModes: ["inline", "fullscreen"]`.
- Read `hostCapabilities` and hide controls for missing features.

**Selection sent to the model:**

```ts
type PartReference = { project: string, rev: number, scene_id: string,
                       path: string, name: string, mention: string };
type Selection = { project: string, rev: number, kind: "face"|"edge"|"vertex"|"part",
                   selector: string, feature?: string,
                   geometry: { type: string, center?: number[], normal?: number[],
                               radius?: number, area?: number, length?: number } }
```

`ui/update-model-context` carries a short text description plus `structuredContent: { selection: Selection[] }`. Each update replaces the last.

Chat part selection uses this channel only when the host advertises it. A part's
`selector` is an identity URI, `aad://part/<project>/<rev>/<scene_id>?path=<path>`,
with project/path percent-encoded. Resolve it against the exact saved scene
before choosing modeling selectors; mention labels alone do not identify parts
and are not file paths. The native composer item retains its `@name` title.
HTML-only hosts without a draft API keep selection local and leave prompt
insertion unavailable.
On hosts advertising `experimental["openai/message"]`, `ui/message` may append a
titled text reference to the active composer with
`_meta: {"openai/message": {target: "active", send: false}}`. Gate this extension
before calling; ordinary `ui/message` can send immediately and is not a fallback.
Host rejection reports failure without a clipboard or sending fallback.
The viewer never promises composer focus or insertion on unsupported hosts.
Selection context is cleared on deselection or a new scene/revision. Large
selections may be bounded with explicit `selectedCount` and `omittedCount`;
omitted references must be obtained before acting on those parts.

**Local window:** `GET http://127.0.0.1:<port>/p/<project>?token=<token>`; the daemon sends WebSocket updates on `/ws`. Tokens come from `~/.aad/run/aadd.json`. The daemon rejects a missing token or a foreign `Origin`.

The window transport talks to these same-origin routes:

- `GET /p/<project>?token=<token>` returns the viewer HTML with `window.__AAD_WINDOW__ = {project, token}` set before the app script runs.
- `POST /rpc` with `Authorization: Bearer <token>` and body `{method, params}` returns `{content, structuredContent, isError}`, shaped like an MCP tool result. Allowed methods: `aad_show` and the four app-only tools.
- `POST /context` with `Authorization: Bearer <token>` and body `{project, selection: Selection[], text?}` replaces the stored selection. It is attached as `user_selection` to the next tool result for that project, then cleared.
- `GET /ws?token=<token>&project=<project>` is a WebSocket that sends `{type: "result", envelope: Envelope}` for each new revision of that project.

The daemon also rejects any `Host` other than `127.0.0.1:<port>` or `localhost:<port>`, which blocks DNS rebinding.

---

## 9. Python interfaces between packages

S1 publishes `aad.engine.types` within its first hour; others import from it. Workers call these in-process. The platform calls workers via `aad.engine.jobs`.

```python
# aad.engine.types
@dataclass
class ModelContext:
    project_dir: Path; rev: int; kind: str; units: str
    shape: "Shape | None"            # part or assembly
    sheet: "Sheet2D | None"          # kind == "2d"
    features: list[FeatureInfo]      # name, faces, edges, line range, deps
    params: dict[str, Any]
    metrics: dict[str, Any]
    spec: dict[str, Any]
    material: str | None
    process: str | None
    job_dir: Path                    # the only writable path

# S1
def aad.engine.jobs.run_build(req: BuildRequest) -> BuildResult
def aad.engine.gate.hard_checks(ctx: ModelContext) -> list[Check]
def aad.engine.select.resolve(ctx, selector: str, expect: int | None = None) -> list[Entity]

# S3
def aad_render.make_scene(ctx: ModelContext, out: Path, compare: ModelContext | None = None) -> SceneManifest
def aad_render.render(ctx: ModelContext, req: RenderRequest, out: Path) -> list[Path]

# S4
def aad_drafting.make_drawing(ctx: ModelContext, req: DrawingRequest, out: Path) -> DrawingResult
def aad_drafting.checks(ctx: ModelContext) -> list[Check]
def aad_drafting.make_scene(ctx: ModelContext, out: Path) -> SceneManifest   # format svg-1

# S5
def aad_quality.spec_checks(ctx: ModelContext) -> list[Check]
def aad_quality.process_checks(ctx: ModelContext) -> list[Check]
def aad_quality.library(action: str, **kw) -> dict
```

**Worker pipeline:** run `model.py` → `hard_checks` → `spec_checks` → `process_checks` (or `aad_drafting.checks` for 2D) → `make_scene` → optional `render` → return `BuildResult`. A failure in an optional step becomes a `warn` check, and must never discard a built revision.

`render` returns PNG paths plus `inspection.json` and optional `labels.json`. The platform emits
only PNGs as image blocks; it includes the JSON data in structured `inspection` and `labels`
fields, with all file paths in `artifacts`. Inspection version 1 includes B-rep face/edge facts,
mass properties, exact section loops, image pane scales, world-to-pixel matrices, visible
selectors, probe results and measurements. Face/edge indexes belong to the current revision.
Exact sharp/tangent/seam edge classification and adjacent-face lists are included up to 2,048
edges. Above that explicit limit, curve facts remain available, edge classes are omitted, and
`inspection.edge_classification` says so; rendered edges use the general edge style.
Camera direction points toward the viewer; probe direction points into the scene. Pixel probes
use whole-image coordinates and view names without `.png`. Thickness is a sampled mesh search
with B-rep refinement of its smallest candidates, not proof of a global minimum. See
`packages/render/README.md` for field semantics and limitations.

---

## 10. Daemon RPC

JSON-RPC 2.0, newline-delimited, over `~/.aad/run/aadd.sock` (named pipe on Windows). The first call is `hello {adapter_version, protocol: 1}`. On a mismatch the adapter restarts the daemon. Each MCP tool maps to one method of the same name with the same arguments, plus `client: {name, version, supports_ui: boolean}`.

The daemon serializes jobs per project and runs different projects in parallel. Default pool: 2 warm workers, one spare.

---

## 11. Changelog

- 2026-11-05: S3 specified whole-part toggle selection, subtle highlighting, exact scene-scoped references, negotiated selection context and capability-gated unsent composer insertion; unsupported hosts keep local selection without a clipboard workflow, and server editing routes remain reserved.
- 2026-11-05: Replaced `PLAN.md` with `ARCHITECTURE.md` at the user's request. Scope, sector and milestone text remains in git history.
- 2026-11-05: S5 added rigid reference-model comparison as a spec requirement and sandboxed CLI operation, with explicit geometric-only evidence limits.
- 2026-11-05: S1/S3/S5 added an opt-in interference spec requirement with a reported mm³ threshold, bounded pair details and explicit unknowns, and documented the render edge-classification cap so large assemblies can render without dropping B-rep curve facts.
- 2026-11-05: S1 added the first-party gear helpers to the `from aad import *` modeling API, with degree-based angles and normal module.
- 2026-11-05: Added optional `timeout_s` to `aad_build` in section 5, because the fixed 60 second kill limit stopped a full gearbox assembly that needed about 80 seconds.
- 2026-11-05: S2/S3 added optional agent-controlled viewer presentations to `aad_show` and its result, following the user’s request for simple user controls and detailed views prepared through chat.
- 2026-11-05: S2/S3 added optional `presentation.inside` to describe a real interior view and conditionally expose an inside/outside button, at the user's request.
- 2026-11-05: S2/S3 added `aad_show.views`, sourced component analysis display, and full native CAD controls outside chat; replaced the separate Inside button with curated Views, at the user's request.
- 2026-11-05: S1/S2/S4/S5 added CAD capabilities, additively: `aad.cad` helpers including `assembly()` and `asset()`; `kind: "2d"` builds; `aad_inspect what: "bom"`; `aad_measure` kinds `length`, `bbox`, `center_of_mass`, `principal_inertia`, with `mass` and `thickness` now implemented; `aad_export` formats `iges`, `obj`, `ply`; IGES and DXF/SVG import; `aad_drawing` `notes`, `revisions`, `bom`. `aad-design` now depends on `aad-quality[parts]`. See `docs/cad-parity.md`.
- 2026-11-04: Marked the viewer as display-only in section 8, following the user's direction in issue #35. No interface was removed.
- 2026-11-04: S2 added optional tool-specific fields to the envelope in section 6, so that results of query, measure, inspect, history, import and library have named places.
- 2026-11-04: S2 specified the window transport's routes in section 8, because S3's transport and S2's server need the same paths and shapes.
- 2026-11-04: S2 tightened `verified` in section 6 (issue #5), because an untested `error`-level requirement must never pass as proven.
- 2026-11-04: S2 added distribution names to section 1, because workspace packages must not collide and `uv tool install aad-design` must get the CLI.
- 2026-11-04: S1 removed the mandatory stop-and-ask GitHub access note at the user's request; agents may run repository connection and authentication setup themselves.
- 2026-11-04: v0 written with `PLAN.md`.
- 2026-11-04: renamed to AAD. Switched from a shared working tree to per-sector worktrees and PRs into `main`. Cross-sector messages moved to GitHub issues.
