"""`python -m aad_platform.ops`: one post-build operation in its own kernel process.

Reads one JSON request on stdin and writes one marked JSON line on stdout. Holds no kernel
logic of its own beyond file export: it loads a revision and calls the sectors' public functions.
A function that does not exist yet reports that plainly, and nothing is guessed.
"""

from __future__ import annotations

import dataclasses
import importlib
import json
import os
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

RESULT_MARKER = b"AAD_OPS_RESULT:"
NOT_READY = "NOT_READY"


class OpFailure(Exception):
    def __init__(self, code: str, phase: str, message: str, **extra: Any):
        super().__init__(message)
        self.failure = {"code": code, "phase": phase, "message": message, **extra}


def _optional(module: str, name: str) -> Callable[..., Any] | None:
    try:
        return getattr(importlib.import_module(module), name)
    except (ImportError, AttributeError):
        return None


def _required(module: str, name: str, what: str) -> Callable[..., Any]:
    function = _optional(module, name)
    if function is None:
        raise OpFailure(NOT_READY, "input", f"{what} is not available yet: {module}.{name} is not installed.")
    return function


def _plain(value: Any) -> Any:
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return _plain(dataclasses.asdict(value))
    if isinstance(value, dict):
        return {str(k): _plain(v) for k, v in value.items()}
    if isinstance(value, list | tuple | set):
        return [_plain(v) for v in value]
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, str | int | bool) or value is None:
        return value
    if isinstance(value, float):
        return value if value == value and abs(value) != float("inf") else None
    return str(value)


def _feature(item: dict[str, Any]) -> Any:
    """Rebuild FeatureInfo with real Entity records, so selectors keep named ownership."""
    from aad.engine.types import Entity, FeatureInfo

    known = {f.name for f in dataclasses.fields(FeatureInfo)}
    entity_fields = {f.name for f in dataclasses.fields(Entity)}
    values = {k: v for k, v in item.items() if k in known}
    for key in ("faces", "edges", "vertices"):
        if key in values:
            values[key] = [
                Entity(**{k: v for k, v in entity.items() if k in entity_fields}) if isinstance(entity, dict) else entity
                for entity in values[key]
            ]
    return FeatureInfo(**values)


def load_context(request: dict[str, Any]) -> Any:
    from aad.engine.types import ModelContext

    project_dir = Path(request["project_dir"])
    rev_dir = project_dir / ".aad" / "revs" / f"{request['rev']:06d}"
    meta = json.loads((rev_dir / "meta.json").read_text())
    shape = None
    brep = rev_dir / "part.brep"
    if brep.exists():
        from build123d import import_brep

        shape = import_brep(str(brep))
    spec: dict[str, Any] = {}
    if (rev_dir / "spec.yaml").exists():
        from .specs import parse_spec

        spec = parse_spec((rev_dir / "spec.yaml").read_text())
    features = [_feature(item) for item in meta.get("features") or []]
    return ModelContext(
        project_dir=project_dir,
        rev=request["rev"],
        kind=request.get("kind", "part"),
        units=request.get("units", "mm"),
        shape=shape,
        sheet=None,
        features=features,
        params=meta.get("params") or {},
        metrics=meta.get("metrics") or {},
        spec=spec,
        material=request.get("material"),
        process=request.get("process"),
        job_dir=Path(request["out_dir"]),
    )


def op_render(ctx: Any, args: dict[str, Any], out: Path) -> dict[str, Any]:
    render = _required("aad_render", "render", "Agent rendering")
    from aad.engine.types import RenderRequest

    known = {f.name for f in dataclasses.fields(RenderRequest)}
    request = RenderRequest(**{k: v for k, v in args.items() if k in known and v is not None})
    paths = render(ctx, request, out)
    artifacts = {path.name: str(path) for path in paths}
    result = {
        "images": [{"path": str(path), "mime": "image/png"} for path in paths if path.suffix == ".png"],
        "artifacts": artifacts,
    }
    if "inspection.json" in artifacts:
        result["inspection"] = json.loads(Path(artifacts["inspection.json"]).read_text())
    if "labels.json" in artifacts:
        result["labels"] = json.loads(Path(artifacts["labels.json"]).read_text())
    return result


