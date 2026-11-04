"""VTK offscreen renderer. The VTK package is imported only at construction."""

from __future__ import annotations

import ctypes.util
import os
import sys
from typing import Any

import numpy as np

from .protocol import CameraSpec, Frame, RenderScene

SUPERSAMPLE = 3
MAX_RENDER_PIXELS = 4608  # Above this per-side size a view drops to 2x, then 1x.
LINE_DEPTH_OFFSET = -20.0
BACKGROUND = (0.957, 0.965, 0.973)
EDGE_WIDTHS = {"hatch": 0.55, "seam": 0.5, "tangent": 0.65, "edge": 1.2, "highlight": 2.6}
EDGE_COLORS = {
    "shaded": {"edge": (30, 36, 44), "tangent": (96, 104, 114), "seam": (130, 137, 146)},
    "lines": {"edge": (20, 26, 34), "tangent": (120, 128, 138), "seam": (170, 176, 184)},
    "xray": {"edge": (46, 60, 76), "tangent": (96, 112, 128), "seam": (130, 144, 158)},
}
HATCH_COLOR = (58, 44, 24)
HIGHLIGHT_EDGE = (255, 79, 22)
HIDDEN_EDGE = (150, 158, 170)


def prepare_headless_gl() -> None:
    """Pick a safe OpenGL path before VTK opens a window.

    On Linux with no display, VTK's default X11 window segfaults, which would kill the worker with
    no message. OSMesa needs no display, so it's used when installed; otherwise this raises.
    """
    if not sys.platform.startswith("linux"):
        return
    if os.environ.get("VTK_DEFAULT_OPENGL_WINDOW"):
        return
    if os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"):
        return
    if ctypes.util.find_library("OSMesa") is None:
        raise RuntimeError(
            "No usable headless GL on this Linux machine: there is no display and OSMesa is not "
            "installed. Install libosmesa6, or run under a display such as xvfb."
        )
    os.environ["VTK_DEFAULT_OPENGL_WINDOW"] = "vtkOSOpenGLRenderWindow"


def _supersample(size: int) -> int:
    for factor in (SUPERSAMPLE, 2):
        if size * factor <= MAX_RENDER_PIXELS:
            return factor
    return 1


