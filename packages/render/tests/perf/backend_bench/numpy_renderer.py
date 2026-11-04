"""Small deterministic z-buffer rasterizer using NumPy and Pillow."""

from __future__ import annotations

import math

import numpy as np
from PIL import Image

from common import (
    BACKGROUND,
    EDGE_COLOR,
    FACE_COLOR,
    PANE_SIZE,
    VIEWS,
    camera_for,
    pane_with_label,
    project_points,
    save_sheet,
    shade,
)


def render_pane(mesh, label, direction, view_up):
    camera = camera_for(mesh, direction, view_up)
    screen, vertex_depth = project_points(mesh.vertices, camera)
    vertex_colors = FACE_COLOR[None, :] * shade(mesh.normals)[:, None]
    rgb = np.empty((PANE_SIZE, PANE_SIZE, 3), dtype=np.uint8)
    rgb[:] = BACKGROUND
    depth_buffer = np.full((PANE_SIZE, PANE_SIZE), -np.inf, dtype=np.float32)
    width = height = PANE_SIZE

    for indices in mesh.triangles:
        tri = screen[indices].astype(np.float64)
        z = vertex_depth[indices].astype(np.float64)
        min_x = max(0, int(math.floor(float(tri[:, 0].min()))))
        max_x = min(width - 1, int(math.ceil(float(tri[:, 0].max()))))
        min_y = max(0, int(math.floor(float(tri[:, 1].min()))))
        max_y = min(height - 1, int(math.ceil(float(tri[:, 1].max()))))
        if min_x > max_x or min_y > max_y:
            continue

        x0, y0 = tri[0]
        x1, y1 = tri[1]
        x2, y2 = tri[2]
        denominator = (y1 - y2) * (x0 - x2) + (x2 - x1) * (y0 - y2)
        if abs(denominator) < 1e-10:
            continue

        xs = np.arange(min_x, max_x + 1, dtype=np.float64)[None, :] + 0.5
        ys = np.arange(min_y, max_y + 1, dtype=np.float64)[:, None] + 0.5
        w0 = ((y1 - y2) * (xs - x2) + (x2 - x1) * (ys - y2)) / denominator
        w1 = ((y2 - y0) * (xs - x2) + (x0 - x2) * (ys - y2)) / denominator
        w2 = 1.0 - w0 - w1
        inside = (w0 >= -1e-8) & (w1 >= -1e-8) & (w2 >= -1e-8)
        triangle_depth = w0 * z[0] + w1 * z[1] + w2 * z[2]
        depth_slice = depth_buffer[min_y : max_y + 1, min_x : max_x + 1]
        visible = inside & (triangle_depth > depth_slice)
        if not np.any(visible):
            continue
        depth_slice[visible] = triangle_depth[visible]
        color = (
            w0[..., None] * vertex_colors[indices[0]]
            + w1[..., None] * vertex_colors[indices[1]]
            + w2[..., None] * vertex_colors[indices[2]]
        )
        rgb_slice = rgb[min_y : max_y + 1, min_x : max_x + 1]
        rgb_slice[visible] = np.clip(np.rint(color[visible]), 0, 255).astype(np.uint8)

    # Draw sampled B-rep curves only where their depth is on the visible surface.
    tolerance = 0.02
    segments = np.concatenate(
        [np.stack((edge[:-1], edge[1:]), axis=1).reshape(-1, 2, 3) for edge in mesh.edges if len(edge) > 1]
    )
    edge_screen, edge_depth = project_points(segments.reshape(-1, 3), camera)
    edge_screen = edge_screen.reshape(-1, 2, 2)
    edge_depth = edge_depth.reshape(-1, 2)
    for batch_start in range(0, len(segments), 256):
        batch_screen = edge_screen[batch_start : batch_start + 256]
        batch_depth = edge_depth[batch_start : batch_start + 256]
        delta = batch_screen[:, 1] - batch_screen[:, 0]
        steps = np.maximum(1, np.ceil(np.max(np.abs(delta), axis=1)).astype(np.int32))
        columns = np.arange(int(steps.max()) + 1, dtype=np.int32)[None, :]
        t = columns / steps[:, None]
        active = columns <= steps[:, None]
        px = np.rint(batch_screen[:, 0, 0, None] + t * delta[:, 0, None]).astype(np.int32)
        py = np.rint(batch_screen[:, 0, 1, None] + t * delta[:, 1, None]).astype(np.int32)
        pz = batch_depth[:, 0, None] + t * (batch_depth[:, 1] - batch_depth[:, 0])[:, None]
        visible = active & (px >= 0) & (px < width) & (py >= 0) & (py < height)
        rows, cols = np.nonzero(visible)
        if len(rows) == 0:
            continue
        sample_x, sample_y, sample_z = px[rows, cols], py[rows, cols], pz[rows, cols]
        in_front = sample_z >= depth_buffer[sample_y, sample_x] - tolerance
        rgb[sample_y[in_front], sample_x[in_front]] = EDGE_COLOR

    return pane_with_label(Image.fromarray(rgb, "RGB"), label)


def render(mesh, output):
    panes = [render_pane(mesh, *view) for view in VIEWS]
    return save_sheet(panes, output)

