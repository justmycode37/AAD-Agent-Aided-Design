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
import shutil
import stat
import sys
from collections.abc import Callable, Mapping
from numbers import Real
from pathlib import Path
from typing import Any

RESULT_MARKER = b"AAD_OPS_RESULT:"
NOT_READY = "NOT_READY"
PREPARED_RENDER_FILENAME = "prepared-render.json"


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
    sheet = None
    if (rev_dir / "sheet.json").exists():
        sheet = json.loads((rev_dir / "sheet.json").read_text())
    assembly = (meta.get("meta") or {}).get("assembly")
    if shape is not None and assembly:
        shape = _restore_assembly(shape, assembly)
    return ModelContext(
        project_dir=project_dir,
        rev=request["rev"],
        kind=request.get("kind", "part"),
        units=request.get("units", "mm"),
        shape=shape,
        sheet=sheet,
        features=features,
        params=meta.get("params") or {},
        metrics=meta.get("metrics") or {},
        spec=spec,
        material=request.get("material") or (meta.get("meta") or {}).get("material"),
        process=request.get("process"),
        job_dir=Path(request["out_dir"]),
        meta=meta.get("meta") or {},
    )


def _restore_assembly(shape: Any, assembly: dict[str, Any]) -> Any:
    """BREP stores no tree or names, so rebuild the recorded components, in order, on the same geometry."""
    from build123d import Compound

    names = assembly.get("components") or []
    children = list(shape) if isinstance(shape, Compound) else []
    if not names or len(names) != len(children):
        return shape
    numbers = {
        name: row.get("part_number")
        for row in assembly.get("bom") or []
        for name in row.get("components") or []
    }
    # Assembly.bom() refused differing geometry under one number, so a number is one part.
    representative: dict[str, Any] = {}
    for name, child in zip(names, children, strict=True):
        number = numbers.get(name) or name
        child.label = name
        child.part_number = number
        child.obj = representative.setdefault(number, child)
    return Compound(label=assembly.get("name") or "assembly", children=children)


def _sheet_preview(ctx: Any, out: Path) -> dict[str, Any]:
    make_drawing = _required("aad_drafting", "make_drawing", "Sheet preview")
    drawing = _plain(make_drawing(ctx, {"formats": ["svg"]}, out))
    preview = drawing.get("preview")
    return {
        "images": [{"path": str(preview), "mime": "image/png"}] if preview else [],
        "artifacts": {k: str(v) for k, v in (drawing.get("artifacts") or {}).items()},
    }


def op_render(ctx: Any, args: dict[str, Any], out: Path) -> dict[str, Any]:
    if ctx.kind == "2d":
        return _sheet_preview(ctx, out)
    render = _required("aad_render", "render", "Agent rendering")
    request = _render_request(args)
    paths = render(ctx, request, out)
    return _render_result(paths)