class VtkBackend:
    """VTK OpenGL renderer configured for repeatable offscreen pixels.

    Each view takes two passes through one window: an unlit pass that records which face owns each
    pixel and how deep it is, then the lit pass. Depth-based shading happens in ``post``, and the
    frame is box-filtered down so edges stay crisp without hardware antialiasing.
    """

    name = "vtk"

    def __init__(self) -> None:
        prepare_headless_gl()
        try:
            import vtk
        except ImportError as exc:  # pragma: no cover - depends on optional extra
            raise RuntimeError(
                "VTK rendering requires the optional aiad-render[vtk] extra"
            ) from exc
        self._vtk = vtk

    def render(
        self,
        scene: RenderScene,
        camera: CameraSpec,
        *,
        width: int,
        height: int,
        style: str,
    ) -> Any:
        return self.render_frames(scene, [camera], width=width, height=height, style=style)[0].image

    def render_many(
        self,
        scene: RenderScene,
        cameras: list[CameraSpec],
        *,
        width: int,
        height: int,
        style: str,
    ) -> list[Any]:
        return [frame.image for frame in self.render_frames(scene, cameras, width=width, height=height, style=style)]

    def render_frames(
        self,
        scene: RenderScene,
        cameras: list[CameraSpec],
        *,
        width: int,
        height: int,
        style: str,
        clip_box: tuple[tuple[float, float, float], tuple[float, float, float]] | None = None,
    ) -> list[Frame]:
        """Render views at one size through one offscreen window."""
        from PIL import Image

        from ..post import apply_gain, cavity_gain, downsample, draw_outlines

        if not cameras:
            return []
        factor = _supersample(max(width, height))
        full_width, full_height = width * factor, height * factor
        window = self._window(full_width, full_height)
        surface, ids = self._surface_mappers(scene, clip_box)
        lines = self._line_mappers(scene, style, clip_box)
        id_renderer = self._id_renderer(ids)
        beauty = self._beauty_renderer(surface, lines, style, factor, data_colors=scene.data_colors)
        hidden = self._hidden_renderer(scene, clip_box, factor) if style == "lines" else None
        center = factor // 2
        frames = []
        try:
            for camera in cameras:
                pixel_world = 2.0 * camera.parallel_scale / full_height
                raw = self._pass(window, id_renderer, camera, scene, full_width, full_height, lit=False)
                part = (raw[..., 0] | raw[..., 1] | raw[..., 2]) != 0
                near, far = id_renderer.GetActiveCamera().GetClippingRange()
                depth = self._depth(window, full_width, full_height)
                world_height = (camera.distance - (near + depth * (far - near))).astype(np.float32)
                world_height[~part] = np.nan
                sampled = raw[center::factor, center::factor][:height, :width].astype(np.int32)
                face_ids = (sampled[..., 0] << 16 | sampled[..., 1] << 8 | sampled[..., 2]) - 1
                pixels = self._pass(window, beauty, camera, scene, full_width, full_height)
                if hidden is not None:
                    behind = self._pass(window, hidden, camera, scene, full_width, full_height, lit=False)
                    # Hidden edges show only on blank surface, never over a visible edge.
                    keep = behind.max(axis=2) < 128
                    keep &= pixels.min(axis=2) > 225
                    pixels = pixels.copy()
                    pixels[keep] = HIDDEN_EDGE
                if style != "xray":
                    pixels = draw_outlines(pixels, world_height, part, pixel_world, factor)
                small_height = world_height[center::factor, center::factor][:height, :width]
                image = downsample(pixels, factor)
                if style == "shaded" and not scene.data_colors:
                    image = apply_gain(image, cavity_gain(small_height, face_ids >= 0, pixel_world * factor))
                frames.append(
                    Frame(image=Image.fromarray(image, mode="RGB"), face_ids=face_ids, height=small_height.copy())
                )
            return frames
        finally:
            for renderer in filter(None, (id_renderer, beauty, hidden)):
                renderer.RemoveAllViewProps()
            window.Finalize()

    def _pass(self, window: Any, renderer: Any, camera: CameraSpec, scene: RenderScene, width: int, height: int, *, lit: bool = True) -> Any:
        window.AddRenderer(renderer)
        try:
            self._aim(renderer, camera, scene, lit=lit)
            return self._capture(window, width, height)
        finally:
            window.RemoveRenderer(renderer)

    def _depth(self, window: Any, width: int, height: int) -> Any:
        """Depth left by the last pass. It stays readable after that pass's renderer is removed."""
        from vtk.util.numpy_support import vtk_to_numpy

        buffer = self._vtk.vtkFloatArray()
        window.GetZbufferData(0, 0, int(width) - 1, int(height) - 1, buffer)
        return np.flipud(vtk_to_numpy(buffer).reshape((int(height), int(width))))

    def _window(self, width: int, height: int) -> Any:
        window = self._vtk.vtkRenderWindow()
        window.SetOffScreenRendering(1)
        window.SetShowWindow(False)
        window.SetMultiSamples(0)  # Hardware antialiasing can vary between runs; we supersample instead.
        window.SetAlphaBitPlanes(0)
        window.SetSize(int(width), int(height))
        window.SetWindowName("AIAD offscreen render")
        return window

    def _clipped(self, mapper: Any, clip_box: Any) -> Any:
        if clip_box is None:
            return mapper
        low, high = clip_box
        for axis in range(3):
            for bound, sign in ((low, 1.0), (high, -1.0)):
                plane = self._vtk.vtkPlane()
                origin = [0.0, 0.0, 0.0]
                normal = [0.0, 0.0, 0.0]
                origin[axis] = bound[axis]
                normal[axis] = sign
                plane.SetOrigin(*origin)
                plane.SetNormal(*normal)
                mapper.AddClippingPlane(plane)
        return mapper

    def _surface_mappers(self, scene: RenderScene, clip_box: Any) -> tuple[Any, Any]:
        """A lit mapper with smooth normals and an unlit one whose colors encode face numbers."""
        vtk = self._vtk
        surface = self._surface_polydata(scene)
        if not surface.GetNumberOfCells():
            return None, None
        normals = vtk.vtkPolyDataNormals()
        normals.SetInputData(surface)
        normals.ComputePointNormalsOn()
        normals.ComputeCellNormalsOff()
        normals.ConsistencyOff()
        normals.SplittingOff()
        normals.AutoOrientNormalsOff()
        normals.Update()
        lit = vtk.vtkPolyDataMapper()
        lit.SetInputConnection(normals.GetOutputPort())
        lit.SetScalarModeToUseCellFieldData()
        lit.SelectColorArray("colors")
        lit.SetColorModeToDirectScalars()
        lit.ScalarVisibilityOn()
        ids = vtk.vtkPolyDataMapper()
        ids.SetInputData(surface)
        ids.SetScalarModeToUseCellFieldData()
        ids.SelectColorArray("ids")
        ids.SetColorModeToDirectScalars()
        ids.ScalarVisibilityOn()
        return self._clipped(lit, clip_box), self._clipped(ids, clip_box)

    def _line_mappers(self, scene: RenderScene, style: str, clip_box: Any) -> dict[str, Any]:
        """One mapper per line weight, since VTK sets width per actor."""
        palette = EDGE_COLORS[style]
        groups: dict[str, list[tuple[Any, tuple[int, int, int]]]] = {}
        for line in scene.lines:
            if len(line.points) < 2:
                continue
            if line.highlighted:
                groups.setdefault("highlight", []).append((line.points, HIGHLIGHT_EDGE))
            elif line.role == "hatch":
                groups.setdefault("hatch", []).append((line.points, HATCH_COLOR))
            elif line.role != "seam" or style == "xray":
                groups.setdefault(line.role, []).append((line.points, palette.get(line.role, palette["edge"])))
        mappers = {}
        for role in sorted(groups):
            mapper = self._vtk.vtkPolyDataMapper()
            mapper.SetInputData(self._polylines(groups[role]))
            mapper.SetScalarModeToUseCellData()
            mapper.SetColorModeToDirectScalars()
            mapper.ScalarVisibilityOn()
            # B-rep edges sit on true curves while faces are chords, so an edge can fall just behind
            # its face. This offset covers that sag and still hides edges behind solid material.
            mapper.SetResolveCoincidentTopologyToPolygonOffset()
            mapper.SetRelativeCoincidentTopologyLineOffsetParameters(-1.0, LINE_DEPTH_OFFSET)
            mappers[role] = self._clipped(mapper, clip_box)
        return mappers

    def _base_renderer(self) -> Any:
        renderer = self._vtk.vtkRenderer()
        renderer.SetBackground(*BACKGROUND)
        renderer.GradientBackgroundOff()
        renderer.AutomaticLightCreationOff()
        return renderer

    def _id_renderer(self, mapper: Any) -> Any:
        renderer = self._vtk.vtkRenderer()
        renderer.SetBackground(0.0, 0.0, 0.0)
        renderer.GradientBackgroundOff()
        renderer.AutomaticLightCreationOff()
        if mapper is not None:
            actor = self._vtk.vtkActor()
            actor.SetMapper(mapper)
            actor.GetProperty().SetLighting(False)
            renderer.AddActor(actor)
        return renderer

    def _beauty_renderer(self, surface: Any, lines: dict[str, Any], style: str, factor: int, *, data_colors: bool = False) -> Any:
        vtk = self._vtk
        renderer = self._base_renderer()
        if surface is not None:
            actor = vtk.vtkActor()
            actor.SetMapper(surface)
            prop = actor.GetProperty()
            prop.SetAmbient(0.34)
            prop.SetDiffuse(0.66)
            prop.SetSpecular(0.22)
            prop.SetSpecularPower(42.0)
            prop.SetSpecularColor(1.0, 1.0, 1.0)
            prop.SetInterpolationToPhong()
            if style == "lines":
                prop.SetAmbient(1.0)
                prop.SetDiffuse(0.0)
                prop.SetSpecular(0.0)
            elif style == "xray":
                prop.SetOpacity(0.28)
            elif data_colors:  # Colors carry values here, so keep them close to the legend's.
                prop.SetAmbient(0.62)
                prop.SetDiffuse(0.42)
                prop.SetSpecular(0.04)
            renderer.AddActor(actor)
        for role, mapper in lines.items():
            line_actor = vtk.vtkActor()
            line_actor.SetMapper(mapper)
            line_actor.GetProperty().SetLighting(False)
            line_actor.GetProperty().SetLineWidth(EDGE_WIDTHS.get(role, 1.2) * factor)
            renderer.AddActor(line_actor)
        return renderer

    def _hidden_renderer(self, scene: RenderScene, clip_box: Any, factor: int) -> Any:
        """Every real edge with no surface in the way; compositing keeps those behind material."""
        renderer = self._vtk.vtkRenderer()
        renderer.SetBackground(1.0, 1.0, 1.0)
        renderer.GradientBackgroundOff()
        renderer.AutomaticLightCreationOff()
        edges = [(line.points, (0, 0, 0)) for line in scene.lines if line.role in ("edge", "tangent") and len(line.points) >= 2]
        if edges:
            mapper = self._vtk.vtkPolyDataMapper()
            mapper.SetInputData(self._polylines(edges))
            mapper.SetScalarModeToUseCellData()
            mapper.SetColorModeToDirectScalars()
            actor = self._vtk.vtkActor()
            actor.SetMapper(self._clipped(mapper, clip_box))
            actor.GetProperty().SetLighting(False)
            actor.GetProperty().SetLineWidth(1.0 * factor)
            renderer.AddActor(actor)
        return renderer

    def _aim(self, renderer: Any, camera: CameraSpec, scene: RenderScene, *, lit: bool = True) -> None:
        minimum, maximum = scene.bounds
        center = tuple((minimum[i] + maximum[i]) * 0.5 for i in range(3))
        toward = tuple(camera.position[i] - camera.focal_point[i] for i in range(3))
        length = max(sum(c * c for c in toward) ** 0.5, 1.0e-12)
        along = sum((center[i] - camera.focal_point[i]) * toward[i] / length for i in range(3))
        reach = abs(along) + self._diagonal(scene) * 0.55 + 1.0e-6
        vtk_camera = renderer.GetActiveCamera()
        vtk_camera.ParallelProjectionOn()
        vtk_camera.SetFocalPoint(*camera.focal_point)
        vtk_camera.SetPosition(*camera.position)
        vtk_camera.SetViewUp(*camera.view_up)
        vtk_camera.OrthogonalizeViewUp()
        vtk_camera.SetParallelScale(camera.parallel_scale)
        vtk_camera.SetClippingRange(max(camera.distance - reach, 1.0e-6), camera.distance + reach)
        renderer.RemoveAllLights()
        if lit:
            self._add_lights(renderer, camera, scene)

    def _capture(self, window: Any, width: int, height: int) -> Any:
        from vtk.util.numpy_support import vtk_to_numpy

        window.Render()
        capture = self._vtk.vtkWindowToImageFilter()
        capture.SetInput(window)
        capture.SetInputBufferTypeToRGB()
        capture.ReadFrontBufferOff()
        capture.Update()
        scalars = capture.GetOutput().GetPointData().GetScalars()
        if scalars is None:
            raise RuntimeError("VTK offscreen rendering produced no RGB pixels")
        pixels = vtk_to_numpy(scalars).reshape((int(height), int(width), 3))
        return np.ascontiguousarray(np.flipud(pixels))

    def _surface_polydata(self, scene: RenderScene) -> Any:
        from vtk.util.numpy_support import numpy_to_vtk, numpy_to_vtkIdTypeArray

        vtk = self._vtk
        all_points, all_cells, all_colors, all_ids = [], [], [], []
        start = 0
        for face in scene.faces:
            if not len(face.triangles):
                continue
            points = np.asarray(face.points, dtype=np.float64)
            triangles = np.asarray(face.triangles, dtype=np.int64)
            all_points.append(points)
            all_cells.append(triangles + start)
            start += len(points)
            count = len(triangles)
            if len(face.triangle_colors):
                all_colors.append(np.asarray(face.triangle_colors, dtype=np.uint8))
            else:
                all_colors.append(np.tile(np.asarray(face.color, dtype=np.uint8), (count, 1)))
            code = face.index + 1
            all_ids.append(np.tile(np.asarray([code >> 16 & 255, code >> 8 & 255, code & 255], dtype=np.uint8), (count, 1)))
        polydata = vtk.vtkPolyData()
        if not all_cells:
            return polydata
        points = vtk.vtkPoints()
        points.SetData(numpy_to_vtk(np.concatenate(all_points), deep=True))
        polygons = vtk.vtkCellArray()
        polygons.SetData(3, numpy_to_vtkIdTypeArray(np.concatenate(all_cells).ravel(), deep=True))
        polydata.SetPoints(points)
        polydata.SetPolys(polygons)
        for name, values in (("colors", all_colors), ("ids", all_ids)):
            array = numpy_to_vtk(np.concatenate(values), deep=True, array_type=vtk.VTK_UNSIGNED_CHAR)
            array.SetName(name)
            polydata.GetCellData().AddArray(array)
        return polydata

    def _polylines(self, lines: list[tuple[Any, tuple[int, int, int]]]) -> Any:
        from vtk.util.numpy_support import numpy_to_vtk

        vtk = self._vtk
        points = vtk.vtkPoints()
        points.SetData(numpy_to_vtk(np.concatenate([np.asarray(p, dtype=np.float64) for p, _ in lines]), deep=True))
        cells = vtk.vtkCellArray()
        start = 0
        for line_points, _ in lines:
            cells.InsertNextCell(len(line_points))
            for offset in range(len(line_points)):
                cells.InsertCellPoint(start + offset)
            start += len(line_points)
        colors = numpy_to_vtk(np.asarray([color for _, color in lines], dtype=np.uint8), deep=True, array_type=vtk.VTK_UNSIGNED_CHAR)
        data = vtk.vtkPolyData()
        data.SetPoints(points)
        data.SetLines(cells)
        data.GetCellData().SetScalars(colors)
        return data

    def _add_lights(self, renderer: Any, camera: CameraSpec, scene: RenderScene) -> None:
        """Lights follow the camera, so a face turned toward the viewer is never left dark."""
        vtk = self._vtk
        center = camera.focal_point
        radius = max(self._diagonal(scene), 1.0) * 1.8
        toward = tuple(camera.position[i] - center[i] for i in range(3))
        length = max(sum(c * c for c in toward) ** 0.5, 1.0e-12)
        toward = tuple(c / length for c in toward)
        for (right, up, front), intensity in (
            ((0.42, 0.62, 0.66), 0.72),
            ((-0.72, -0.12, 0.68), 0.30),
            ((0.10, 0.92, 0.38), 0.16),
        ):
            direction = tuple(
                camera.right[i] * right + camera.up[i] * up + toward[i] * front for i in range(3)
            )
            light = vtk.vtkLight()
            light.SetLightTypeToSceneLight()
            light.SetPosition(*(center[i] + direction[i] * radius for i in range(3)))
            light.SetFocalPoint(*center)
            light.SetColor(1.0, 1.0, 1.0)
            light.SetIntensity(intensity)
            light.PositionalOff()
            renderer.AddLight(light)

    @staticmethod
    def _diagonal(scene: RenderScene) -> float:
        minimum, maximum = scene.bounds
        return sum((maximum[i] - minimum[i]) ** 2 for i in range(3)) ** 0.5
