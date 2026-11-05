"""Ray casting against the render mesh, in plain NumPy so results repeat exactly between processes.

Wall thickness is a heuristic: rays go inward from sampled points on each face, and a sample only
counts when the ray leaves the solid through a surface roughly opposite the one it started from.
Rays that run into a neighboring face at a corner measure no wall, so they are dropped.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .backends.protocol import RenderScene

SAMPLES_PER_FACE = 16
MIN_OPPOSITION = 0.5  # cos(60 degrees) between the ray and the exit surface's outward normal
FACE_PERCENTILE = 10  # A low percentile, so one thin spot shows without a single stray ray deciding it
RAYS_PER_CHUNK_LIMIT = 4_000_000  # ray-triangle tests per NumPy batch, which bounds memory use
CLUSTER_SIZE = 48  # triangles per bounding box in the ray index
FIELD_SAMPLES = 20_000  # roughly how many rays a thickness field casts, beyond the mesh's own triangles
EXACT_REFINEMENTS = 48  # thinnest mesh readings re-measured on the B-rep


@dataclass(frozen=True, slots=True)
class Triangles:
    v0: np.ndarray
    edge1: np.ndarray
    edge2: np.ndarray
    normals: np.ndarray
    areas: np.ndarray
    centroids: np.ndarray
    face_index: np.ndarray
    diagonal: float
    clusters: tuple[tuple[np.ndarray, np.ndarray, np.ndarray], ...] = ()


def scene_triangles(scene: RenderScene, *, include_caps: bool = True) -> Triangles:
    corners: list[np.ndarray] = []
    owners: list[np.ndarray] = []
    for face in scene.faces:
        if not len(face.triangles) or (face.section_cap and not include_caps):
            continue
        points = np.asarray(face.points, dtype=np.float64)
        indices = np.asarray(face.triangles, dtype=np.int64)
        corners.append(points[indices])
        owners.append(np.full(len(indices), face.index, dtype=np.int64))
    corner_array = np.concatenate(corners) if corners else np.zeros((0, 3, 3))
    face_index = np.concatenate(owners) if owners else np.zeros(0, dtype=np.int64)
    edge1 = corner_array[:, 1] - corner_array[:, 0]
    edge2 = corner_array[:, 2] - corner_array[:, 0]
    cross = np.cross(edge1, edge2)
    doubled_area = np.linalg.norm(cross, axis=1)
    keep = doubled_area > 1.0e-14
    normals = cross[keep] / doubled_area[keep, None]
    minimum, maximum = (np.asarray(bound, dtype=np.float64) for bound in scene.bounds)
    return Triangles(
        v0=corner_array[keep, 0],
        edge1=edge1[keep],
        edge2=edge2[keep],
        normals=normals,
        areas=doubled_area[keep] * 0.5,
        centroids=corner_array[keep].mean(axis=1),
        face_index=face_index[keep],
        diagonal=float(np.linalg.norm(maximum - minimum)),
        clusters=_cluster(corner_array[keep]),
    )


def _cluster(corners: np.ndarray) -> tuple[tuple[np.ndarray, np.ndarray, np.ndarray], ...]:
    """Group nearby triangles, in a Morton order, so a ray only tests the groups it passes through."""
    if len(corners) == 0:
        return ()
    centers = corners.mean(axis=1)
    low = centers.min(axis=0)
    span = np.maximum(centers.max(axis=0) - low, 1.0e-12)
    cells = np.minimum(((centers - low) / span * 1024.0).astype(np.int64), 1023)
    code = np.zeros(len(cells), dtype=np.int64)
    for bit in range(10):
        for axis in range(3):
            code |= ((cells[:, axis] >> bit) & 1) << (3 * bit + axis)
    order = np.argsort(code, kind="stable")
    margin = float(np.linalg.norm(span)) * 1.0e-9 + 1.0e-12
    clusters = []
    for start in range(0, len(order), CLUSTER_SIZE):
        members = order[start : start + CLUSTER_SIZE]
        points = corners[members].reshape(-1, 3)
        clusters.append((members, points.min(axis=0) - margin, points.max(axis=0) + margin))
    return tuple(clusters)


def first_hits(tris: Triangles, origins: np.ndarray, directions: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Nearest forward hit per ray: distances (inf when none) and triangle indices (-1 when none)."""
    count = len(origins)
    distances = np.full(count, np.inf)
    triangles = np.full(count, -1, dtype=np.int64)
    if count == 0 or len(tris.v0) == 0:
        return distances, triangles
    with np.errstate(divide="ignore", invalid="ignore"):
        inverse_direction = 1.0 / directions
    clusters = tris.clusters or ((np.arange(len(tris.v0)), tris.v0.min(axis=0), tris.v0.max(axis=0)),)
    for members, low, high in clusters:
        with np.errstate(invalid="ignore"):
            near = (low - origins) * inverse_direction
            far = (high - origins) * inverse_direction
        enter = np.nanmax(np.minimum(near, far), axis=1)
        leave = np.nanmin(np.maximum(near, far), axis=1)
        rays = np.flatnonzero((leave >= np.maximum(enter, 0.0)) & (enter < distances))
        step = max(1, RAYS_PER_CHUNK_LIMIT // max(len(members), 1))
        for start in range(0, len(rays), step):
            chosen = rays[start : start + step]
            found, rows = _nearest(tris, members, origins[chosen], directions[chosen])
            better = found < distances[chosen]
            distances[chosen[better]] = found[better]
            triangles[chosen[better]] = rows[better]
    return distances, triangles


def _nearest(tris: Triangles, members: np.ndarray, origins: np.ndarray, directions: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    o = origins[:, None, :]
    d = directions[:, None, :]
    edge1 = tris.edge1[members][None, :, :]
    edge2 = tris.edge2[members][None, :, :]
    pvec = np.cross(d, edge2)
    det = np.sum(pvec * edge1, axis=2)
    with np.errstate(divide="ignore", invalid="ignore"):
        inverse = 1.0 / det
        tvec = o - tris.v0[members][None, :, :]
        u = np.sum(tvec * pvec, axis=2) * inverse
        qvec = np.cross(tvec, edge1)
        v = np.sum(d * qvec, axis=2) * inverse
        t = np.sum(qvec * edge2, axis=2) * inverse
        # A small allowance, so a ray aimed exactly along a shared edge cannot slip between two triangles.
        hit = (np.abs(det) > 1.0e-14) & (u >= -1.0e-9) & (v >= -1.0e-9) & (u + v <= 1.0 + 1.0e-9) & (t > 0.0)
    t = np.where(hit, t, np.inf)
    best = np.argmin(t, axis=1)
    return t[np.arange(len(t)), best], members[best]


def subdivide(points: np.ndarray, triangles: np.ndarray, spacing: float) -> tuple[np.ndarray, np.ndarray]:
    """Bisect long edges of large triangles down to about ``spacing``. Shared edges split at a shared midpoint.

    Long thin facets on a curved face stop once their area is small; their width is already fine.
    """
    points = np.asarray(points, dtype=np.float64)
    triangles = np.asarray(triangles, dtype=np.int64)
    for _ in range(32):
        corners = points[triangles]
        lengths = np.linalg.norm(corners[:, [1, 2, 0]] - corners, axis=2)
        longest = np.argmax(lengths, axis=1)
        areas = np.linalg.norm(np.cross(corners[:, 1] - corners[:, 0], corners[:, 2] - corners[:, 0]), axis=1) * 0.5
        split = (lengths[np.arange(len(lengths)), longest] > spacing) & (areas > spacing * spacing * 0.25)
        if not split.any():
            break
        chosen = triangles[split]
        turn = longest[split]
        # Rotate each triangle so that its longest edge runs from the first corner to the second.
        rows = np.arange(len(chosen))
        a = chosen[rows, turn]
        b = chosen[rows, (turn + 1) % 3]
        c = chosen[rows, (turn + 2) % 3]
        keys = np.sort(np.stack([a, b], axis=1), axis=1)
        unique, inverse = np.unique(keys, axis=0, return_inverse=True)
        inverse = inverse.ravel()
        middle = len(points) + inverse
        points = np.concatenate([points, points[unique].mean(axis=1)])
        triangles = np.concatenate([triangles[~split], np.stack([a, middle, c], axis=1), np.stack([middle, b, c], axis=1)])
    return points, triangles


@dataclass(slots=True)
class WallSamples:
    """Thickness along inward rays from points spread evenly over the surface."""

    faces: dict[int, tuple[np.ndarray, np.ndarray, np.ndarray]]  # face: points, triangles, thickness (nan = no reading)
    origins: np.ndarray
    directions: np.ndarray
    values: np.ndarray
    owners: np.ndarray
    exits: np.ndarray
    spacing: float


def wall_samples(scene: RenderScene, target: RenderScene | None = None, *, samples: int = FIELD_SAMPLES) -> WallSamples:
    """Cast rays from ``scene``'s surface against ``target`` (the uncut solid, in a section)."""
    target_tris = scene_triangles(target if target is not None else scene)
    faces: dict[int, tuple[np.ndarray, np.ndarray]] = {}
    area = 0.0
    for face in scene.faces:
        if len(face.triangles) and not face.section_cap:
            corners = np.asarray(face.points, dtype=np.float64)[np.asarray(face.triangles, dtype=np.int64)]
            area += float(np.linalg.norm(np.cross(corners[:, 1] - corners[:, 0], corners[:, 2] - corners[:, 0]), axis=1).sum()) * 0.5
    spacing = max(float(np.sqrt(2.0 * max(area, 1.0e-12) / max(samples, 1))), target_tris.diagonal * 1.0e-4)
    origin_rows, direction_rows, owner_rows, spans = [], [], [], []
    start = 0
    epsilon = max(target_tris.diagonal * 1.0e-5, 1.0e-7)
    for face in scene.faces:
        if not len(face.triangles) or face.section_cap:
            continue
        points, triangles = subdivide(face.points, face.triangles, spacing)
        corners = points[triangles]
        normals = np.cross(corners[:, 1] - corners[:, 0], corners[:, 2] - corners[:, 0])
        lengths = np.linalg.norm(normals, axis=1)
        usable = lengths > 1.0e-14
        directions = np.zeros_like(normals)
        directions[usable] = -normals[usable] / lengths[usable, None]
        directions[~usable] = (0.0, 0.0, 1.0)
        origin_rows.append(corners.mean(axis=1) + directions * epsilon)
        direction_rows.append(directions)
        owner_rows.append(np.full(len(triangles), face.index, dtype=np.int64))
        faces[face.index] = (points, triangles)
        spans.append((face.index, start, start + len(triangles), usable))
        start += len(triangles)
    if not origin_rows:
        empty = np.zeros((0, 3))
        return WallSamples({}, empty, empty, np.zeros(0), np.zeros(0, dtype=np.int64), np.zeros(0, dtype=np.int64), spacing)
    origins = np.concatenate(origin_rows)
    directions = np.concatenate(direction_rows)
    distances, rows = first_hits(target_tris, origins, directions)
    found = rows >= 0
    opposed = np.zeros(len(rows), dtype=bool)
    opposed[found] = np.sum(target_tris.normals[rows[found]] * directions[found], axis=1) >= MIN_OPPOSITION
    values = np.where(opposed, distances + epsilon, np.nan)
    exits = np.full(len(rows), -1, dtype=np.int64)
    exits[opposed] = target_tris.face_index[rows[opposed]]
    result_faces = {}
    for index, begin, end, usable in spans:
        points, triangles = faces[index]
        face_values = values[begin:end].copy()
        face_values[~usable] = np.nan
        result_faces[index] = (points, triangles, face_values)
        values[begin:end] = face_values
    return WallSamples(result_faces, origins, directions, values, np.concatenate(owner_rows), exits, spacing)


def face_thickness(scene: RenderScene) -> dict[int, float]:
    """Thickness per face in model units. Faces with no usable sample are left out."""
    tris = scene_triangles(scene)
    if len(tris.v0) == 0:
        return {}
    epsilon = max(tris.diagonal * 1.0e-5, 1.0e-7)
    sample_rows: list[int] = []
    for face_index in np.unique(tris.face_index):
        rows = np.flatnonzero(tris.face_index == face_index)
        largest_first = rows[np.argsort(-tris.areas[rows], kind="stable")]
        sample_rows.extend(largest_first[:SAMPLES_PER_FACE].tolist())
    samples = np.asarray(sample_rows, dtype=np.int64)
    directions = -tris.normals[samples]
    origins = tris.centroids[samples] + directions * epsilon
    distances, hit_rows = first_hits(tris, origins, directions)

    readings: dict[int, list[float]] = {}
    for row, direction, distance, hit_row in zip(samples, directions, distances, hit_rows, strict=True):
        if hit_row < 0:
            continue
        if float(np.dot(tris.normals[hit_row], direction)) < MIN_OPPOSITION:
            continue
        readings.setdefault(int(tris.face_index[row]), []).append(float(distance) + epsilon)
    return {
        face: float(np.percentile(values, FACE_PERCENTILE, method="lower"))
        for face, values in sorted(readings.items())
    }


def visible_from(tris: Triangles, points: np.ndarray, toward_camera: np.ndarray) -> np.ndarray:
    """True where nothing in the mesh blocks the straight line from a point to an orthographic camera."""
    if len(points) == 0:
        return np.zeros(0, dtype=bool)
    offset = max(tris.diagonal * 5.0e-4, 1.0e-6)
    directions = np.tile(toward_camera, (len(points), 1))
    distances, _ = first_hits(tris, points + directions * offset, directions)
    return ~np.isfinite(distances)