def op_render_prepare(ctx: Any, args: dict[str, Any], _out: Path) -> dict[str, Any]:
    """Prepare 3D render data while the B-rep is still inside the sandbox."""
    if ctx.kind == "2d":
        raise ValueError("2D sheet previews do not use the 3D mesh render worker")
    prepare_render = _required("aad_render", "prepare_render", "Agent render preparation")
    prepared = prepare_render(ctx, _render_request(args))
    from aad_render.render_limits import (
        MAX_INSPECTION_JSON_BYTES,
        MAX_PREPARED_RENDER_BYTES,
        MAX_RENDER_PAYLOAD_BYTES,
        json_size_limited,
    )

    if not isinstance(prepared, dict) or set(prepared) != {"version", "render", "inspection"}:
        raise ValueError("render preparation returned a malformed bundle")
    json_size_limited(prepared["inspection"], MAX_INSPECTION_JSON_BYTES, "render inspection")
    render_bytes = json.dumps(
        {"render": prepared["render"]}, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")
    if len(render_bytes) > MAX_RENDER_PAYLOAD_BYTES:
        raise ValueError(f"render payload exceeds the {MAX_RENDER_PAYLOAD_BYTES}-byte limit")

    # The large mesh bypasses worker stdout and the parent's JSON decoder. The
    # fixed sidecar lives in the operation's write-only job directory; stdout
    # returns just the exact report and the request needed to finish it.
    render_request = prepared["render"].get("request")
    if not isinstance(render_request, dict):
        raise ValueError("render preparation returned no render request")
    metadata = {
        "version": prepared["version"],
        "render": {"request": render_request},
        "inspection": prepared["inspection"],
        "render_payload_file": PREPARED_RENDER_FILENAME,
    }
    json_size_limited(metadata, MAX_PREPARED_RENDER_BYTES, "prepared render result")
    _write_prepared_render_payload(_out, render_bytes)
    return {"prepared_render": metadata}


def _write_prepared_render_payload(out: Path, data: bytes) -> None:
    """Create the fixed mesh sidecar without following or replacing a link."""
    nofollow = getattr(os, "O_NOFOLLOW", None)
    if nofollow is None:
        raise ValueError("safe render payload writes require O_NOFOLLOW support")
    path = Path(out) / PREPARED_RENDER_FILENAME
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | nofollow, 0o600)
    try:
        remaining = memoryview(data)
        while remaining:
            written = os.write(fd, remaining)
            if written <= 0:
                raise OSError("could not write the prepared render payload")
            remaining = remaining[written:]
    except BaseException:
        os.close(fd)
        path.unlink(missing_ok=True)
        raise
    else:
        os.close(fd)


def _render_request(args: dict[str, Any]) -> Any:
    from aad.engine.types import RenderRequest

    known = {f.name for f in dataclasses.fields(RenderRequest)}
    return RenderRequest(**{k: v for k, v in args.items() if k in known and v is not None})


def _render_result(paths: list[Path]) -> dict[str, Any]:
    from aad_render.render_limits import MAX_INSPECTION_JSON_BYTES, MAX_LABELS_JSON_BYTES

    artifacts = {path.name: str(path) for path in paths}
    result = {
        "images": [{"path": str(path), "mime": "image/png"} for path in paths if path.suffix == ".png"],
        "artifacts": artifacts,
    }
    if "inspection.json" in artifacts:
        result["inspection"] = _read_limited_json(Path(artifacts["inspection.json"]), MAX_INSPECTION_JSON_BYTES)
    if "labels.json" in artifacts:
        result["labels"] = _read_limited_json(Path(artifacts["labels.json"]), MAX_LABELS_JSON_BYTES)
    return result


def _read_limited_json(path: Path, maximum: int) -> Any:
    """Read a renderer artifact without following symlinks or trusting its length."""
    nofollow = getattr(os, "O_NOFOLLOW", None)
    if nofollow is None:
        raise ValueError("safe render artifact reads require O_NOFOLLOW support")
    fd = os.open(path, os.O_RDONLY | nofollow)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_size > maximum:
            raise ValueError(f"render artifact {path.name!r} is not a regular file within its size limit")
        chunks = bytearray()
        while len(chunks) <= maximum:
            chunk = os.read(fd, min(64 * 1024, maximum + 1 - len(chunks)))
            if not chunk:
                break
            chunks.extend(chunk)
        if len(chunks) > maximum:
            raise ValueError(f"render artifact {path.name!r} exceeds the {maximum}-byte limit")
        return json.loads(bytes(chunks), parse_constant=_reject_json_constant)
    finally:
        os.close(fd)


def _reject_json_constant(token: str) -> None:
    raise ValueError(f"non-finite JSON number {token} is not allowed")


def op_scene(ctx: Any, args: dict[str, Any], out: Path) -> dict[str, Any]:
    make_scene = _required("aad_render", "make_scene", "Scene generation")
    return {"scene": _plain(make_scene(ctx, out))}