def op_scene(ctx: Any, args: dict[str, Any], out: Path) -> dict[str, Any]:
    make_scene = _required("aad_render", "make_scene", "Scene generation")
    return {"scene": _plain(make_scene(ctx, out))}


def op_drawing(ctx: Any, args: dict[str, Any], out: Path) -> dict[str, Any]:
    make_drawing = _required("aad_drafting", "make_drawing", "Drawing generation")
    request = {k: v for k, v in args.items() if v is not None}
    result = make_drawing(ctx, request, out)
    return {"drawing": _plain(result)}


def op_check(ctx: Any, args: dict[str, Any], out: Path) -> dict[str, Any]:
    suites = args.get("suites") or (["2d"] if ctx.kind == "2d" else ["hard", "spec", "process"])
    sources: dict[str, tuple[str, str]] = {
        "hard": ("aad.engine.gate", "hard_checks"),
        "spec": ("aad_quality", "spec_checks"),
        "process": ("aad_quality", "process_checks"),
        "2d": ("aad_drafting", "checks"),
        "interference": ("aad.engine.gate", "interference_checks"),
    }
    checks: list[dict[str, Any]] = []
    for suite in suites:
        function = _optional(*sources[suite])
        if function is None:
            checks.append(
                {
                    "id": f"{suite}_suite_unavailable",
                    "suite": suite,
                    "level": "warn",
                    "status": "skip",
                    "message": f"The {suite} suite is not available in this install.",
                }
            )
            continue
        try:
            checks.extend(_plain(function(ctx)))
        except Exception as exc:  # A suite that crashed is not a suite that passed.
            checks.append(
                {
                    "id": f"{suite}_suite_failed",
                    "suite": suite,
                    "level": "error",
                    "status": "fail",
                    "message": f"The {suite} suite raised {type(exc).__name__}: {exc}",
                }
            )
    return {"checks": checks}


def op_query(ctx: Any, args: dict[str, Any], out: Path) -> dict[str, Any]:
    resolve = _required("aad.engine.select", "resolve", "Selector queries")
    entities = resolve(ctx, args["selector"], args.get("expect"))
    limit = args.get("limit")
    items = entities[:limit] if limit else entities
    return {"count": len(entities), "entities": _plain(items)}


def op_measure(ctx: Any, args: dict[str, Any], out: Path) -> dict[str, Any]:
    measure = _required("aad.engine.measure", "measure", "Measurement")
    return {"measurement": _plain(measure(ctx, args["kind"], args.get("a"), args.get("b")))}


def op_pick_info(ctx: Any, args: dict[str, Any], out: Path) -> dict[str, Any]:
    pick_info = _required("aad.engine.select", "pick_info", "Pick resolution")
    return {"pick": _plain(pick_info(ctx, args["part"], args["kind"], args["id"]))}


def op_topology(ctx: Any, args: dict[str, Any], out: Path) -> dict[str, Any]:
    shape = ctx.shape
    if shape is None:
        raise OpFailure("BAD_INPUT", "input", "This revision has no 3D shape.")
    return {
        "topology": {
            "solids": len(shape.solids()),
            "shells": len(shape.shells()),
            "faces": len(shape.faces()),
            "edges": len(shape.edges()),
            "vertices": len(shape.vertices()),
        }
    }


def _export_kernel(ctx: Any, fmt: str, path: Path, options: dict[str, Any]) -> None:
    import build123d as bd

    shape = ctx.shape
    if shape is None:
        raise OpFailure("EXPORT_FAILED", "input", f"{fmt} export needs a 3D shape, and this revision has none.")
    tolerance = float(options.get("tolerance", 1e-3))
    angular = float(options.get("angular_tolerance", 0.1))
    if fmt == "step":
        ok = bd.export_step(shape, str(path))
    elif fmt == "brep":
        ok = bd.export_brep(shape, str(path))
    elif fmt == "stl":
        ok = bd.export_stl(shape, str(path), tolerance=tolerance, angular_tolerance=angular)
    elif fmt == "glb":
        ok = bd.export_gltf(shape, str(path), binary=True, linear_deflection=tolerance, angular_deflection=angular)
    elif fmt == "3mf":
        mesher = bd.Mesher()
        mesher.add_shape(shape, linear_deflection=tolerance, angular_deflection=angular)
        mesher.write(str(path))
        ok = True
    else:
        raise OpFailure("EXPORT_FAILED", "input", f"Unknown export format {fmt!r}.")
    if ok is False or not path.exists() or path.stat().st_size == 0:
        raise OpFailure("EXPORT_FAILED", "kernel", f"The kernel did not write a usable {fmt} file.")


