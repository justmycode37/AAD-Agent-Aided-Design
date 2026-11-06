"""Private, one-request worker entry point used by :mod:`aad.engine.jobs`."""

from __future__ import annotations

import contextlib
import importlib
import json
import math
import sys
import time
import traceback
from dataclasses import asdict, fields, is_dataclass, replace
from pathlib import Path
from types import ModuleType
from typing import Any

import aad.cad as cad
from aad.cad import configure_assets
from aad._runtime import (
    ModelInputError, configure_params, current_param_definitions, current_params, validate_params,
)
from aad.modeling import (
    configure_modeling, context_metadata, current_part_name, finalize_modeling, named_shape, record_operation,
)
from .gate import _hard_checks_with_timings
from .memory import is_memory_exhaustion
from .select import SelectorError
from .types import (
    BuildResult,
    Check,
    Failure,
    ModelContext,
    SceneManifest,
)

_RESULT_MARKER = "AAD_ENGINE_RESULT:"
_LOG_LIMIT = 32_768


class _TailTextStream:
    """Text sink retaining only a bounded tail of model output."""

    def __init__(self, limit: int = _LOG_LIMIT) -> None:
        self._limit = limit
        self._text = ""

    def write(self, value: str) -> int:
        self._text = (self._text + value)[-self._limit :]
        return len(value)

    def flush(self) -> None:
        return None

    def getvalue(self) -> str:
        return self._text


