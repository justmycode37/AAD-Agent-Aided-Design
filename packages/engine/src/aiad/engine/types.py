"""Public data contracts shared by AIAD packages.

This module deliberately imports only the standard library. In particular,
``Shape`` and ``Sheet2D`` are annotations under ``TYPE_CHECKING`` only.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, Protocol

if TYPE_CHECKING:
    from build123d import Shape
else:
    class Shape(Protocol):
        """Runtime-only structural placeholder; the worker uses build123d.Shape."""


class Sheet2D(Protocol):
    """Structural placeholder for the drafting package's sheet type."""

CheckSuite = Literal["hard", "spec", "process", "interference", "2d"]
CheckLevel = Literal["error", "warn"]
CheckStatus = Literal["pass", "fail", "warn", "skip"]
BuildStatus = Literal["failed", "draft", "verified"]
FailurePhase = Literal[
    "input",
    "sandbox",
    "syntax",
    "runtime",
    "selector",
    "kernel",
    "validate",
    "timeout",
    "oom",
    "crash",
]


@dataclass
class Entity:
    # A revision-local metadata handle. Persistent references use ``selector``;
    # exact topology handles returned by resolve() stay bound to that context.
    kind: Literal["face", "edge", "vertex", "part"] = "part"
    index: int | None = None
    feature: str | None = None
    selector: str | None = None
    geometry: dict[str, Any] = field(default_factory=dict)


@dataclass
class Candidate:
    selector: str = ""
    feature: str | None = None
    kind: str | None = None
    index: int | None = None
    geometry: dict[str, Any] = field(default_factory=dict)


@dataclass
class FeatureInfo:
    name: str = "main"
    faces: list[Entity] = field(default_factory=list)
    edges: list[Entity] = field(default_factory=list)
    line_start: int | None = None
    line_end: int | None = None
    deps: list[str] = field(default_factory=list)
    intent: str | None = None
    vertices: list[Entity] = field(default_factory=list)


@dataclass
class Check:
    id: str = ""
    suite: CheckSuite = "hard"
    level: CheckLevel = "error"
    status: CheckStatus = "skip"
    message: str = ""
    expected: str | None = None
    actual: str | None = None
    feature: str | None = None
    selector: str | None = None
    heuristic: bool | None = None
    assumptions: str | None = None
    fix_hint: str | None = None


@dataclass
class Failure:
    code: str = "BAD_INPUT"
    phase: FailurePhase = "input"
    message: str = ""
    kernel_message: str | None = None
    feature: str | None = None
    file: str | None = None
    line: int | None = None
    selector: str | None = None
    candidates: list[Candidate] = field(default_factory=list)
    probes: list[dict[str, Any]] = field(default_factory=list)
    repair: list[str] = field(default_factory=list)
    last_good_rev: int | None = None


@dataclass
class ModelContext:
    # These fields mirror CONTRACTS.md §9. Shape objects exist only inside
    # the worker; the parent result carries their BREP path instead.
    project_dir: Path = field(default_factory=Path.cwd)
    rev: int = 0
    kind: str = "part"
    units: str = "mm"
    shape: Shape | None = None
    sheet: Sheet2D | None = None
    features: list[FeatureInfo] = field(default_factory=list)
    params: dict[str, Any] = field(default_factory=dict)
    metrics: dict[str, Any] = field(default_factory=dict)
    spec: dict[str, Any] = field(default_factory=dict)
    material: str | None = None
    process: str | None = None
    job_dir: Path = field(default_factory=Path.cwd)
    meta: dict[str, Any] = field(default_factory=dict)
    param_definitions: dict[str, dict[str, Any]] = field(default_factory=dict)
    part_name: str | None = None


@dataclass
class BuildRequest:
    source: str = ""
    project_dir: Path = field(default_factory=Path.cwd)
    job_dir: Path | None = None
    rev: int = 0
    kind: str = "part"
    units: str = "mm"
    params: dict[str, Any] = field(default_factory=dict)
    spec: dict[str, Any] = field(default_factory=dict)
    material: str | None = None
    process: str | None = None
    timeout_s: float = 60.0
    preview: bool = False


@dataclass
class SceneManifest:
    scene_id: str = ""
    project: str = ""
    rev: int = 0
    kind: Literal["3d", "2d"] = "3d"
    format: Literal["tcv-5", "svg-1"] = "tcv-5"
    encoding: Literal["gzip"] = "gzip"
    units: str = "mm"
    bbox: list[float] = field(default_factory=list)
    total_bytes: int = 0
    triangles: int | None = None
    parts: list[dict[str, Any]] = field(default_factory=list)
    params: list[dict[str, Any]] = field(default_factory=list)
    checks_summary: dict[str, int] = field(default_factory=dict)
    failed_features: list[str] = field(default_factory=list)
    status: Literal["draft", "verified"] = "draft"
    compare: SceneManifest | None = None


@dataclass
class RenderRequest:
    views: list[str] = field(default_factory=lambda: ["sheet"])
    style: str = "shaded"
    section: dict[str, Any] | None = None
    labels: str = "none"
    heatmap: str | None = None
    highlight: list[str] = field(default_factory=list)
    size: int = 1024


@dataclass
class DrawingRequest:
    sheet: str = "A4"
    views: list[dict[str, Any]] = field(default_factory=list)
    dimensions: str | list[dict[str, Any]] = "auto"
    style: str = "iso"
    title_block: dict[str, str] = field(default_factory=dict)
    formats: list[str] = field(default_factory=lambda: ["svg"])


@dataclass
class DrawingResult:
    ok: bool = False
    summary: str = ""
    artifacts: dict[str, str] = field(default_factory=dict)
    checks: list[Check] = field(default_factory=list)
    failure: Failure | None = None


@dataclass
class BuildResult:
    ok: bool = False
    status: BuildStatus = "failed"
    summary: str = ""
    context: ModelContext | None = None
    metrics: dict[str, Any] = field(default_factory=dict)
    checks: list[Check] = field(default_factory=list)
    checks_summary: dict[str, int] = field(default_factory=dict)
    failure: Failure | None = None
    artifacts: dict[str, str] = field(default_factory=dict)
    scene: SceneManifest | None = None
    job_dir: Path | None = None
    build_ms: float = 0.0
    stdout: str = ""
    stderr: str = ""
    requirements_proven: bool = False
