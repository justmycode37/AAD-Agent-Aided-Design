"""Deterministic ``svg-1`` scene packages for native Sheet2D contexts."""

from __future__ import annotations

import gzip
import hashlib
import json
import os
from pathlib import Path
import tempfile
import uuid
from typing import Any

from .io2d import (
    _Entity,
    _Sheet,
    _TRANSFORM_KEYS,
    _check_job_confinement,
    _field,
    _geometry_bounds,
    _json_value,
    _read_sheet,
    _svg_string,
    _viewport,
)


def _project_name(ctx: Any) -> str:
    project = _field(ctx, "project")
    if isinstance(project, str) and project.strip():
        return project.strip()
    project_dir = _field(ctx, "project_dir")
    if project_dir is not None:
        name = Path(project_dir).name
        if name:
            return name
    raise ValueError("2D scene context must expose ctx.project or ctx.project_dir.name.")


def _revision(ctx: Any) -> int:
    rev = _field(ctx, "rev")
    if type(rev) is not int or rev < 0:
        raise ValueError("2D scene context rev must be a nonnegative integer.")
    return rev


def _entity_record(entity: _Entity) -> dict[str, Any]:
    """Return the source entity as finite JSON with its point names and provenance."""
    metadata = _json_value(entity.metadata or {}, f"Entity {entity.name!r} metadata")
    result: dict[str, Any] = {
        "name": entity.name,
        "kind": entity.kind,
        "layer": entity.layer,
        "point_names": list(entity.point_names),
        "points": [[x, y] for x, y in entity.points],
        "closed": entity.closed,
    }
    for key, value in (
        ("radius", entity.radius),
        ("start_angle", entity.start_angle),
        ("end_angle", entity.end_angle),
        ("text", entity.text),
        ("height", entity.height),
    ):
        if value is not None:
            result[key] = value
    scene_metadata = {
        key: value for key, value in metadata.items()
        if key != "source_element_attributes" and key not in _TRANSFORM_KEYS
    }
    if scene_metadata:
        result["metadata"] = scene_metadata
    provenance: dict[str, Any] = {
        "source_name": metadata.get("source_name", entity.name),
    }
    for key in ("source_id", "source_tag", "source_layer", "source_group_ids", "feature"):
        value = metadata.get(key)
        if value is not None:
            provenance[key] = value
    result["provenance"] = provenance
    return result


def _payload(sheet: _Sheet) -> tuple[dict[str, Any], list[dict[str, str]]]:
    _, _, width, height = _viewport(sheet)
    payload = {
        "svg": _svg_string(sheet),
        "sheet": {"w": width, "h": height, "units": sheet.units},
        "layers": list(sheet.layers),
        "entities": [_entity_record(entity) for entity in sheet.entities],
    }
    parts: list[dict[str, str]] = []
    for entity in sheet.entities:
        part: dict[str, str] = {"path": entity.name, "name": entity.name}
        feature = (entity.metadata or {}).get("feature")
        if isinstance(feature, str) and feature.strip():
            part["feature"] = feature
        parts.append(part)
    return payload, parts


def _write_pair(directory: Path, files: dict[str, bytes]) -> None:
    """Publish two prepared files together, restoring prior files on failure."""
    existed = directory.exists()
    if existed and not directory.is_dir():
        raise ValueError(f"Scene output must be a directory, got file {directory}.")
    directory.mkdir(parents=True, exist_ok=True)
    staged: dict[str, Path] = {}
    backups: dict[str, Path] = {}
    installed: list[str] = []
    try:
        for name, data in files.items():
            fd, temp_name = tempfile.mkstemp(prefix=f".{name}.", suffix=".tmp", dir=directory)
            temp = Path(temp_name)
            staged[name] = temp
            with os.fdopen(fd, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())

        for name in files:
            final = directory / name
            if final.exists() and final.is_dir():
                raise ValueError(f"Scene artifact path must be a file, got directory {final}.")
            if final.exists():
                backup = directory / f".{name}.{uuid.uuid4().hex}.bak"
                os.replace(final, backup)
                backups[name] = backup

        for name, temp in staged.items():
            os.replace(temp, directory / name)
            installed.append(name)
        staged.clear()
        # Both new files are now published. Backup cleanup is housekeeping;
        # a cleanup failure must not roll back a complete new scene package.
        for backup in backups.values():
            try:
                backup.unlink(missing_ok=True)
            except OSError:
                pass
        backups.clear()
    except BaseException as exc:
        rollback_errors: list[BaseException] = []
        for name in reversed(installed):
            try:
                (directory / name).unlink(missing_ok=True)
            except BaseException as rollback_exc:
                rollback_errors.append(rollback_exc)
        for name, backup in backups.items():
            try:
                os.replace(backup, directory / name)
            except BaseException as rollback_exc:
                rollback_errors.append(rollback_exc)
        for temp in staged.values():
            try:
                temp.unlink(missing_ok=True)
            except BaseException as rollback_exc:
                rollback_errors.append(rollback_exc)
        if not existed:
            try:
                directory.rmdir()
            except OSError:
                pass
        if rollback_errors:
            raise RuntimeError(
                f"Scene artifact write failed and rollback was incomplete: {rollback_errors[0]}"
            ) from exc
        raise


def make_scene(ctx: Any, out: Path) -> dict[str, Any]:
    """Write and return a deterministic ``svg-1`` SceneManifest.

    The manifest is always draft. Verification claims and caller metrics are
    not evidence; its bounds come from analytic Sheet2D geometry only.
    """
    if not isinstance(out, (str, os.PathLike)):
        raise TypeError("out must be a scene output directory path.")
    sheet = _read_sheet(ctx)
    directory = _check_job_confinement(ctx, Path(out))
    project = _project_name(ctx)
    rev = _revision(ctx)

    payload, parts = _payload(sheet)
    payload_bytes = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    ).encode("utf-8")
    compressed = gzip.compress(payload_bytes, compresslevel=9, mtime=0)
    geometry = _geometry_bounds(sheet)
    bbox = (
        [geometry.xmin, geometry.ymin, 0.0, geometry.xmax, geometry.ymax, 0.0]
        if geometry is not None
        else [0.0, 0.0, 0.0, 0.0, 0.0, 0.0]
    )
    manifest: dict[str, Any] = {
        "scene_id": hashlib.sha256(compressed).hexdigest(),
        "project": project,
        "rev": rev,
        "kind": "2d",
        "format": "svg-1",
        "encoding": "gzip",
        "units": sheet.units,
        "bbox": bbox,
        "total_bytes": len(compressed),
        "parts": parts,
        "status": "draft",
    }
    manifest_bytes = json.dumps(
        manifest, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    ).encode("utf-8")

    # Resolve after generating the bytes as well as before creating the output
    # directory, so a parent symlink cannot escape a worker job directory.
    directory = _check_job_confinement(ctx, directory)
    _write_pair(directory, {"scene.bin.gz": compressed, "scene.json": manifest_bytes})
    return manifest


__all__ = ["make_scene"]
