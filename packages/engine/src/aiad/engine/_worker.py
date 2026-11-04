"""Private, one-request worker entry point used by :mod:`aiad.engine.jobs`."""

from __future__ import annotations

import contextlib
import importlib
import json
import math
import sys
import time
import traceback
from dataclasses import asdict, is_dataclass, replace
from pathlib import Path
from types import ModuleType
from typing import Any

from aiad._runtime import configure_params, current_params
from .gate import hard_checks
from .types import (
    BuildResult,
    Check,
    Entity,
    Failure,
    FeatureInfo,
    ModelContext,
    SceneManifest,
)

_RESULT_MARKER = "AIAD_ENGINE_RESULT:"
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
            result = _result_wire(
                BuildResult(
                    ok=False,
                    status="failed",
                    summary="The worker encountered an unexpected internal error.",
                    failure=Failure(
                        code="WORKER_CRASH",
                        phase="crash",
                        message="The worker encountered an unexpected internal error; no cause was inferred.",
                        kernel_message=f"{type(exc).__name__}: {exc}",
                    ),
                    artifacts=artifacts,
                    job_dir=job_dir,
                )
            )
    sys.stdout.write("\n" + _RESULT_MARKER + json.dumps({"protocol": 1, "result": result}, default=_json_default) + "\n")
    sys.stdout.flush()