def op_drawing(ctx: Any, args: dict[str, Any], out: Path) -> dict[str, Any]:
    make_drawing = _required("aad_drafting", "make_drawing", "Drawing generation")
    revision_status = args.pop("_revision_status", None)
    request = {k: v for k, v in args.items() if v is not None}
    result = make_drawing(ctx, request, out, revision_status=revision_status)
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
        if ctx.kind == "2d" and suite in {"hard", "process", "interference"}:
            checks.append(
                {
                    "id": f"{suite}.requires_3d_shape",
                    "suite": suite,
                    "level": "warn",
                    "status": "skip",
                    "message": f"The {suite} suite requires a 3D solid B-rep and was skipped for this 2D sheet.",
                    "fix_hint": "Use suite '2d' for sheet geometry; use a part or assembly project for 3D checks.",
                }
            )
            continue
        if ctx.kind != "2d" and suite == "2d":
            checks.append(
                {
                    "id": "2d.requires_sheet",
                    "suite": suite,
                    "level": "warn",
                    "status": "skip",
                    "message": "The 2d suite requires a 2D sheet and was skipped for this 3D project.",
                    "fix_hint": "Use a project created with kind='2d' to check sheet contours.",
                }
            )
            continue
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
            if ctx.kind == "2d" and suite == "2d":
                checks.extend(_plain(function(ctx, limits=_sheet_laser_limits(ctx))))
            else:
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


def _sheet_laser_limits(ctx: Any) -> dict[str, Any] | None:
    """Translate laser process settings into native-unit limits for 2D cutouts."""
    spec = getattr(ctx, "spec", None)
    if not isinstance(spec, Mapping):
        spec = {}
    process = getattr(ctx, "process", None) or spec.get("process")
    if not isinstance(process, str) or process.strip().lower() not in {"laser", "laser_cutting", "laser_cut"}:
        return None

    settings = spec.get("process_settings")
    if not isinstance(settings, Mapping):
        return {}
    millimeters_per_unit = 25.4 if getattr(ctx, "units", None) in {"in", "inch", "inches"} else 1.0
    limits: dict[str, Any] = {}
    raw_kerf = settings.get("kerf_mm")
    raw_minimum = settings.get("minimum_feature_mm")

    def native_length(value: Any) -> Any:
        if isinstance(value, Real) and not isinstance(value, bool):
            return float(value) / millimeters_per_unit
        return value

    if raw_kerf is not None:
        limits["kerf"] = native_length(raw_kerf)
    if raw_kerf is not None or raw_minimum is not None:
        if (
            (raw_kerf is None or isinstance(raw_kerf, Real) and not isinstance(raw_kerf, bool))
            and (raw_minimum is None or isinstance(raw_minimum, Real) and not isinstance(raw_minimum, bool))
        ):
            kerf = float(raw_kerf or 0.0) / millimeters_per_unit
            minimum = float(raw_minimum or 0.0) / millimeters_per_unit
            limits["min_slot"] = max(2.0 * kerf, minimum)
        else:
            limits["min_slot"] = native_length(raw_minimum if raw_minimum is not None else raw_kerf)
    return limits


def op_query(ctx: Any, args: dict[str, Any], out: Path) -> dict[str, Any]:
    resolve = _required("aad.engine.select", "resolve", "Selector queries")
    entities = resolve(ctx, args["selector"], args.get("expect"))
    limit = args.get("limit")
    items = entities[:limit] if limit else entities
    return {"count": len(entities), "entities": _plain(items)}


_CM3_PER_UNIT3 = {"mm": 1e-3, "in": 2.54**3}


def _density(material_id: str) -> tuple[float, dict[str, Any]]:
    get_material = _required("aad_quality.materials", "get_material", "The material catalog")
    material = get_material(material_id)
    if not material:
        raise OpFailure("BAD_INPUT", "input", f"Material {material_id!r} has no sourced density. Search aad_library kind material.")
    return float(material["density_g_cm3"]), material


def _measure_mass(ctx: Any) -> dict[str, Any]:
    """Nominal, fully dense mass from sourced densities. Without a material, no mass is claimed."""
    factor = _CM3_PER_UNIT3[ctx.units]
    rows = (ctx.meta.get("assembly") or {}).get("bom") or []
    grams = 0.0
    breakdown = []
    if rows:
        for row in rows:
            material = row.get("material") or ctx.material
            if not material:
                raise OpFailure("BAD_INPUT", "input", f"Component {row['part_number']!r} has no material, so no mass is claimed.")
            line = row["unit_volume"] * row["quantity"] * factor * _density(material)[0]
            grams += line
            breakdown.append({"part_number": row["part_number"], "material": material, "quantity": row["quantity"], "mass_g": line})
    else:
        if not ctx.material:
            raise OpFailure("BAD_INPUT", "input", "Mass needs a declared material with a sourced density. Set material in aad_new or meta().")
        volume = sum(float(solid.volume) for solid in ctx.shape.solids())
        grams = volume * factor * _density(ctx.material)[0]
    result = {"kind": "mass", "value": grams, "unit": "g", "definition": "nominal_fully_dense_volume_times_sourced_density"}
    if ctx.material:
        result["material"] = ctx.material
    if breakdown:
        result["breakdown"] = breakdown
    return result


