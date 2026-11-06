"""Backend-neutral input types for agent renders."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, TypeAlias

Vec3: TypeAlias = tuple[float, float, float]
RGB: TypeAlias = tuple[int, int, int]


@dataclass(slots=True)
class RenderFace:
    index: int
    source_index: int | None
    feature: str | None
    points: Any  # N x 3 floats
    triangles: Any  # M x 3 ints into points
    normal: Vec3
    anchor: Vec3
    section_cap: bool = False
    highlighted: bool = False
    color: RGB = (194, 201, 208)
    triangle_colors: Any = ()  # Optional N x 3 uint8 colors, one per triangle
    uv: Any = None  # N x 2 surface parameters of points, when the kernel gave them


@dataclass(slots=True)
class RenderLine:
    points: Any  # N x 3 floats
    role: str = "edge"  # edge, tangent, seam or hatch
    index: int | None = None
    source_index: int | None = None
    feature: str | None = None
    anchor: Vec3 | None = None
    highlighted: bool = False


@dataclass(slots=True)
class RenderScene:
    faces: list[RenderFace] = field(default_factory=list)
    lines: list[RenderLine] = field(default_factory=list)
    bounds: tuple[Vec3, Vec3] = ((0.0, 0.0, 0.0), (1.0, 1.0, 1.0))
    units: str = "model units"
    section_label: str | None = None
    data_colors: bool = False  # Face colors encode measured values, as in a heatmap.


@dataclass(frozen=True, slots=True)
class CameraSpec:
    name: str
    position: Vec3
    focal_point: Vec3
    view_up: Vec3
    right: Vec3
    up: Vec3
    parallel_scale: float
    distance: float


@dataclass(slots=True)
class Frame:
    """One rendered view and what lies under each of its pixels."""

    image: Any  # RGB Pillow image
    face_ids: Any = None  # H x W ints: RenderFace.index, -1 for background
    height: Any = None  # H x W floats: world distance toward the camera from its focal plane, nan for background


class RenderBackend(Protocol):
    """Renderer plug-in contract. Implementations return an RGB Pillow image."""

    name: str

    def render(
        self,
        scene: RenderScene,
        camera: CameraSpec,
        *,
        width: int,
        height: int,
        style: str,
    ) -> object:
        """Render one orthographic camera to an RGB image."""