def _build(payload: dict[str, Any]) -> dict[str, Any]:
    started = time.perf_counter()
    job_dir = Path(payload["job_dir"])
    project_dir = Path(payload["project_dir"])
    source_path = job_dir / "model.py"
    source = payload["source"]
    stdout_stream = _TailTextStream()
    stderr_stream = _TailTextStream()

    try:
        source_path.write_text(source, encoding="utf-8")
        code = compile(source, str(source_path), "exec")
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
        failure = Failure(
            code="IMPORT_FAILED",
            phase="kernel",
            message="The build123d kernel could not be imported in the worker.",
            kernel_message=f"{type(exc).__name__}: {exc}",
            file=source_path.name,
        )
        return _result_wire(_failed_result(failure, elapsed_ms=_elapsed_ms(started), job_dir=job_dir))

    tracked_build_parts: list[Any] = []
    original_build_part = build123d.BuildPart

    class TrackedBuildPart(original_build_part):
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            super().__init__(*args, **kwargs)
            # build123d 0.13 records its caller's Python frame to decide
            # whether nested builders publish to the same construction scope.
            # This tracking constructor adds one frame; preserve the frame a
            # plain BuildPart would have recorded instead.
            self._python_frame = sys._getframe(1)
            self._aiad_completed = False
            self._aiad_nested = False
            tracked_build_parts.append(self)

        def __exit__(self, exception_type: Any, exception_value: Any, traceback: Any) -> Any:
            result = super().__exit__(exception_type, exception_value, traceback)
            self._aiad_completed = exception_type is None
            # The original exit has restored the enclosing builder context.
            # Record actual nesting independently of same-scope publication:
            # helper functions can have an enclosing context but no parent.
            self._aiad_nested = original_build_part._get_context() is not None
            return result

    # build123d decides which operations a context allows from its class name, so a subclass with
    # another name makes fillet() and chamfer() refuse to run inside `with BuildPart()`.
    TrackedBuildPart.__name__ = original_build_part.__name__
    TrackedBuildPart.__qualname__ = original_build_part.__qualname__

    # Scripts that import BuildPart from aiad or build123d receive this
    # subclass. The class only records the active builder object so a context
    # manager can supply its completed part when no `result` is assigned.
    build123d.BuildPart = TrackedBuildPart
    configure_params(payload.get("params", {}))
    namespace: dict[str, Any] = {
        "__name__": "__aiad_model__",
        "__file__": str(source_path),
        "params": __import__("aiad").params,
    }
    for name in dir(build123d):
        if not name.startswith("_"):
            namespace[name] = getattr(build123d, name)

    try:
        with contextlib.redirect_stdout(stdout_stream), contextlib.redirect_stderr(stderr_stream):
            exec(code, namespace, namespace)
    except BaseException as exc:
        if isinstance(exc, SyntaxError):
            failure = Failure(
                code="SYNTAX_ERROR",
                phase="syntax",
                message="The model script has a syntax error at the reported source line.",
                kernel_message=exc.msg,
                file=source_path.name,
                line=exc.lineno,
                repair=["Correct the syntax at the reported model.py line and rebuild."],
            )
        else:
            file_name, line = _source_location(exc, source_path)
            failure = Failure(
                code="RUNTIME_ERROR",
                phase="runtime",
                message=(
                    f"The model script raised {type(exc).__name__} at {file_name}:{line}."
                    if file_name and line
                    else f"The model script raised {type(exc).__name__}."
                ),
                kernel_message=str(exc) or type(exc).__name__,
                file=file_name,
                line=line,
                feature="main" if file_name else None,
                repair=["Inspect the reported source line and the exception detail, then rebuild."],
            )
        result = _failed_result(
            failure,
            elapsed_ms=_elapsed_ms(started),
            job_dir=job_dir,
            stdout=stdout_stream.getvalue(),
            stderr=stderr_stream.getvalue(),
        )
        build123d.BuildPart = original_build_part
        return _result_wire(result)
    finally:
        build123d.BuildPart = original_build_part

    try:
        shape = _extract_shape(namespace, tracked_build_parts, build123d.Shape)
    except (TypeError, ValueError) as exc:
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
            )
        )

    artifacts: dict[str, str] = {}
    brep_path = job_dir / "part.brep"
    try:
        exported = bool(build123d.export_brep(shape, brep_path))
        if not exported or not brep_path.is_file() or brep_path.stat().st_size == 0:
            raise OSError("build123d did not create a non-empty BREP file")
        artifacts["part.brep"] = str(brep_path)
    except Exception as exc:
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
            )
        )

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

    build_ms = _elapsed_ms(started)
    metrics = _shape_metrics(shape, build123d, payload.get("units", "mm"), build_ms)
    main_feature = FeatureInfo(
        name="main",
        faces=[Entity(kind="face", index=index, feature="main") for index in range(metrics["faces"])],
        edges=[Entity(kind="edge", index=index, feature="main") for index in range(metrics["edges"])],
        line_start=1 if source else None,
        line_end=max(1, source.count("\n") + 1) if source else None,
    )
    context = ModelContext(
        project_dir=project_dir,
        rev=payload.get("rev", 0),
        kind=payload.get("kind", "part"),
        units=payload.get("units", "mm"),
        shape=shape,
        features=[main_feature],
        params=params,
        metrics=metrics,
        spec=payload.get("spec", {}),
        material=payload.get("material"),
        process=payload.get("process"),
        job_dir=job_dir,
    )

    checks = hard_checks(context)
    if params_warning is not None:
        checks.append(params_warning)

    spec_checks, spec_hook_available = _optional_checks(
        module_name="aiad_quality",
        function_name="spec_checks",
        context=context,
        suite="spec",
        unavailable_id="spec_checks_unavailable",
        unavailable_message="Specification checks are unavailable because aiad_quality is not installed.",
    )
    checks.extend(spec_checks)

    process_checks, process_hook_available = _optional_checks(
        module_name="aiad_quality",
        function_name="process_checks",
        context=context,
        suite="process",
        unavailable_id="process_checks_unavailable",
        unavailable_message="Process checks are unavailable because aiad_quality is not installed.",
    )
    checks.extend(process_checks)

    scene, scene_check = _optional_scene(context)
    checks.append(scene_check)

    render_artifacts, render_check = _optional_render(context, bool(payload.get("preview", False)))
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
            code="INVALID_SOLID",
            phase="validate",
            message="The built shape failed the B-rep validity or solid-count gate.",
            kernel_message=failed_check.actual,
            feature="main",
            file=source_path.name,
        )
        status = "failed"
        ok = False
        summary = "A BREP artifact was saved, but the hard geometry gate failed."
    elif quality_failed:
        failure = None
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

    for builder in reversed(build_parts):
        if (
            not getattr(builder, "_aiad_completed", False)
            or getattr(builder, "_aiad_nested", True)
            or builder.mode.name == "PRIVATE"
        ):
            continue
        try:
            candidate = builder.part
        except Exception:
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
    solid_count = len(shape.solids())
    faces = len(shape.faces())
    edges = len(shape.edges())
    return {
        "units": units,
        "bbox": [_number(size.X), _number(size.Y), _number(size.Z)],
        "volume": _number(shape.volume),
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
        module = importlib.import_module("aiad_render")
        hook = getattr(module, "make_scene")
    except Exception as exc:
        missing = isinstance(exc, ModuleNotFoundError) and exc.name == "aiad_render"
        return None, Check(
            id="scene_generation_unavailable" if missing else "scene_generation_failed",
            suite="hard", level="warn", status="skip" if missing else "warn",
            message="Scene generation is unavailable because aiad_render is not installed." if missing else "aiad_render.make_scene could not be loaded; the built BREP artifact was retained.",
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
        module = importlib.import_module("aiad_render")
        hook = getattr(module, "render")
    except Exception as exc:
        missing = isinstance(exc, ModuleNotFoundError) and exc.name == "aiad_render"
        return {}, Check(
            id="render_unavailable" if missing else "render_failed",
            suite="hard", level="warn", status="skip" if missing else "warn",
            message="Agent rendering is unavailable because aiad_render is not installed." if missing else "aiad_render.render could not be loaded; the built BREP artifact was retained.",
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
        return {}, Check(
            id="render_failed",
            suite="hard",
            level="warn",
            status="warn",
            message="Agent rendering failed; the built BREP artifact was retained.",
            actual=f"{type(exc).__name__}: {exc}",
        )


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


def _elapsed_ms(started: float) -> float:
    return (time.perf_counter() - started) * 1000