def _measure_thickness(ctx: Any) -> dict[str, Any]:
    screen = _required("aad_quality.process_geometry", "wall_thickness_screen", "Wall thickness")
    if ctx.shape is None or len(ctx.shape.solids()) != 1:
        raise OpFailure("BAD_INPUT", "input", "Wall thickness needs exactly one solid. For an assembly, measure a part project.")
    walls = screen(ctx.shape, units=ctx.units, tolerance_mm=1e-3)
    mm_per_unit = {"mm": 1.0, "in": 25.4}[ctx.units]
    return {
        "kind": "thickness",
        "value": walls.minimum_mm / mm_per_unit,
        "unit": ctx.units,
        "definition": "smallest_sampled_wall_not_a_proven_global_minimum",
        "samples_hit": walls.samples_hit,
        "samples_missed": walls.samples_missed,
        "sample_limit_reached": walls.sample_limit_reached,
    }


def op_measure(ctx: Any, args: dict[str, Any], out: Path) -> dict[str, Any]:
    kind = args["kind"]
    if ctx.kind == "2d":
        if kind not in {"area", "bbox"} or args.get("a") or args.get("b"):
            raise OpFailure("BAD_INPUT", "input", "A 2D sheet supports whole-sheet area and bbox measurements.")
        evidence = _required("aad_drafting", "measurement_evidence", "2D measurement")
        return {"measurement": _plain({"kind": kind, "evidence": evidence(ctx)})}
    if ctx.shape is not None and not args.get("a") and not args.get("b"):
        if kind == "mass":
            return {"measurement": _plain(_measure_mass(ctx))}
        if kind == "thickness":
            return {"measurement": _plain(_measure_thickness(ctx))}
    measure = _required("aad.engine.measure", "measure", "Measurement")
    return {"measurement": _plain(measure(ctx, kind, args.get("a"), args.get("b")))}


def op_compare(ctx: Any, args: dict[str, Any], out: Path) -> dict[str, Any]:
    if ctx.shape is None:
        raise OpFailure("BAD_INPUT", "input", "This revision has no 3D model shape to compare.")
    try:
        from aad_quality.compare import ComparisonTimeout, compare_shape_to_reference

        metrics = compare_shape_to_reference(
            ctx.shape,
            args["reference_path"],
            model_units=ctx.units,
            reference_units=ctx.units,
            tau_pct=args.get("tau_pct", 0.1),
            seed=7,
            time_budget_s=100.0,
            compute_iou=True,
        )
    except ComparisonTimeout as exc:
        raise OpFailure("TIMEOUT", "timeout", str(exc)) from exc
    except Exception as exc:
        from aad_quality.compare import ComparisonError

        if isinstance(exc, ComparisonError):
            raise OpFailure("BAD_INPUT", "validate", str(exc)) from exc
        raise
    return {"comparison": metrics}


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


def _export_sheet(ctx: Any, args: dict[str, Any], out: Path) -> dict[str, Any]:
    files: dict[str, str] = {}
    for fmt in args["formats"]:
        if fmt in {"dxf", "svg"}:
            export_2d = _required("aad_drafting", "export_2d", "2D export")
            files[fmt] = str(export_2d(ctx, fmt, out / f"{args['stem']}.{fmt}"))
        elif fmt == "pdf":
            make_drawing = _required("aad_drafting", "make_drawing", "PDF export")
            files[fmt] = str(_plain(make_drawing(ctx, {"formats": ["pdf"]}, out)).get("artifacts", {})["pdf"])
        elif fmt == "viewer_html":
            files[fmt] = _viewer_page(ctx, out / f"{args['stem']}.viewer.html")
        else:
            raise OpFailure("EXPORT_FAILED", "input", f"A 2D sheet exports DXF, SVG, PDF or viewer_html, not {fmt}. Extrude it in a part project for 3D formats.")
    return {"files": files}