def op_export(ctx: Any, args: dict[str, Any], out: Path) -> dict[str, Any]:
    files: dict[str, str] = {}
    options = args.get("options") or {}
    stem = args["stem"]
    engine_export = _optional("aad.engine.fileio", "export")
    drawing_formats = [fmt for fmt in args["formats"] if fmt in ("dxf", "svg", "pdf")]
    if drawing_formats:
        make_drawing = _required("aad_drafting", "make_drawing", "DXF, SVG and PDF export")
        result = make_drawing(ctx, {"formats": drawing_formats}, out)
        artifacts = _plain(getattr(result, "artifacts", {}) or {})
        for fmt in drawing_formats:
            if fmt not in artifacts:
                raise OpFailure("EXPORT_FAILED", "kernel", f"The drawing step did not produce a {fmt} file.")
            files[fmt] = artifacts[fmt]
    for fmt in args["formats"]:
        if fmt in drawing_formats:
            continue
        path = out / f"{stem}.{fmt}"
        if fmt == "viewer_html":
            exporter = _required("aad_render", "export_viewer_html", "Standalone viewer export")
            path = out / f"{stem}.viewer.html"
            exporter(ctx, path)
        elif engine_export is not None:
            path = Path(engine_export(ctx, fmt, path, **options))
        else:
            _export_kernel(ctx, fmt, path, options)
        files[fmt] = str(path)
    return {"files": files}


def op_import(ctx: Any, args: dict[str, Any], out: Path) -> dict[str, Any]:
    importer = _required("aad.engine.fileio", "import_file", "Import")
    return {"imported": _plain(importer(args["path"], out, name=args.get("name"), role=args.get("as", "base")))}


def op_library(ctx: Any, args: dict[str, Any], out: Path) -> dict[str, Any]:
    library = _required("aad_quality", "library", "The library")
    action = args.pop("action")
    return {"library": _plain(library(action, **{k: v for k, v in args.items() if v is not None}))}


OPS: dict[str, Callable[[Any, dict[str, Any], Path], dict[str, Any]]] = {
    "render": op_render,
    "scene": op_scene,
    "drawing": op_drawing,
    "check": op_check,
    "query": op_query,
    "measure": op_measure,
    "pick_info": op_pick_info,
    "topology": op_topology,
    "export": op_export,
}
CONTEXT_FREE = {"library": op_library, "import": op_import}


def _classify(exc: BaseException) -> dict[str, Any]:
    name = type(exc).__name__
    code = getattr(exc, "code", None)
    if isinstance(code, str) and code.isupper():
        failure = {"code": code, "phase": getattr(exc, "phase", "selector"), "message": str(exc)}
        for key in ("candidates", "selector"):
            if getattr(exc, key, None):
                failure[key] = _plain(getattr(exc, key))
        return failure
    return {"code": "RUNTIME_ERROR", "phase": "runtime", "message": f"{name}: {exc}"}


def main() -> int:
    # Native libraries print to fd 1. Keep the result line clean by moving them to stderr.
    protocol = os.dup(1)
    os.dup2(2, 1)
    request = json.load(sys.stdin)
    out = Path(request["out_dir"])
    out.mkdir(parents=True, exist_ok=True)
    op = request["op"]
    try:
        if op in CONTEXT_FREE:
            payload = CONTEXT_FREE[op](None, dict(request["args"]), out)
        else:
            payload = OPS[op](load_context(request), dict(request["args"]), out)
        result: dict[str, Any] = {"ok": True, **_plain(payload)}
    except OpFailure as exc:
        result = {"ok": False, "failure": exc.failure}
    except ImportError as exc:
        result = {
            "ok": False,
            "failure": {"code": NOT_READY, "phase": "input", "message": f"A required package is missing: {exc}"},
        }
    except Exception as exc:
        result = {"ok": False, "failure": _classify(exc)}
    os.write(protocol, RESULT_MARKER + json.dumps(result, default=str).encode() + b"\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