def main() -> None:
    """Read one JSON request and write a single marked JSON result."""
    try:
        request_line = sys.stdin.buffer.readline()
        payload = json.loads(request_line.decode("utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("worker request must be a JSON object")
    except BaseException as exc:
        result = _result_wire(
            BuildResult(
                ok=False,
                status="failed",
                summary="The worker could not decode the JSON build request.",
                failure=Failure(
                    code="BAD_INPUT",
                    phase="input",
                    message="The worker could not process the JSON build request.",
                    kernel_message=f"{type(exc).__name__}: {exc}",
                ),
            )
        )
    else:
        try:
            result = _build(payload)
        except BaseException as exc:
            # An unexpected engine exception is not evidence of an OOM or any
            # other particular cause. Keep an already-written BREP visible.
            job_dir = Path(payload.get("job_dir", "."))
            artifacts = _preserved_artifacts(job_dir)
            failure = _memory_failure(exc) if is_memory_exhaustion(exc) else Failure(
                code="WORKER_CRASH",
                phase="crash",
                message="The worker encountered an unexpected internal error; no cause was inferred.",
                kernel_message=f"{type(exc).__name__}: {exc}",
            )
            result = _result_wire(
                BuildResult(
                    ok=False,
                    status="failed",
                    summary=failure.message,
                    failure=failure,
                    artifacts=artifacts,
                    job_dir=job_dir,
                )
            )
    sys.stdout.write("\n" + _RESULT_MARKER + json.dumps({"protocol": 1, "result": result}, default=_json_default) + "\n")
    sys.stdout.flush()


def _build(payload: dict[str, Any]) -> dict[str, Any]:
    started = time.perf_counter()
    phase_timings: dict[str, float] = {}
    job_dir = Path(payload["job_dir"])
    project_dir = Path(payload["project_dir"])
    source_path = job_dir / "model.py"
    source = payload["source"]
    stdout_stream = _TailTextStream()
    stderr_stream = _TailTextStream()

    try:
        phase_started = time.perf_counter()
        source_path.write_text(source, encoding="utf-8")
        _add_phase_time(phase_timings, "write", phase_started)
        phase_started = time.perf_counter()
        code = compile(source, str(source_path), "exec")
        _add_phase_time(phase_timings, "model", phase_started)
    except SyntaxError as exc:
        failure = Failure(
            code="SYNTAX_ERROR",
            phase="syntax",
            message="The model script has a syntax error at the reported source line.",
            kernel_message=exc.msg,
            file=source_path.name,
            line=exc.lineno,
            repair=["Correct the syntax at the reported model.py line and rebuild."],
        )
        return _result_wire(_failed_result(failure, elapsed_ms=_elapsed_ms(started), job_dir=job_dir))
    except OSError as exc:
        failure = Failure(
            code="BAD_INPUT",
            phase="input",
            message="The model source could not be written into the job directory.",
            kernel_message=str(exc),
            file=source_path.name,
        )
        return _result_wire(_failed_result(failure, elapsed_ms=_elapsed_ms(started), job_dir=job_dir))

    try:
        import build123d
    except Exception as exc:
        if is_memory_exhaustion(exc):
            return _result_wire(_failed_result(_memory_failure(exc, file=source_path.name),
                                               elapsed_ms=_elapsed_ms(started), job_dir=job_dir))
        failure = Failure(
            code="IMPORT_FAILED",
            phase="kernel",
            message="The build123d kernel could not be imported in the worker.",
            kernel_message=f"{type(exc).__name__}: {exc}",
            file=source_path.name,
        )
        return _result_wire(_failed_result(failure, elapsed_ms=_elapsed_ms(started), job_dir=job_dir))

    configure_modeling(source)
    configure_assets(job_dir / "assets", payload.get("units", "mm"))
    tracked_build_parts: list[Any] = []
    original_build_part = build123d.BuildPart

    # Generic operations in build123d 0.13 validate the builder class name.
    class BuildPart(original_build_part):
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            super().__init__(*args, **kwargs)
            # build123d 0.13 records its caller's Python frame to decide
            # whether nested builders publish to the same construction scope.
            # This tracking constructor adds one frame; preserve the frame a
            # plain BuildPart would have recorded instead.
            self._python_frame = sys._getframe(1)
            self._aad_completed = False
            self._aad_nested = False

        def __exit__(self, exception_type: Any, exception_value: Any, traceback: Any) -> Any:
            # A failed reentry must not retain an earlier successful outcome.
            self._aad_completed = False
            result = super().__exit__(exception_type, exception_value, traceback)
            self._aad_completed = exception_type is None
            # The original exit has restored the enclosing builder context.
            # Record actual nesting independently of same-scope publication:
            # helper functions can have an enclosing context but no parent.
            self._aad_nested = original_build_part._get_context() is not None
            if self._aad_completed:
                tracked_build_parts.append(self)
            return result

        def _add_to_context(self, *args: Any, **kwargs: Any) -> Any:
            before = self._obj
            result = super()._add_to_context(*args, **kwargs)
            record_operation(self, before, self._obj)
            return result

    # Scripts that import BuildPart from aad or build123d receive this
    # subclass. It records builder operations and supplies a completed part
    # when the script does not assign `result`.
    build123d.BuildPart = BuildPart
    aad = __import__("aad")
    namespace: dict[str, Any] = {
        "__name__": "__aad_model__",
        "__file__": str(source_path),
    }
    for name in dir(build123d):
        if not name.startswith("_"):
            namespace[name] = getattr(build123d, name)
    for name in ("Param", "params", "meta", "part", "feature", "face", "edge", "vertex", "entity_shape", "SelectorError"):
        namespace[name] = getattr(aad, name)
    for name in cad.__all__:
        namespace[name] = getattr(cad, name)

    model_phase_started = time.perf_counter()
    try:
        with contextlib.redirect_stdout(stdout_stream), contextlib.redirect_stderr(stderr_stream):
            configure_params(payload.get("params", {}))
            exec(code, namespace, namespace)
            validate_params()
            metadata = context_metadata(payload)
    except BaseException as exc:
        if is_memory_exhaustion(exc):
            file_name, line = _source_location(exc, source_path)
            failure = _memory_failure(exc, file=file_name, line=line)
        elif isinstance(exc, SyntaxError):
            failure = Failure(
                code="SYNTAX_ERROR",
                phase="syntax",
                message="The model script has a syntax error at the reported source line.",
                kernel_message=exc.msg,
                file=source_path.name,
                line=exc.lineno,
                repair=["Correct the syntax at the reported model.py line and rebuild."],
            )
        elif isinstance(exc, (SelectorError, ModelInputError)):
            file_name, line = _source_location(exc, source_path)
            failure = exc.failure if isinstance(exc, SelectorError) else Failure(
                code="BAD_INPUT", phase="input", message=str(exc),
            )
            failure.file = failure.file or file_name
            failure.line = failure.line or line
            failure.feature = failure.feature or getattr(exc, "_aad_feature", None)
        else:
            file_name, line = _source_location(exc, source_path)
            kernel_code = _kernel_failure_code(exc)
            where = f" at {file_name}:{line}" if file_name and line else ""
            failure = Failure(
                code=kernel_code or "RUNTIME_ERROR",
                phase="kernel" if kernel_code else "runtime",
                message=(
                    f"The kernel refused this operation{where}: {type(exc).__name__}."
                    if kernel_code
                    else f"The model script raised {type(exc).__name__}{where}."
                ),
                kernel_message=str(exc) or type(exc).__name__,
                file=file_name,
                line=line,
                feature=getattr(exc, "_aad_feature", "main" if file_name else None),
                repair=_KERNEL_REPAIRS.get(
                    kernel_code or "", ["Inspect the reported source line and the exception detail, then rebuild."]
                ),
            )
        result = _failed_result(
            failure,
            elapsed_ms=_elapsed_ms(started),
            job_dir=job_dir,
            stdout=stdout_stream.getvalue(),
            stderr=stderr_stream.getvalue(),
            metrics={"phase_timings_ms": _rounded_timings(phase_timings)},
        )
        _add_phase_time(phase_timings, "model", model_phase_started)
        result.metrics["phase_timings_ms"] = _rounded_timings(phase_timings)
        return _result_wire(result)
    finally:
        build123d.BuildPart = original_build_part

    if payload.get("kind") == "2d":
        return _finish_sheet(
            namespace, payload, metadata, source_path, started, stdout_stream, stderr_stream, phase_timings,
        )

    model_phase_started = time.perf_counter()
    try:
        candidate = namespace.get("result")
        if isinstance(candidate, cad.Assembly):
            metadata["assembly"] = candidate.record()
            namespace["result"] = candidate.compound()
        shape = _extract_shape(namespace, tracked_build_parts, build123d.Shape)
    except (TypeError, ValueError) as exc:
        _add_phase_time(phase_timings, "model", model_phase_started)
        failure = Failure(
            code="BAD_INPUT",
            phase="validate",
            message=str(exc),
            file=source_path.name,
            repair=["Assign a build123d Shape to `result` or create geometry in a BuildPart context."],
        )
        return _result_wire(
            _failed_result(
                failure,
                elapsed_ms=_elapsed_ms(started),
                job_dir=job_dir,
                stdout=stdout_stream.getvalue(),
                stderr=stderr_stream.getvalue(),
                metrics={"phase_timings_ms": _rounded_timings(phase_timings)},
            )
        )
    else:
        _add_phase_time(phase_timings, "model", model_phase_started)

    artifacts: dict[str, str] = {}
    brep_path = job_dir / "part.brep"
    write_started = time.perf_counter()
    try:
        exported = bool(build123d.export_brep(shape, brep_path))
        if not exported or not brep_path.is_file() or brep_path.stat().st_size == 0:
            raise OSError("build123d did not create a non-empty BREP file")
        artifacts["part.brep"] = str(brep_path)
    except Exception as exc:
        if is_memory_exhaustion(exc):
            return _result_wire(
                _failed_result(_memory_failure(exc, file="part.brep"), elapsed_ms=_elapsed_ms(started),
                               job_dir=job_dir, stdout=stdout_stream.getvalue(), stderr=stderr_stream.getvalue())
            )
        failure = Failure(
            code="EXPORT_FAILED",
            phase="kernel",
            message="The built shape could not be saved as part.brep.",
            kernel_message=f"{type(exc).__name__}: {exc}",
            file="part.brep",
        )
        return _result_wire(
            _failed_result(
                failure,
                elapsed_ms=_elapsed_ms(started),
                job_dir=job_dir,
                stdout=stdout_stream.getvalue(),
                stderr=stderr_stream.getvalue(),
                metrics={"phase_timings_ms": _rounded_timings(phase_timings)},
            )
        )
    finally:
        _add_phase_time(phase_timings, "write", write_started)

    params = current_params()
    if payload.get("params") and not params:
        # Overrides without a params() declaration were not applied.
        params_warning = Check(
            id="params_not_applied",
            suite="hard",
            level="warn",
            status="warn",
            message="Parameter overrides were supplied, but the model did not declare parameters with params().",
        )
    else:
        params_warning = None

    model_phase_started = time.perf_counter()
    build_ms = _elapsed_ms(started)
    metrics = _shape_metrics(shape, build123d, metadata.get("units", "mm"), build_ms)
    features, _ = finalize_modeling(shape)
    context = ModelContext(
        project_dir=project_dir,
        rev=payload.get("rev", 0),
        kind=payload.get("kind", "part"),
        units=metadata.get("units", "mm"),
        shape=shape,
        features=features,
        params=params,
        metrics=metrics,
        spec=payload.get("spec", {}),
        material=metadata.get("material"),
        process=metadata.get("process"),
        job_dir=job_dir,
        meta=metadata,
        param_definitions=current_param_definitions(),
        part_name=current_part_name(),
    )
    _add_phase_time(phase_timings, "model", model_phase_started)

    checks, gate_timings = _hard_checks_with_timings(context)
    phase_timings.update(gate_timings)
    if params_warning is not None:
        checks.append(params_warning)
    checks.extend(_param_provenance_warnings(context.param_definitions))

    phase_started = time.perf_counter()
    spec_checks, spec_hook_available = _optional_checks(
        module_name="aad_quality",
        function_name="spec_checks",
        context=context,
        suite="spec",
        unavailable_id="spec_checks_unavailable",
        unavailable_message="Specification checks are unavailable because aad_quality is not installed.",
    )
    _add_phase_time(phase_timings, "spec", phase_started)
    checks.extend(spec_checks)

    phase_started = time.perf_counter()
    process_checks, process_hook_available = _optional_checks(
        module_name="aad_quality",
        function_name="process_checks",
        context=context,
        suite="process",
        unavailable_id="process_checks_unavailable",
        unavailable_message="Process checks are unavailable because aad_quality is not installed.",
    )
    _add_phase_time(phase_timings, "process", phase_started)
    checks.extend(process_checks)

    phase_started = time.perf_counter()
    scene, scene_check = _optional_scene(context)
    _add_phase_time(phase_timings, "scene", phase_started)
    checks.append(scene_check)

    phase_started = time.perf_counter()
    render_artifacts, render_check = _optional_render(context, bool(payload.get("preview", False)))
    _add_phase_time(phase_timings, "render", phase_started)
    checks.append(render_check)
    artifacts.update(render_artifacts)

    requirements_proven, coverage_check = _requirements_proven(
        context.spec,
        spec_checks,
        hook_available=spec_hook_available,
        params_applied=params_warning is None,
    )
    if coverage_check is not None:
        checks.append(coverage_check)

    hard_failed = any(check.suite == "hard" and check.level == "error" and check.status == "fail" for check in checks)
    quality_failed = any(
        check.suite in {"spec", "process", "2d"} and check.level == "error" and check.status == "fail"
        for check in checks
    )
    process_proven = (
        context.process is None
        or (
            process_hook_available
            and not any(check.status == "skip" for check in process_checks)
            and all(check.status == "pass" for check in process_checks if check.level == "error")
        )
    )
    if hard_failed:
        failed_check = next(
            check for check in checks if check.suite == "hard" and check.level == "error" and check.status == "fail"
        )
        failure = Failure(
            code="HARD_GATE_FAILED",
            phase="validate",
            message=f"Hard geometry check '{failed_check.id}' failed: {failed_check.message}",
            kernel_message=failed_check.actual,
            feature="main",
            file=source_path.name,
        )
        status = "failed"
        ok = False
        summary = "A BREP artifact was saved, but the hard geometry gate failed."
    elif quality_failed:
        failed_ids = [check.id for check in checks if check.level == "error" and check.status == "fail"]
        failure = Failure(
            code="REQUIREMENT_FAILED",
            phase="validate",
            message=f"The model built, but these error-level checks failed: {', '.join(failed_ids)}.",
            feature="main",
            file=source_path.name,
            repair=[
                "Read expected and actual in each failed check, and fix the model if it misses the request.",
                "If the dimension was meant to change, send the updated spec in the same aad_build call.",
                "Inspect the kept candidate with aad_history(action='job').",
            ],
        )
        status = "failed"
        ok = False
        summary = "A BREP artifact was saved, but one or more error-level quality checks failed."
    elif requirements_proven and process_proven and all(
        check.status == "pass" for check in checks if check.suite == "hard" and check.level == "error"
    ):
        failure = None
        status = "verified"
        ok = True
        summary = "The BREP passed the hard gate and every supplied requirement was checked."
    else:
        failure = None
        status = "draft"
        ok = True
        summary = "The model built and passed the available hard checks; requirements are not fully proven."

    metrics["phase_timings_ms"] = _rounded_timings(phase_timings)
    result = BuildResult(
        ok=ok,
        status=status,
        summary=summary,
        context=context,
        metrics=metrics,
        checks=checks,
        failure=failure,
        artifacts=artifacts,
        scene=scene,
        job_dir=job_dir,
        build_ms=_elapsed_ms(started),
        stdout=stdout_stream.getvalue(),
        stderr=stderr_stream.getvalue(),
        requirements_proven=requirements_proven,
    )
    result.checks_summary = _checks_summary(checks)
    return _result_wire(result)


def _finish_sheet(
    namespace: dict[str, Any],
    payload: dict[str, Any],
    metadata: dict[str, Any],
    source_path: Path,
    started: float,
    stdout_stream: Any,
    stderr_stream: Any,
    phase_timings: dict[str, float],
) -> dict[str, Any]:
    """Finish a kind='2d' build: save the Sheet2D, run the 2D suite and write its scene."""
    job_dir = Path(payload["job_dir"])

    def failed(code: str, message: str, kernel: str | None = None) -> dict[str, Any]:
        failure = Failure(code=code, phase="validate", message=message, kernel_message=kernel, file=source_path.name,
                          repair=["Create a sheet with sheet2d() from aad_drafting, or assign one to `result`."])
        return _result_wire(_failed_result(failure, elapsed_ms=_elapsed_ms(started), job_dir=job_dir,
                                           stdout=stdout_stream.getvalue(), stderr=stderr_stream.getvalue()))

    model_phase_started = time.perf_counter()
    try:
        drafting = importlib.import_module("aad_drafting")
        from aad_drafting.model2d import Sheet2D, latest_sheet
    except ImportError as exc:
        return failed("IMPORT_FAILED", "A 2D build needs the aad_drafting package.", str(exc))
    sheet = namespace.get("result")
    if not isinstance(sheet, Sheet2D):
        if sheet is not None:
            return failed("BAD_INPUT", f"A 2D project's `result` must be a Sheet2D, not {type(sheet).__name__}. "
                                       "Extrude it into a part project for 3D.")
        sheet = latest_sheet()
    if not isinstance(sheet, Sheet2D):
        return failed("BAD_INPUT", "The 2D model completed without a sheet.")
    units = metadata.get("units", "mm")
    if sheet.units != units:
        return failed("BAD_INPUT", f"The sheet uses {sheet.units} but the project declares {units}. Numbers are never rescaled.")
    try:
        data = sheet.to_dict()
        _add_phase_time(phase_timings, "model", model_phase_started)
        write_started = time.perf_counter()
        (job_dir / "sheet.json").write_text(json.dumps(data, allow_nan=False), encoding="utf-8")
        _add_phase_time(phase_timings, "write", write_started)
    except (OSError, TypeError, ValueError) as exc:
        return failed("BAD_INPUT", "The sheet could not be saved.", f"{type(exc).__name__}: {exc}")

    model_phase_started = time.perf_counter()
    context = ModelContext(
        project_dir=Path(payload["project_dir"]),
        rev=payload.get("rev", 0),
        kind="2d",
        units=units,
        sheet=data,
        params=current_params(),
        spec=payload.get("spec", {}),
        material=metadata.get("material"),
        process=metadata.get("process"),
        job_dir=job_dir,
        meta=metadata,
        param_definitions=current_param_definitions(),
    )
    context.metrics = _sheet_metrics(sheet, units, _elapsed_ms(started))
    _add_phase_time(phase_timings, "model", model_phase_started)
    if not any(entity.layer == "cut" and entity.kind != "text" for entity in sheet.entities.values()):
        return failed("BAD_INPUT", "The sheet has no cut geometry, so its contours cannot be checked.")
    keep = {item.name for item in fields(Check)}
    phase_started = time.perf_counter()
    checks = [
        Check(**{key: value for key, value in dict(item).items() if key in keep})
        for item in drafting.checks(context)
    ]
    try:
        quality = importlib.import_module("aad_quality")
        for item in [*quality.spec_checks(context), *quality.process_checks(context)]:
            if isinstance(item, Check):
                checks.append(item)
            else:
                checks.append(Check(**{key: value for key, value in dict(item).items() if key in keep}))
    except Exception as exc:
        if is_memory_exhaustion(exc):
            raise
        checks.append(Check(id="2d_spec_evaluation_failed", suite="spec", level="error", status="fail",
                            message="2D specification or process checks could not be evaluated.",
                            actual=f"{type(exc).__name__}: {exc}"))
    checks.extend(_param_provenance_warnings(context.param_definitions))
    _add_phase_time(phase_timings, "gate.2d", phase_started)
    scene = None
    phase_started = time.perf_counter()
    try:
        scene_dir = job_dir / "scene"
        scene_dir.mkdir(exist_ok=True)
        value = drafting.make_scene(context, scene_dir)
        scene = SceneManifest(**value) if isinstance(value, dict) else value
    except Exception as exc:  # The sheet is still real evidence without its viewer scene.
        if is_memory_exhaustion(exc):
            raise
        checks.append(Check(id="scene_generation_failed", suite="2d", level="warn", status="warn",
                            message="Scene generation failed; the saved sheet was retained.",
                            actual=f"{type(exc).__name__}: {exc}"))
    _add_phase_time(phase_timings, "scene", phase_started)
    context.metrics["phase_timings_ms"] = _rounded_timings(phase_timings)
    failing = next((c for c in checks if c.level == "error" and c.status == "fail"), None)
    if failing is not None:
        summary = f"2D check {failing.id} failed: {failing.message}"
    elif any(c.status in {"skip", "warn"} for c in checks):
        summary = "The sheet built with warnings or skipped checks; it remains draft."
    else:
        summary = "The sheet built and its available 2D checks passed; it remains draft."
    result = BuildResult(
        ok=failing is None,
        status="draft" if failing is None else "failed",
        summary=summary,
        context=context,
        checks=checks,
        metrics=context.metrics,
        artifacts={"sheet.json": str(job_dir / "sheet.json")},
        scene=scene,
        job_dir=job_dir,
        build_ms=_elapsed_ms(started),
        stdout=stdout_stream.getvalue(),
        stderr=stderr_stream.getvalue(),
    )
    if failing is not None:
        code = "REQUIREMENT_FAILED" if failing.suite in {"spec", "process"} else "HARD_GATE_FAILED"
        result.failure = Failure(code=code, phase="validate", message=result.summary,
                                 feature=failing.feature, file=source_path.name)
    result.checks_summary = _checks_summary(checks)
    return _result_wire(result)


def _sheet_metrics(sheet: Any, units: str, build_ms: float) -> dict[str, Any]:
    metrics: dict[str, Any] = {
        "units": units, "bbox": [0.0, 0.0, 0.0], "solids": 0, "faces": 0,
        "edges": len(sheet.entities), "valid": True, "build_ms": build_ms,
    }
    layers = [layer for layer in sheet.layers if any(
        entity.layer == layer and entity.kind != "text" for entity in sheet.entities.values())]
    if layers:
        size = sheet.bounding_box(layers=layers).size
        metrics["bbox"] = [_number(size.X), _number(size.Y), 0.0]
    try:
        metrics["area"] = _number(sheet.to_face().area)
    except Exception as exc:  # Open or absent cut contours have no area. The 2D checks report why.
        if is_memory_exhaustion(exc):
            raise
        pass
    return metrics


def _extract_shape(namespace: dict[str, Any], build_parts: list[Any], shape_type: type[Any]) -> Any:
    candidate = namespace.get("result")
    if candidate is not None:
        if hasattr(candidate, "part") and not isinstance(candidate, shape_type):
            candidate = candidate.part
        if isinstance(candidate, shape_type):
            return candidate
        raise TypeError(
            f"The model's `result` is {type(candidate).__name__}, not a build123d Shape."
        )

    declared_part = named_shape()
    if isinstance(declared_part, shape_type):
        return declared_part

    for builder in reversed(build_parts):
        if (
            not getattr(builder, "_aad_completed", False)
            or getattr(builder, "_aad_nested", True)
            or builder.mode.name == "PRIVATE"
        ):
            continue
        try:
            candidate = builder.part
        except Exception as exc:
            if is_memory_exhaustion(exc):
                raise
            continue
        if isinstance(candidate, shape_type):
            return candidate
    raise ValueError(
        "The model completed without a shape. Assign a build123d Shape to `result` "
        "or create geometry in a BuildPart context."
    )


def _shape_metrics(shape: Any, build123d: ModuleType, units: str, build_ms: float) -> dict[str, Any]:
    box = shape.bounding_box()
    size = box.size
    center = shape.center(build123d.CenterOf.MASS)
    solids = list(shape.solids())
    solid_count = len(solids)
    faces = len(shape.faces())
    edges = len(shape.edges())
    return {
        "units": units,
        "bbox": [_number(size.X), _number(size.Y), _number(size.Z)],
        "volume": _number(math.fsum(_number(solid.volume) for solid in solids)),
        "area": _number(shape.area),
        "center_of_mass": [_number(center.X), _number(center.Y), _number(center.Z)],
        "solids": solid_count,
        "faces": faces,
        "edges": edges,
        "valid": bool(shape.is_valid),
        "build_ms": float(build_ms),
    }


def _number(value: Any) -> float:
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("build123d returned a non-finite metric")
    return number


def _optional_checks(
    *,
    module_name: str,
    function_name: str,
    context: ModelContext,
    suite: str,
    unavailable_id: str,
    unavailable_message: str,
) -> tuple[list[Check], bool]:
    try:
        module = importlib.import_module(module_name)
        hook = getattr(module, function_name)
    except Exception as exc:
        if is_memory_exhaustion(exc):
            raise
        missing = isinstance(exc, ModuleNotFoundError) and exc.name == module_name
        return [Check(
            id=unavailable_id if missing else f"{function_name}_hook_failed",
            suite=suite, level="warn", status="skip" if missing else "warn",
            message=unavailable_message if missing else f"{module_name}.{function_name} could not be loaded; the built BREP artifact was retained.",
            actual=f"{type(exc).__name__}: {exc}",
        )], False

    try:
        values = hook(context)
        if values is None:
            raise TypeError(f"{module_name}.{function_name} returned None instead of a check list")
        normalized = [_normalize_check(value, suite) for value in values]
        if not normalized:
            normalized = [
                Check(
                    id=f"{function_name}_no_results",
                    suite=suite,
                    level="warn",
                    status="skip",
                    message=f"{function_name} returned no checks; this hook did not prove any requirements.",
                )
            ]
        return normalized, True
    except Exception as exc:
        if is_memory_exhaustion(exc):
            raise
        return [
            Check(
                id=f"{function_name}_hook_failed",
                suite=suite,
                level="warn",
                status="warn",
                message=f"{function_name} raised an exception; the built BREP artifact was retained.",
                actual=f"{type(exc).__name__}: {exc}",
            )
        ], False


def _normalize_check(value: Any, suite: str) -> Check:
    if isinstance(value, Check):
        check = replace(value, suite=suite) if value.suite == "hard" else value
    elif isinstance(value, dict):
        fields = {key: value[key] for key in Check.__dataclass_fields__ if key in value}
        fields.setdefault("suite", suite)
        fields.setdefault("level", "warn")
        fields.setdefault("status", "skip")
        check = Check(**fields)
    else:
        raise TypeError(f"check hook returned {type(value).__name__}, not Check or dict")
    if not isinstance(check.id, str) or not check.id.strip():
        raise ValueError("check hook returned a check without a non-empty string ID")
    if (
        check.suite != suite
        or not isinstance(check.level, str)
        or check.level not in {"error", "warn"}
        or not isinstance(check.status, str)
        or check.status not in {"pass", "fail", "warn", "skip"}
    ):
        raise ValueError("check hook returned a check with an unsupported suite, level, or status")
    return check


def _requirements_proven(
    spec: dict[str, Any],
    spec_checks: list[Check],
    *,
    hook_available: bool,
    params_applied: bool,
) -> tuple[bool, Check | None]:
    requirements = spec.get("requirements", [])
    if not isinstance(requirements, list):
        return False, Check(
            id="spec_requirements_malformed",
            suite="spec",
            level="warn",
            status="skip",
            message="The requirements field is not a list; verification was withheld.",
        )
    if not requirements:
        return False, Check(
            id="requirements_not_provided",
            suite="spec",
            level="warn",
            status="skip",
            message="No requirements were supplied, so the model remains a draft.",
        )

    requirement_rows: list[tuple[str, str]] = []
    for requirement in requirements:
        if not isinstance(requirement, dict):
            return False, Check(
                id="spec_requirements_malformed",
                suite="spec",
                level="warn",
                status="skip",
                message="A requirement is not an object; verification was withheld.",
            )
        requirement_id = requirement.get("id")
        level = requirement.get("level", "error")
        if (
            not isinstance(requirement_id, str)
            or not requirement_id.strip()
            or not isinstance(level, str)
            or level not in {"error", "warn"}
        ):
            return False, Check(
                id="spec_requirements_malformed",
                suite="spec",
                level="warn",
                status="skip",
                message="A requirement has a missing ID or unsupported level; verification was withheld.",
            )
        requirement_rows.append((requirement_id, level))

    requirement_ids = [requirement_id for requirement_id, _ in requirement_rows]
    if len(set(requirement_ids)) != len(requirement_ids):
        return False, Check(
            id="spec_requirements_malformed",
            suite="spec",
            level="warn",
            status="skip",
            message="Requirement IDs are duplicated; verification was withheld.",
        )
    if not hook_available or not params_applied:
        return False, None

    check_ids = [check.id for check in spec_checks]
    if len(set(check_ids)) != len(check_ids):
        return False, _coverage_incomplete("The spec hook returned duplicate check IDs.")
    if any(check.status == "skip" for check in spec_checks):
        return False, _coverage_incomplete("The spec hook skipped at least one check.")
    if any(check.level == "error" and check.status != "pass" for check in spec_checks):
        return False, _coverage_incomplete("An error-level spec check did not pass.")
    if any(_contains_nonfinite(check.expected) or _contains_nonfinite(check.actual) for check in spec_checks):
        return False, _coverage_incomplete("A spec check reported a non-finite value.")

    for requirement_id, requirement_level in requirement_rows:
        matches = [check for check in spec_checks if check.id == requirement_id]
        if len(matches) != 1:
            return False, _coverage_incomplete(
                f"Requirement {requirement_id!r} did not have exactly one check result."
            )
        check = matches[0]
        if check.level != requirement_level:
            return False, _coverage_incomplete(
                f"Requirement {requirement_id!r} and its check have different levels."
            )
        if requirement_level == "error" and check.status != "pass":
            return False, _coverage_incomplete(f"Error-level requirement {requirement_id!r} did not pass.")
        if requirement_level == "warn" and check.status not in {"pass", "warn"}:
            return False, _coverage_incomplete(f"Warning-level requirement {requirement_id!r} was not evaluated.")
    return True, None


def _coverage_incomplete(message: str) -> Check:
    return Check(
        id="spec_coverage_incomplete",
        suite="spec",
        level="warn",
        status="skip",
        message=message,
    )


def _contains_nonfinite(value: Any) -> bool:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return not math.isfinite(float(value))
    if isinstance(value, str):
        tokens = value.lower().replace("infinity", "inf").replace(",", " ").replace(";", " ").split()
        return any(token.strip("[](){}:=") in {"nan", "+nan", "-nan", "inf", "+inf", "-inf"} for token in tokens)
    if isinstance(value, dict):
        return any(_contains_nonfinite(nested) for nested in value.values())
    if isinstance(value, (list, tuple)):
        return any(_contains_nonfinite(nested) for nested in value)
    return False


def _optional_scene(context: ModelContext) -> tuple[SceneManifest | None, Check]:
    try:
        module = importlib.import_module("aad_render")
        hook = getattr(module, "make_scene")
    except Exception as exc:
        if is_memory_exhaustion(exc):
            raise
        missing = isinstance(exc, ModuleNotFoundError) and exc.name == "aad_render"
        return None, Check(
            id="scene_generation_unavailable" if missing else "scene_generation_failed",
            suite="hard", level="warn", status="skip" if missing else "warn",
            message="Scene generation is unavailable because aad_render is not installed." if missing else "aad_render.make_scene could not be loaded; the built BREP artifact was retained.",
            actual=f"{type(exc).__name__}: {exc}",
        )

    try:
        scene_dir = context.job_dir / "scene"
        scene_dir.mkdir(parents=True, exist_ok=True)
        value = hook(context, scene_dir)
        if isinstance(value, SceneManifest):
            scene = value
        elif isinstance(value, dict):
            scene = SceneManifest(**value)
        else:
            raise TypeError("make_scene did not return a SceneManifest or dictionary")
        if (
            len(scene.scene_id) != 64 or any(c not in "0123456789abcdef" for c in scene.scene_id)
            or not isinstance(scene.total_bytes, int) or isinstance(scene.total_bytes, bool) or scene.total_bytes <= 0
            or len(scene.bbox) != 6 or not all(math.isfinite(float(v)) for v in scene.bbox)
            or scene.format not in {"tcv-5", "svg-1"} or scene.encoding != "gzip"
            or scene.units != context.units or scene.rev != context.rev or not scene.project
            or not scene.parts or any(not p.get("path") or not p.get("name") for p in scene.parts)
        ):
            raise ValueError("make_scene returned an incomplete or invalid scene manifest")
        return scene, Check(
            id="scene_generated",
            suite="hard",
            level="warn",
            status="pass",
            message="A scene manifest was produced.",
            actual=scene.scene_id,
        )
    except Exception as exc:
        if is_memory_exhaustion(exc):
            raise
        return None, Check(
            id="scene_generation_failed",
            suite="hard",
            level="warn",
            status="warn",
            message="Scene generation failed; the built BREP artifact was retained.",
            actual=f"{type(exc).__name__}: {exc}",
        )


def _optional_render(context: ModelContext, requested: bool) -> tuple[dict[str, str], Check]:
    if not requested:
        return {}, Check(
            id="render_not_requested",
            suite="hard",
            level="warn",
            status="skip",
            message="Agent rendering was not requested for this build.",
        )
    try:
        module = importlib.import_module("aad_render")
        hook = getattr(module, "render")
    except Exception as exc:
        if is_memory_exhaustion(exc):
            raise
        missing = isinstance(exc, ModuleNotFoundError) and exc.name == "aad_render"
        return {}, Check(
            id="render_unavailable" if missing else "render_failed",
            suite="hard", level="warn", status="skip" if missing else "warn",
            message="Agent rendering is unavailable because aad_render is not installed." if missing else "aad_render.render could not be loaded; the built BREP artifact was retained.",
            actual=f"{type(exc).__name__}: {exc}",
        )

    try:
        from .types import RenderRequest

        output_dir = context.job_dir / "render"
        output_dir.mkdir(parents=True, exist_ok=True)
        paths = hook(context, RenderRequest(), output_dir)
        artifacts = {f"render_{Path(path).name}": str(Path(path)) for path in paths if Path(path).is_file()}
        if not artifacts:
            raise ValueError("render returned no existing output files")
        return artifacts, Check(
            id="render_generated",
            suite="hard",
            level="warn",
            status="pass",
            message="Agent render output was produced.",
            actual=str(len(artifacts)),
        )
    except Exception as exc:
        if is_memory_exhaustion(exc):
            raise
        return {}, Check(
            id="render_failed",
            suite="hard",
            level="warn",
            status="warn",
            message="Agent rendering failed; the built BREP artifact was retained.",
            actual=f"{type(exc).__name__}: {exc}",
        )


# build123d function names, deepest call first, that identify the refused operation.
_KERNEL_OPERATIONS = {
    "fillet": "KERNEL_FILLET_FAILED",
    "max_fillet": "KERNEL_FILLET_FAILED",
    "chamfer": "KERNEL_CHAMFER_FAILED",
    "offset": "KERNEL_SHELL_FAILED",
    "offset_3d": "KERNEL_SHELL_FAILED",
    "shell": "KERNEL_SHELL_FAILED",
    "thicken": "KERNEL_SHELL_FAILED",
    "sweep": "KERNEL_SWEEP_FAILED",
    "loft": "KERNEL_SWEEP_FAILED",
    "make_loft": "KERNEL_SWEEP_FAILED",
    "revolve": "KERNEL_SWEEP_FAILED",
    "extrude": "KERNEL_SWEEP_FAILED",
    "_bool_op": "KERNEL_BOOLEAN_FAILED",
    "fuse": "KERNEL_BOOLEAN_FAILED",
    "cut": "KERNEL_BOOLEAN_FAILED",
    "intersect": "KERNEL_BOOLEAN_FAILED",
    "split": "KERNEL_BOOLEAN_FAILED",
}
_KERNEL_REPAIRS = {
    "KERNEL_FILLET_FAILED": [
        "Use a smaller radius. max_fillet() on the shape gives the largest that fits.",
        "Fillet fewer edges at once, or fillet before cutting nearby features.",
    ],
    "KERNEL_CHAMFER_FAILED": [
        "Use a smaller length, or chamfer fewer edges at once.",
        "Chamfer before cutting nearby features.",
    ],
    "KERNEL_SHELL_FAILED": [
        "Make the wall thinner than the smallest local radius or the part's half thickness.",
        "Shell before adding fillets and small features.",
    ],
    "KERNEL_SWEEP_FAILED": [
        "Check that the profile is closed, does not cross itself, and does not cross the axis or path.",
        "Check that a sweep profile is smaller than the path's tightest bend.",
    ],
    "KERNEL_BOOLEAN_FAILED": [
        "Move faces that only just touch so that the tools clearly overlap or clearly miss.",
        "Check both shapes are valid, and apply the operation to fewer bodies at a time.",
    ],
    "KERNEL_OTHER": [
        "The kernel gave no specific cause. Check for zero or negative sizes at the reported line.",
        "Reduce the operation to its smallest failing case, then rebuild.",
    ],
}


def _kernel_failure_code(exc: BaseException) -> str | None:
    """Name the refused operation, only when the kernel layer raised the error.

    A TypeError, a typo, or a plain raise in the model stays RUNTIME_ERROR. A
    ValueError counts only when it comes from a recognized build123d operation.
    """
    native = type(exc).__module__.startswith("OCP")
    if not native and not isinstance(exc, (ValueError, RuntimeError)):
        return None
    try:
        frames = traceback.extract_tb(exc.__traceback__)
    except Exception:
        return "KERNEL_OTHER" if native else None
    kernel_frames = [frame.name for frame in frames if "build123d" in Path(frame.filename).parts]
    if not kernel_frames and not native:
        return None
    if "_validate_builder" in kernel_frames:  # An API misuse, not a kernel refusal.
        return None
    for name in reversed(kernel_frames):
        code = _KERNEL_OPERATIONS.get(name)
        if code:
            return code
    return "KERNEL_OTHER" if native else None


def _source_location(exc: BaseException, source_path: Path) -> tuple[str | None, int | None]:
    try:
        frames = traceback.extract_tb(exc.__traceback__)
    except Exception:
        return None, None
    expected = source_path.resolve()
    for frame in reversed(frames):
        try:
            if Path(frame.filename).resolve() == expected:
                return source_path.name, frame.lineno
        except OSError:
            continue
    return None, None


def _memory_failure(
    exc: BaseException,
    *,
    file: str | None = None,
    line: int | None = None,
) -> Failure:
    return Failure(
        code="OUT_OF_MEMORY",
        phase="oom",
        message="The worker could not allocate memory under the configured engine.memory ceiling.",
        kernel_message=f"{type(exc).__name__}: {exc}" or type(exc).__name__,
        file=file,
        line=line,
        repair=["Reduce the model's memory use or raise engine.memory if the machine has capacity."],
    )


def _failed_result(
    failure: Failure,
    *,
    elapsed_ms: float,
    job_dir: Path,
    metrics: dict[str, Any] | None = None,
    context: ModelContext | None = None,
    artifacts: dict[str, str] | None = None,
    stdout: str = "",
    stderr: str = "",
) -> BuildResult:
    result = BuildResult(
        ok=False,
        status="failed",
        summary=failure.message,
        context=context,
        metrics=dict(metrics or {}),
        failure=failure,
        artifacts=dict(artifacts or {}),
        job_dir=job_dir,
        build_ms=elapsed_ms,
        stdout=stdout,
        stderr=stderr,
    )
    return result


def _checks_summary(checks: list[Check]) -> dict[str, int]:
    return {
        status: sum(1 for check in checks if check.status == status)
        for status in ("pass", "fail", "warn", "skip")
    }


def _param_provenance_warnings(definitions: dict[str, dict[str, Any]]) -> list[Check]:
    """Warn when a publication or measurement label has no source to inspect."""
    warnings = []
    for name, definition in definitions.items():
        basis = definition.get("basis", "unknown")
        source = definition.get("source")
        if basis in {"published", "measured"} and not (isinstance(source, str) and source.strip()):
            warnings.append(
                Check(
                    id=f"param_source_missing.{name}",
                    suite="hard",
                    level="warn",
                    status="warn",
                    message=(
                        f"Parameter {name!r} is labeled {basis} but has no source. "
                        "AAD does not verify provenance claims."
                    ),
                )
            )
    return warnings


def _preserved_artifacts(job_dir: Path) -> dict[str, str]:
    brep_path = job_dir / "part.brep"
    return {"part.brep": str(brep_path)} if brep_path.is_file() else {}


def _result_wire(result: BuildResult) -> dict[str, Any]:
    context: dict[str, Any] | None = None
    if result.context is not None:
        ctx = result.context
        context = {
            "project_dir": str(ctx.project_dir),
            "rev": ctx.rev,
            "kind": ctx.kind,
            "units": ctx.units,
            "shape": None,
            "sheet": None,
            "features": [asdict(feature) for feature in ctx.features],
            "params": ctx.params,
            "metrics": ctx.metrics,
            "spec": ctx.spec,
            "material": ctx.material,
            "process": ctx.process,
            "job_dir": str(ctx.job_dir),
            "meta": ctx.meta,
            "param_definitions": ctx.param_definitions,
            "part_name": ctx.part_name,
        }
    return {
        "ok": result.ok,
        "status": result.status,
        "summary": result.summary,
        "context": context,
        "metrics": result.metrics,
        "checks": [asdict(check) for check in result.checks],
        "checks_summary": result.checks_summary or _checks_summary(result.checks),
        "failure": asdict(result.failure) if result.failure is not None else None,
        "artifacts": result.artifacts,
        "scene": asdict(result.scene) if result.scene is not None else None,
        "build_ms": result.build_ms,
        "stdout": result.stdout,
        "stderr": result.stderr,
        "requirements_proven": result.requirements_proven,
    }


def _json_default(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    if is_dataclass(value):
        return asdict(value)
    raise TypeError(f"{type(value).__name__} is not JSON serializable")


def _add_phase_time(timings: dict[str, float], name: str, started: float) -> None:
    timings[name] = timings.get(name, 0.0) + (time.perf_counter() - started) * 1000


def _rounded_timings(timings: dict[str, float]) -> dict[str, float]:
    return {name: round(milliseconds, 3) for name, milliseconds in timings.items()}


def _elapsed_ms(started: float) -> float:
    return (time.perf_counter() - started) * 1000
