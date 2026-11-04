"""Shared, deterministic CAD fixture and camera helpers for backend_bench."""

from __future__ import annotations

from dataclasses import dataclass
import math
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont


PANE_SIZE = 1024
VIEWS = (
    ("FRONT", (0.0, -1.0, 0.0), (0.0, 0.0, 1.0)),
    ("RIGHT", (1.0, 0.0, 0.0), (0.0, 0.0, 1.0)),
    ("TOP", (0.0, 0.0, 1.0), (0.0, 1.0, 0.0)),
    ("ISO", (1.0, -1.0, 0.85), (0.0, 0.0, 1.0)),
)
BACKGROUND = (246, 248, 250)
FACE_COLOR = np.array((166.0, 188.0, 207.0), dtype=np.float32)
EDGE_COLOR = (29, 38, 48)
LIGHT = np.array((0.35, -0.55, 0.75), dtype=np.float64)
LIGHT /= np.linalg.norm(LIGHT)


@dataclass
class Mesh:
    vertices: np.ndarray
    normals: np.ndarray
    triangles: np.ndarray
    edges: list[np.ndarray]
    face_count: int


@dataclass
class Camera:
    target: np.ndarray
    right: np.ndarray
    up: np.ndarray
    forward: np.ndarray
    scale: float


def build_bracket():
    """Return a fused bracket built with build123d 0.13.0."""
    from build123d import Align, Box, Cylinder, GeomType, Pos, Rot, fillet

    base = Box(90, 60, 10, align=(Align.CENTER, Align.CENTER, Align.MIN))
    wall = Pos(0, 24, 8) * Box(90, 12, 58, align=(Align.CENTER, Align.CENTER, Align.MIN))
    part = base + wall

    # Four through holes with shallow counterbores, cut from the top face.
    for x in (-31, 31):
        for y in (-14, 2):
            part -= Pos(x, y, 5) * Cylinder(3.7, 10)
            part -= Pos(x, y, 8.8) * Cylinder(6.2, 2.4)

    # A large through hole in the upright wall.
    part -= Pos(0, 24, 41) * Rot(90, 0, 0) * Cylinder(13, 12)

    # Soften the exposed front top edge and the two long edges at the wall top.
    fillet_edges = []
    for edge in part.edges():
        if edge.geom_type != GeomType.LINE or edge.length < 80:
            continue
        center = edge.center()
        is_long_exposed_edge = (center.Y < -29.9 and center.Z > 9.9) or center.Z > 65.9
        is_wall_corner = abs(center.X) > 44.9 and 40 < edge.length < 60 and (center.Y < 18.1 or center.Y > 29.9)
        if is_long_exposed_edge or is_wall_corner:
            fillet_edges.append(edge)
    part = fillet(fillet_edges, 1.25)

    # Add small lead-in radii to the counterbore mouths and both wall-hole lips.
    counterbore_centers = {(x, y) for x in (-31, 31) for y in (-14, 2)}
    hole_lip_edges = []
    for edge in part.edges():
        if edge.geom_type != GeomType.CIRCLE:
            continue
        center = edge.bounding_box().center()
        is_counterbore_lip = abs(center.Z - 10.0) < 0.02 and any(
            abs(center.X - x) < 0.02 and abs(center.Y - y) < 0.02 for x, y in counterbore_centers
        )
        is_wall_hole_lip = abs(center.X) < 0.02 and abs(center.Z - 41.0) < 0.02 and min(
            abs(center.Y - 18.0), abs(center.Y - 30.0)
        ) < 0.02
        if is_counterbore_lip or is_wall_hole_lip:
            hole_lip_edges.append(edge)
    part = fillet(hole_lip_edges, 0.5)

    if not part.is_valid:
        raise RuntimeError("bracket fixture is not a valid solid")
    return part


