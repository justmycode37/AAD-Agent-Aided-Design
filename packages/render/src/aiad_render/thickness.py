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


def scene_triangles(scene: RenderScene, *, include_caps: bool = True) -> Triangles:
    corners: list[np.ndarray] = []
    owners: list[np.ndarray] = []
    for face in scene.faces:
        if not face.triangles or (face.section_cap and not include_caps):
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
    )


def first_hits(tris: Triangles, origins: np.ndarray, directions: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Nearest forward hit per ray: distances (inf when none) and triangle indices (-1 when none)."""
    count = len(origins)
    distances = np.full(count, np.inf)
    triangles = np.full(count, -1, dtype=np.int64)
    if count == 0 or len(tris.v0) == 0:
        return distances, triangles
    step = max(1, RAYS_PER_CHUNK_LIMIT // len(tris.v0))
    for start in range(0, count, step):
        stop = min(start + step, count)
        o = origins[start:stop, None, :]
        d = directions[start:stop, None, :]
        pvec = np.cross(d, tris.edge2[None, :, :])
        det = np.sum(pvec * tris.edge1[None, :, :], axis=2)
        with np.errstate(divide="ignore", invalid="ignore"):
            inverse = 1.0 / det
            tvec = o - tris.v0[None, :, :]
            u = np.sum(tvec * pvec, axis=2) * inverse
            qvec = np.cross(tvec, tris.edge1[None, :, :])
            v = np.sum(d * qvec, axis=2) * inverse
            t = np.sum(qvec * tris.edge2[None, :, :], axis=2) * inverse
            hit = (np.abs(det) > 1.0e-14) & (u >= 0.0) & (v >= 0.0) & (u + v <= 1.0) & (t > 0.0)
        t = np.where(hit, t, np.inf)
        best = np.argmin(t, axis=1)
        best_t = t[np.arange(stop - start), best]
        found = np.isfinite(best_t)
        distances[start:stop] = best_t
        triangles[start:stop] = np.where(found, best, -1)
    return distances, triangles


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
