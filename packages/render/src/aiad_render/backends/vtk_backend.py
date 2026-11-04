"""VTK offscreen renderer. The VTK package is imported only at construction."""

from __future__ import annotations

import ctypes.util
import os
import sys
from typing import Any

from .protocol import CameraSpec, RenderFace, RenderLine, RenderScene

SUPERSAMPLE = 2
MAX_RENDER_PIXELS = 4096
LINE_DEPTH_OFFSET = -20.0


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
    return SUPERSAMPLE if size * SUPERSAMPLE <= MAX_RENDER_PIXELS else 1


def _downsample(image: Any, size: tuple[int, int], factor: int) -> Any:
    if factor == 1:
        return image
    from PIL import Image

    return image.resize(size, Image.Resampling.BOX)


class VtkBackend:
    """VTK OpenGL renderer configured for repeatable offscreen PNG pixels."""

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
        return self.render_many(scene, [camera], width=width, height=height, style=style)[0]

    def render_many(
        self,
        scene: RenderScene,
        cameras: list[CameraSpec],
        *,
        width: int,
        height: int,
        style: str,
    ) -> list[Any]:
        """Render several views through one offscreen window."""
        from PIL import Image

        if not cameras:
            return []
        factor = _supersample(max(width, height))
        window = self._window(width * factor, height * factor)
        mappers = self._mappers(scene, style)
        renderer = self._renderer(mappers, style, factor)
        window.AddRenderer(renderer)
        try:
            images = []
            for camera in cameras:
                self._aim(renderer, camera, scene)
                pixels = self._capture(window, width * factor, height * factor)
                images.append(_downsample(Image.fromarray(pixels, mode="RGB"), (width, height), factor))
            return images
        finally:
            renderer.RemoveAllViewProps()
            window.Finalize()

    def render_sheet(
        self,
        scene: RenderScene,
        cameras: list[CameraSpec],
        *,
        canvas_size: int,
        pane_width: int,
        pane_height: int,
        positions: tuple[tuple[int, int], ...],
        style: str,
    ) -> list[Any]:
        """Render a 4-up sheet in one window pass and crop its four viewports."""
        from PIL import Image

        if len(cameras) != len(positions):
            raise ValueError("each sheet camera needs a pane position")
        if not cameras:
            return []
        factor = _supersample(canvas_size)
        size = canvas_size * factor
        window = self._window(size, size)
        mappers = self._mappers(scene, style)
        renderers = []
        for camera, (x, y) in zip(cameras, positions, strict=True):
            renderer = self._renderer(mappers, style, factor)
            renderer.SetViewport(
                x / canvas_size,
                (canvas_size - y - pane_height) / canvas_size,
                (x + pane_width) / canvas_size,
                (canvas_size - y) / canvas_size,
            )
            self._aim(renderer, camera, scene)
            window.AddRenderer(renderer)
            renderers.append(renderer)
        try:
            pixels = self._capture(window, size, size)
            panes = []
            for x, y in positions:
                crop = pixels[y * factor : (y + pane_height) * factor, x * factor : (x + pane_width) * factor]
                panes.append(_downsample(Image.fromarray(crop.copy(), mode="RGB"), (pane_width, pane_height), factor))
            return panes
        finally:
            for renderer in renderers:
                renderer.RemoveAllViewProps()
            window.Finalize()

    def _window(self, width: int, height: int) -> Any:
        window = self._vtk.vtkRenderWindow()
        window.SetOffScreenRendering(1)
        window.SetMultiSamples(0)  # Hardware antialiasing can vary between runs; we supersample instead.
        window.SetAlphaBitPlanes(0)
        window.SetSize(int(width), int(height))
        window.SetWindowName("AIAD offscreen render")
        return window

    def _mappers(self, scene: RenderScene, style: str) -> tuple[Any, Any]:
        vtk = self._vtk
        surface_mapper = None
        surface = self._surface_polydata(scene)
        if surface.GetNumberOfCells():
            normals = vtk.vtkPolyDataNormals()
            normals.SetInputData(surface)
            normals.ComputePointNormalsOn()
            normals.ComputeCellNormalsOff()
            normals.ConsistencyOn()
            normals.SplittingOff()
            normals.AutoOrientNormalsOff()
            normals.Update()
            surface_mapper = vtk.vtkPolyDataMapper()
            surface_mapper.SetInputConnection(normals.GetOutputPort())
            surface_mapper.SetScalarModeToUseCellData()
            surface_mapper.SetColorModeToDirectScalars()
            surface_mapper.ScalarVisibilityOn()

        line_mapper = None
        line_data = self._line_polydata(scene, style)
        if line_data.GetNumberOfCells():
            line_mapper = vtk.vtkPolyDataMapper()
            line_mapper.SetInputData(line_data)
            line_mapper.SetScalarModeToUseCellData()
            line_mapper.SetColorModeToDirectScalars()
            line_mapper.ScalarVisibilityOn()
            # B-rep edges sit on true curves while faces are chords, so an edge can fall just behind
            # its face. This offset covers that sag and still hides edges behind solid material.
            line_mapper.SetResolveCoincidentTopologyToPolygonOffset()
            line_mapper.SetRelativeCoincidentTopologyLineOffsetParameters(-1.0, LINE_DEPTH_OFFSET)
        return surface_mapper, line_mapper

    def _renderer(self, mappers: tuple[Any, Any], style: str, factor: int) -> Any:
        vtk = self._vtk
        surface_mapper, line_mapper = mappers
        renderer = vtk.vtkRenderer()
        renderer.SetBackground(0.957, 0.965, 0.973)
        renderer.GradientBackgroundOff()
        renderer.AutomaticLightCreationOff()
        if surface_mapper is not None:
            actor = vtk.vtkActor()
            actor.SetMapper(surface_mapper)
            prop = actor.GetProperty()
            prop.SetAmbient(0.36)
            prop.SetDiffuse(0.64)
            prop.SetSpecular(0.08)
            prop.SetSpecularPower(24.0)
            prop.SetInterpolationToPhong()
            if style == "lines":
                prop.SetAmbient(0.92)
                prop.SetDiffuse(0.08)
                prop.SetSpecular(0.0)
            elif style == "xray":
                prop.SetOpacity(0.32)
            renderer.AddActor(actor)
        if line_mapper is not None:
            line_actor = vtk.vtkActor()
            line_actor.SetMapper(line_mapper)
            line_actor.GetProperty().SetLighting(False)
            line_actor.GetProperty().SetLineWidth((1.15 if style == "shaded" else 1.35) * factor)
            renderer.AddActor(line_actor)
        return renderer

    def _aim(self, renderer: Any, camera: CameraSpec, scene: RenderScene) -> None:
        diagonal = self._diagonal(scene)
        vtk_camera = renderer.GetActiveCamera()
        vtk_camera.ParallelProjectionOn()
        vtk_camera.SetFocalPoint(*camera.focal_point)
        vtk_camera.SetPosition(*camera.position)
        vtk_camera.SetViewUp(*camera.view_up)
        vtk_camera.OrthogonalizeViewUp()
        vtk_camera.SetParallelScale(camera.parallel_scale)
        vtk_camera.SetClippingRange(
            max(camera.distance - 3.0 * diagonal, 1.0e-4),
            camera.distance + 3.0 * diagonal,
        )
        renderer.RemoveAllLights()
        self._add_lights(renderer, camera, scene)

    def _capture(self, window: Any, width: int, height: int) -> Any:
        import numpy as np
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

    def _surface_polydata(self, scene: RenderScene, *, include_colors: bool = True) -> Any:
        vtk = self._vtk
        points = vtk.vtkPoints()
        polygons = vtk.vtkCellArray()
        colors = vtk.vtkUnsignedCharArray()
        colors.SetName("AIAD face colors")
        colors.SetNumberOfComponents(3)

        for face in scene.faces:
            if not face.triangles:
                continue
            point_start = points.GetNumberOfPoints()
            for point in face.points:
                points.InsertNextPoint(*point)
            for triangle in face.triangles:
                polygons.InsertNextCell(3)
                for local_index in triangle:
                    polygons.InsertCellPoint(point_start + int(local_index))
                if include_colors:
                    colors.InsertNextTuple3(*face.color)

        polydata = vtk.vtkPolyData()
        polydata.SetPoints(points)
        polydata.SetPolys(polygons)
        if include_colors and colors.GetNumberOfTuples():
            polydata.GetCellData().SetScalars(colors)
        return polydata

    def _line_polydata(self, scene: RenderScene, style: str) -> Any:
        vtk = self._vtk
        points = vtk.vtkPoints()
        cells = vtk.vtkCellArray()
        colors = vtk.vtkUnsignedCharArray()
        colors.SetName("AIAD edge colors")
        colors.SetNumberOfComponents(3)
        default_edge = {
            "shaded": (48, 55, 64),
            "lines": (27, 35, 44),
            "xray": (66, 81, 97),
        }[style]
        for line in scene.lines:
            if len(line.points) < 2:
                continue
            line_cell = vtk.vtkPolyLine()
            ids = line_cell.GetPointIds()
            ids.SetNumberOfIds(len(line.points))
            for local_index, point in enumerate(line.points):
                ids.SetId(local_index, points.InsertNextPoint(*point))
            cells.InsertNextCell(line_cell)
            if line.role == "hatch":
                color = (55, 59, 63)
            elif line.highlighted:
                color = (255, 79, 22)
            else:
                color = default_edge
            colors.InsertNextTuple3(*color)

        data = vtk.vtkPolyData()
        data.SetPoints(points)
        data.SetLines(cells)
        if colors.GetNumberOfTuples():
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
            ((0.40, 0.55, 0.73), 0.66),
            ((-0.70, -0.10, 0.70), 0.34),
            ((0.10, 0.95, 0.30), 0.14),
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