def extract_mesh(part, linear_deflection: float = 0.16, angular_deflection: float = 0.2) -> Mesh:
    """Tessellate faces and sample unique B-rep edges in world coordinates."""
    from OCP.BRep import BRep_Tool
    from OCP.BRepAdaptor import BRepAdaptor_Curve
    from OCP.BRepMesh import BRepMesh_IncrementalMesh
    from OCP.GeomAbs import GeomAbs_Line
    from OCP.TopAbs import TopAbs_EDGE, TopAbs_FACE, TopAbs_REVERSED
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopLoc import TopLoc_Location
    from OCP.TopoDS import TopoDS

    shape = part.wrapped
    mesher = BRepMesh_IncrementalMesh(shape, linear_deflection, False, angular_deflection, True)
    mesher.Perform()
    if not mesher.IsDone():
        raise RuntimeError("OCCT BRepMesh_IncrementalMesh did not finish")

    vertices: list[np.ndarray] = []
    triangles: list[np.ndarray] = []
    normals: list[np.ndarray] = []
    face_count = 0
    face_iter = TopExp_Explorer(shape, TopAbs_FACE)
    while face_iter.More():
        face = TopoDS.Face(face_iter.Current())
        location = TopLoc_Location()
        triangulation = BRep_Tool.Triangulation_s(face, location)
        if triangulation is not None:
            face_count += 1
            transform = location.Transformation()
            offset = sum(len(v) for v in vertices)
            face_vertices = np.empty((triangulation.NbNodes(), 3), dtype=np.float64)
            for node_index in range(1, triangulation.NbNodes() + 1):
                point = triangulation.Node(node_index).Transformed(transform)
                face_vertices[node_index - 1] = (point.X(), point.Y(), point.Z())
            face_triangles = np.empty((triangulation.NbTriangles(), 3), dtype=np.int32)
            for tri_index in range(1, triangulation.NbTriangles() + 1):
                indices = triangulation.Triangle(tri_index).Get()
                if face.Orientation() == TopAbs_REVERSED:
                    indices = (indices[0], indices[2], indices[1])
                face_triangles[tri_index - 1] = np.asarray(indices, dtype=np.int32) - 1 + offset

            face_normals = np.zeros_like(face_vertices)
            local_tris = face_triangles - offset
            tri_points = face_vertices[local_tris]
            tri_normals = np.cross(tri_points[:, 1] - tri_points[:, 0], tri_points[:, 2] - tri_points[:, 0])
            for corner in range(3):
                np.add.at(face_normals, local_tris[:, corner], tri_normals)
            lengths = np.linalg.norm(face_normals, axis=1)
            nonzero = lengths > 0
            face_normals[nonzero] /= lengths[nonzero, None]
            vertices.append(face_vertices)
            triangles.append(face_triangles)
            normals.append(face_normals)
        face_iter.Next()

    if not vertices or not triangles:
        raise RuntimeError("OCCT produced no face triangulations")

    edges: list[np.ndarray] = []
    unique_edges = []
    edge_iter = TopExp_Explorer(shape, TopAbs_EDGE)
    while edge_iter.More():
        edge = TopoDS.Edge(edge_iter.Current())
        if any(edge.IsSame(previous) for previous in unique_edges):
            edge_iter.Next()
            continue
        unique_edges.append(edge)
        curve = BRepAdaptor_Curve(edge)
        first, last = curve.FirstParameter(), curve.LastParameter()
        if not (math.isfinite(first) and math.isfinite(last)) or first == last:
            edge_iter.Next()
            continue
        segments = 1 if curve.GetType() == GeomAbs_Line else 64
        polyline = np.empty((segments + 1, 3), dtype=np.float64)
        for i in range(segments + 1):
            point = curve.Value(first + (last - first) * i / segments)
            polyline[i] = (point.X(), point.Y(), point.Z())
        edges.append(polyline)
        edge_iter.Next()

    return Mesh(
        vertices=np.concatenate(vertices).astype(np.float32),
        normals=np.concatenate(normals).astype(np.float32),
        triangles=np.concatenate(triangles).astype(np.int32),
        edges=edges,
        face_count=face_count,
    )


def camera_for(mesh: Mesh, direction, up, pane_size: int = PANE_SIZE) -> Camera:
    bounds_min = np.min(mesh.vertices, axis=0)
    bounds_max = np.max(mesh.vertices, axis=0)
    target = (bounds_min + bounds_max) * 0.5
    camera_direction = np.asarray(direction, dtype=np.float64)
    camera_direction /= np.linalg.norm(camera_direction)
    forward = -camera_direction
    right = np.cross(forward, np.asarray(up, dtype=np.float64))
    right /= np.linalg.norm(right)
    true_up = np.cross(right, forward)
    true_up /= np.linalg.norm(true_up)
    relative = mesh.vertices.astype(np.float64) - target
    half_extent = max(np.max(np.abs(relative @ right)), np.max(np.abs(relative @ true_up)))
    scale = float(half_extent * 1.12)
    return Camera(target, right, true_up, forward, scale)


def project_points(points: np.ndarray, camera: Camera, pane_size: int = PANE_SIZE):
    relative = points.astype(np.float64) - camera.target
    x = relative @ camera.right
    y = relative @ camera.up
    depth = -(relative @ camera.forward)
    screen = np.column_stack(
        (
            (x / camera.scale + 1.0) * (pane_size - 1) * 0.5,
            (1.0 - y / camera.scale) * (pane_size - 1) * 0.5,
        )
    ).astype(np.float32)
    return screen, depth.astype(np.float32)


def shade(normals: np.ndarray) -> np.ndarray:
    diffuse = np.maximum(normals.astype(np.float32) @ LIGHT.astype(np.float32), 0.0)
    return 0.34 + 0.66 * diffuse


def pane_with_label(image: Image.Image, label: str) -> Image.Image:
    image = image.convert("RGB")
    draw = ImageDraw.Draw(image)
    font = ImageFont.load_default()
    box = draw.textbbox((16, 14), label, font=font)
    draw.rounded_rectangle((box[0] - 5, box[1] - 4, box[2] + 5, box[3] + 4), radius=4, fill=(255, 255, 255))
    draw.text((16, 14), label, font=font, fill=(34, 45, 56))
    return image


def save_sheet(panes: list[Image.Image], output: str | Path) -> tuple[str, int]:
    if len(panes) != 4 or any(p.size != (PANE_SIZE, PANE_SIZE) for p in panes):
        raise ValueError("four 1024x1024 panes are required")
    sheet = Image.new("RGB", (PANE_SIZE * 2, PANE_SIZE * 2), BACKGROUND)
    for index, pane in enumerate(panes):
        sheet.paste(pane.convert("RGB"), ((index % 2) * PANE_SIZE, (index // 2) * PANE_SIZE))
    path = Path(output)
    path.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(path, format="PNG", optimize=False, compress_level=6)
    import hashlib

    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    return digest, path.stat().st_size