def _viewer_page(ctx: Any, path: Path) -> str:
    """A single self-contained page that opens from disk with the revision's own scene."""
    from .viewer.bundle import export_bundle

    scene_dir = Path(ctx.project_dir) / ".aad" / "revs" / f"{ctx.rev:06d}" / "scene"
    if not (scene_dir / "scene.bin.gz").is_file():
        raise OpFailure("EXPORT_FAILED", "input", "This revision has no saved scene, so no standalone viewer can be written.")
    working = path.parent / f".{path.name}.bundle"
    export_bundle(scene_dir, working)
    os.replace(working / "standalone.html", path)
    shutil.rmtree(working, ignore_errors=True)
    return str(path)


def op_export(ctx: Any, args: dict[str, Any], out: Path) -> dict[str, Any]:
    if ctx.kind == "2d":
        return _export_sheet(ctx, args, out)
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
            path = Path(_viewer_page(ctx, out / f"{stem}.viewer.html"))
        elif engine_export is not None:
            path = Path(engine_export(ctx, fmt, path, **options))
        else:
            _export_kernel(ctx, fmt, path, options)
        files[fmt] = str(path)
    return {"files": files}


def op_import(ctx: Any, args: dict[str, Any], out: Path) -> dict[str, Any]:
    if Path(args["path"]).suffix.lower() in {".dxf", ".svg"}:
        drafting = importlib.import_module("aad_drafting")
        import_path = Path(args["path"])
        parse = drafting.parse_dxf if import_path.suffix.lower() == ".dxf" else drafting.parse_svg
        sheet = parse(import_path)
        return {"imported": {"format": Path(args["path"]).suffix[1:].lower(), "role": args.get("as", "base"),
                             "entities": len(sheet.entities), "layers": sorted(sheet.layers),
                             "history_notice": (
                                 f"Imported as editable sheet entities. In a kind='2d' project, use "
                                 f"result = asset({import_path.name!r}).to_sheet() in model.py."
                             )}}
    importer = _required("aad.engine.fileio", "import_file", "Import")
    imported = importer(args["path"], out, name=args.get("name"), role=args.get("as", "base"))
    shape = imported.context.shape
    return {"imported": {
        "format": imported.source_format, "role": imported.role, "units": imported.context.units,
        "source_units": imported.source_units, "assumed_units": imported.source_units_assumed,
        "solids": len(shape.solids()), "faces": len(shape.faces()), "history_notice": imported.history_notice,
    }}


def op_library(ctx: Any, args: dict[str, Any], out: Path) -> dict[str, Any]:
    library = _required("aad_quality", "library", "The library")
    action = args.pop("action")
    return {"library": _plain(library(action, **{k: v for k, v in args.items() if v is not None}))}


OPS: dict[str, Callable[[Any, dict[str, Any], Path], dict[str, Any]]] = {
    "render": op_render,
    "render_prepare": op_render_prepare,
    "scene": op_scene,
    "drawing": op_drawing,
    "check": op_check,
    "query": op_query,
    "measure": op_measure,
    "compare": op_compare,
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
        if op == "render_prepare":
            if not isinstance(payload, dict):
                raise ValueError("render preparation returned a malformed result")
            result: dict[str, Any] = {"ok": True, **payload}
        else:
            result = {"ok": True, **_plain(payload)}
    except OpFailure as exc:
        result = {"ok": False, "failure": exc.failure}
    except ImportError as exc:
        result = {
            "ok": False,
            "failure": {"code": NOT_READY, "phase": "input", "message": f"A required package is missing: {exc}"},
        }
    except Exception as exc:
        result = {"ok": False, "failure": _classify(exc)}
    os.write(protocol, RESULT_MARKER + json.dumps(result, default=str, separators=(",", ":")).encode() + b"\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
