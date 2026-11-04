"""ModernGL standalone-context renderer with Pillow sheet assembly."""

from __future__ import annotations

import numpy as np
from PIL import Image

from common import BACKGROUND, EDGE_COLOR, FACE_COLOR, LIGHT, PANE_SIZE, VIEWS, camera_for, pane_with_label, save_sheet


VERTEX_SHADER = """#version 330
in vec3 in_pos;
in vec3 in_normal;
uniform mat4 u_mvp;
uniform float u_line_bias;
out vec3 v_normal;
void main() {
    gl_Position = u_mvp * vec4(in_pos, 1.0);
    gl_Position.z -= u_line_bias * gl_Position.w;
    v_normal = in_normal;
}
"""

FRAGMENT_SHADER = """#version 330
in vec3 v_normal;
uniform vec3 u_color;
uniform vec3 u_light;
out vec4 f_color;
void main() {
    float diffuse = max(dot(normalize(v_normal), normalize(u_light)), 0.0);
    f_color = vec4(u_color * (0.34 + 0.66 * diffuse), 1.0);
}
"""


def _mvp(mesh, camera):
    depth_extent = np.max(np.abs((mesh.vertices.astype(np.float64) - camera.target) @ camera.forward))
    depth_scale = max(float(depth_extent) * 1.15, 1.0)
    matrix = np.eye(4, dtype=np.float32)
    for row, axis, scale in (
        (0, camera.right, camera.scale),
        (1, camera.up, camera.scale),
        (2, camera.forward, depth_scale),
    ):
        matrix[row, :3] = axis / scale
        matrix[row, 3] = -np.dot(axis, camera.target) / scale
    return matrix


def render(mesh, output):
    import moderngl

    context = moderngl.create_standalone_context(require=330)
    context.enable(moderngl.DEPTH_TEST)
    context.depth_func = "<"
    context.disable(moderngl.CULL_FACE)
    color_texture = context.texture((PANE_SIZE, PANE_SIZE), 4, dtype="f1")
    color_texture.filter = (moderngl.NEAREST, moderngl.NEAREST)
    depth_buffer = context.depth_renderbuffer((PANE_SIZE, PANE_SIZE))
    framebuffer = context.framebuffer(color_attachments=[color_texture], depth_attachment=depth_buffer)
    framebuffer.use()
    context.viewport = (0, 0, PANE_SIZE, PANE_SIZE)

    program = context.program(vertex_shader=VERTEX_SHADER, fragment_shader=FRAGMENT_SHADER)
    program["u_color"].value = tuple((FACE_COLOR / 255.0).tolist())
    program["u_light"].value = tuple(LIGHT.astype(np.float32).tolist())

    packed_vertices = np.column_stack((mesh.vertices, mesh.normals)).astype(np.float32)
    vertex_buffer = context.buffer(packed_vertices.tobytes())
    index_buffer = context.buffer(mesh.triangles.astype(np.uint32).ravel().tobytes())
    face_array = context.vertex_array(
        program,
        [(vertex_buffer, "3f 3f", "in_pos", "in_normal")],
        index_buffer,
        index_element_size=4,
    )

    line_points = []
    for polyline in mesh.edges:
        if len(polyline) > 1:
            line_points.append(np.stack((polyline[:-1], polyline[1:]), axis=1).reshape(-1, 3))
    if line_points:
        positions = np.concatenate(line_points).astype(np.float32)
        line_normals = np.broadcast_to(LIGHT.astype(np.float32), positions.shape)
        line_buffer = context.buffer(np.column_stack((positions, line_normals)).astype(np.float32).tobytes())
        line_array = context.vertex_array(program, [(line_buffer, "3f 3f", "in_pos", "in_normal")])
    else:
        line_buffer = None
        line_array = None

    panes = []
    for label, direction, view_up in VIEWS:
        camera = camera_for(mesh, direction, view_up)
        program["u_mvp"].write(_mvp(mesh, camera).T.tobytes())
        framebuffer.clear(*(component / 255.0 for component in BACKGROUND), depth=1.0)
        program["u_color"].value = tuple((FACE_COLOR / 255.0).tolist())
        program["u_line_bias"].value = 0.0
        face_array.render(mode=moderngl.TRIANGLES)
        if line_array is not None:
            program["u_color"].value = tuple((np.asarray(EDGE_COLOR, dtype=np.float32) / 255.0).tolist())
            program["u_line_bias"].value = 0.0004
            line_array.render(mode=moderngl.LINES)
        raw = framebuffer.read(components=3, alignment=1)
        pixels = np.frombuffer(raw, dtype=np.uint8).reshape(PANE_SIZE, PANE_SIZE, 3)[::-1]
        panes.append(pane_with_label(Image.fromarray(pixels.copy(), "RGB"), label))

    result = save_sheet(panes, output)
    face_array.release()
    if line_array is not None:
        line_array.release()
        line_buffer.release()
    index_buffer.release()
    vertex_buffer.release()
    framebuffer.release()
    depth_buffer.release()
    color_texture.release()
    program.release()
    context.release()
    return result

