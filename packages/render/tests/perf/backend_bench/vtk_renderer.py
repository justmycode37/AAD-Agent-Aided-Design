"""VTK 9.7.1 offscreen baseline using the shared mesh and camera definitions."""

from __future__ import annotations

import numpy as np
from PIL import Image

from common import (
    BACKGROUND,
    EDGE_COLOR,
    FACE_COLOR,
    LIGHT,
    PANE_SIZE,
    VIEWS,
    camera_for,
    pane_with_label,
    save_sheet,
)


def _polydata(mesh):
    from vtkmodules.util.numpy_support import numpy_to_vtk, numpy_to_vtkIdTypeArray
    from vtkmodules.vtkCommonCore import vtkPoints
    from vtkmodules.vtkCommonDataModel import vtkCellArray, vtkPolyData

    points = vtkPoints()
    points.SetData(numpy_to_vtk(np.ascontiguousarray(mesh.vertices, dtype=np.float64), deep=True))
    normal_array = numpy_to_vtk(np.ascontiguousarray(mesh.normals, dtype=np.float32), deep=True)
    normal_array.SetName("Normals")

    triangle_cells = np.empty((len(mesh.triangles), 4), dtype=np.int64)
    triangle_cells[:, 0] = 3
    triangle_cells[:, 1:] = mesh.triangles
    polys = vtkCellArray()
    polys.SetCells(len(triangle_cells), numpy_to_vtkIdTypeArray(triangle_cells.ravel(), deep=True))

    polydata = vtkPolyData()
    polydata.SetPoints(points)
    polydata.SetPolys(polys)
    polydata.GetPointData().SetNormals(normal_array)
    return polydata


def _edge_polydata(mesh):
    from vtkmodules.vtkCommonCore import vtkPoints
    from vtkmodules.vtkCommonDataModel import vtkCellArray, vtkPolyData

    points = vtkPoints()
    lines = vtkCellArray()
    for polyline in mesh.edges:
        point_ids = [points.InsertNextPoint(*map(float, point)) for point in polyline]
        for left, right in zip(point_ids, point_ids[1:]):
            lines.InsertNextCell(2)
            lines.InsertCellPoint(left)
            lines.InsertCellPoint(right)
    polydata = vtkPolyData()
    polydata.SetPoints(points)
    polydata.SetLines(lines)
    return polydata


def render(mesh, output):
    from vtkmodules.util.numpy_support import vtk_to_numpy
    import vtkmodules.vtkRenderingOpenGL2  # Registers the native OpenGL render-window implementation.
    from vtkmodules.vtkRenderingCore import (
        vtkActor,
        vtkCamera,
        vtkLight,
        vtkPolyDataMapper,
        vtkRenderer,
        vtkRenderWindow,
        vtkWindowToImageFilter,
    )

    face_mapper = vtkPolyDataMapper()
    face_mapper.SetInputData(_polydata(mesh))
    face_actor = vtkActor()
    face_actor.SetMapper(face_mapper)
    face_property = face_actor.GetProperty()
    face_property.SetColor(*(FACE_COLOR / 255.0))
    face_property.SetAmbient(0.34)
    face_property.SetDiffuse(0.66)
    face_property.SetSpecular(0.0)
    face_property.SetInterpolationToPhong()

    edge_mapper = vtkPolyDataMapper()
    edge_mapper.SetInputData(_edge_polydata(mesh))
    edge_actor = vtkActor()
    edge_actor.SetMapper(edge_mapper)
    edge_actor.GetProperty().SetColor(*(component / 255.0 for component in EDGE_COLOR))
    edge_actor.GetProperty().SetLineWidth(1.0)
    edge_actor.GetProperty().SetLighting(False)

    renderer = vtkRenderer()
    renderer.SetBackground(*(component / 255.0 for component in BACKGROUND))
    renderer.AutomaticLightCreationOff()
    renderer.SetLightFollowCamera(False)
    light = vtkLight()
    light.SetLightTypeToSceneLight()
    light.SetPositional(False)
    light.SetPosition(*(LIGHT * 200.0))
    light.SetFocalPoint(0.0, 0.0, 0.0)
    renderer.AddLight(light)
    renderer.AddActor(face_actor)
    renderer.AddActor(edge_actor)

    window = vtkRenderWindow()
    window.SetOffScreenRendering(1)
    window.SetShowWindow(False)
    window.SetMultiSamples(0)
    window.SetSize(PANE_SIZE, PANE_SIZE)
    window.AddRenderer(renderer)
    camera = vtkCamera()
    camera.SetParallelProjection(True)
    renderer.SetActiveCamera(camera)

    capture = vtkWindowToImageFilter()
    capture.SetInput(window)
    capture.SetInputBufferTypeToRGB()
    capture.ReadFrontBufferOff()

    panes = []
    for label, direction, view_up in VIEWS:
        spec = camera_for(mesh, direction, view_up)
        camera.SetFocalPoint(*spec.target)
        camera.SetPosition(*(spec.target - spec.forward * 200.0))
        camera.SetViewUp(*spec.up)
        camera.SetParallelScale(spec.scale)
        camera.OrthogonalizeViewUp()
        window.Render()
        capture.Modified()
        capture.Update()
        pixels = vtk_to_numpy(capture.GetOutput().GetPointData().GetScalars())
        pixels = pixels.reshape(PANE_SIZE, PANE_SIZE, 3)[::-1]
        panes.append(pane_with_label(Image.fromarray(pixels.astype(np.uint8), "RGB"), label))
    window.Finalize()
    return save_sheet(panes, output)

