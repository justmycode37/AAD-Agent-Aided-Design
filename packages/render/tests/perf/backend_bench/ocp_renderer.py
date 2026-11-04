"""OCCT V3d offscreen rendering through cadquery-ocp-novtk."""

from __future__ import annotations

from io import BytesIO

import numpy as np
from PIL import Image

from common import BACKGROUND, EDGE_COLOR, FACE_COLOR, PANE_SIZE, VIEWS, camera_for, pane_with_label, save_sheet


def _color(Quantity_Color, Quantity_TOC_RGB, values):
    return Quantity_Color(*(float(value) for value in values), Quantity_TOC_RGB)


def render(mesh, output, part):
    from OCP.AIS import AIS_InteractiveContext, AIS_Shape
    from OCP.Aspect import Aspect_DisplayConnection, Aspect_NeutralWindow, Aspect_TOL_SOLID
    from OCP.Graphic3d import (
        Graphic3d_BufferType,
        Graphic3d_Camera,
        Graphic3d_TypeOfBackfacingModel,
    )
    from OCP.Image import Image_AlienPixMap, Image_Format_RGB
    from OCP.OpenGl import OpenGl_GraphicDriver
    from OCP.Prs3d import Prs3d_LineAspect
    from OCP.Quantity import Quantity_Color, Quantity_TOC_RGB
    from OCP.TCollection import TCollection_AsciiString
    from OCP.V3d import V3d_Viewer
    from OCP.gp import gp_Dir, gp_Pnt

    display = Aspect_DisplayConnection()
    driver = OpenGl_GraphicDriver(display)
    viewer = V3d_Viewer(driver)
    viewer.SetDefaultLights()
    viewer.SetLightOn()
    context = AIS_InteractiveContext(viewer)
    view = viewer.CreateView()

    native_window = Aspect_NeutralWindow()
    native_window.SetSize(PANE_SIZE, PANE_SIZE)
    # Do not set the neutral window virtual: on macOS it must create an internal
    # NSOpenGLContext so V3d_View.ToPixMap can render into its offscreen buffer.
    view.SetWindow(native_window)
    view.MustBeResized()
    view.SetBackgroundColor(_color(Quantity_Color, Quantity_TOC_RGB, np.array(BACKGROUND) / 255.0))

    ais_shape = AIS_Shape(part.wrapped)
    face_color = _color(Quantity_Color, Quantity_TOC_RGB, FACE_COLOR / 255.0)
    ais_shape.SetColor(face_color)
    ais_shape.SetDisplayMode(1)  # AIS_Shaded
    drawer = ais_shape.Attributes()
    shading = drawer.ShadingAspect()
    shading.SetColor(face_color)
    shading.Aspect().SetInteriorColor(face_color)
    shading.Aspect().SetBackInteriorColor(face_color)
    shading.Aspect().SetFaceCulling(Graphic3d_TypeOfBackfacingModel.Graphic3d_TypeOfBackfacingModel_DoubleSided)
    drawer.SetFaceBoundaryDraw(True)
    line_aspect = Prs3d_LineAspect(
        _color(Quantity_Color, Quantity_TOC_RGB, np.array(EDGE_COLOR) / 255.0),
        Aspect_TOL_SOLID,
        1.0,
    )
    drawer.SetFaceBoundaryAspect(line_aspect)
    context.Display(ais_shape, 1, -1, True)

    camera = view.Camera()
    camera.SetProjectionType(Graphic3d_Camera.Projection_e.Projection_Orthographic)
    camera.SetAspect(1.0)
    camera.SetZRange(-500.0, 500.0)
    pixmap = Image_AlienPixMap()
    if not pixmap.InitTrash(Image_Format_RGB, PANE_SIZE, PANE_SIZE):
        raise RuntimeError("Image_PixMap.InitTrash failed")

    panes = []
    for label, direction, view_up in VIEWS:
        spec = camera_for(mesh, direction, view_up)
        eye = spec.target - spec.forward * 200.0
        camera.SetCenter(gp_Pnt(*map(float, spec.target)))
        camera.SetEye(gp_Pnt(*map(float, eye)))
        camera.SetUp(gp_Dir(*map(float, spec.up)))
        camera.OrthogonalizeUp()
        # Graphic3d_Camera.Scale is full viewport size; camera_for uses a half extent.
        camera.SetScale(spec.scale * 2.0)
        view.SetCamera(camera)
        if not view.ToPixMap(pixmap, PANE_SIZE, PANE_SIZE, Graphic3d_BufferType.Graphic3d_BT_RGB):
            raise RuntimeError("V3d_View.ToPixMap returned False")
        encoded = BytesIO()
        if not pixmap.Save(encoded, TCollection_AsciiString("png")):
            raise RuntimeError("Image_AlienPixMap.Save to PNG failed")
        panes.append(pane_with_label(Image.open(BytesIO(encoded.getvalue())).convert("RGB"), label))

    view.Remove()
    return save_sheet(panes, output)

