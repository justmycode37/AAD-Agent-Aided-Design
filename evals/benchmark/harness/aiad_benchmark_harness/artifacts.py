from __future__ import annotations

import hashlib
import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from .security import validate_external_output_path, validate_relative_artifact_path


@dataclass(frozen=True)
class StagedArtifact:
    model_claim: str
    host_path: str
    size_bytes: int
    sha256: str


class UnsafeArtifactPath(ValueError):
    pass


def _open_source(root: Path, parts: list[str]) -> int:
    """Open relative to directory FDs; a symlink swap cannot redirect the copy."""
    if not hasattr(os, "O_NOFOLLOW") or not hasattr(os, "O_DIRECTORY"):
        raise UnsafeArtifactPath("safe_descriptor_relative_copy_unavailable")
    descriptors = []
    try:
        directory = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        descriptors.append(directory)
        for part in parts[:-1]:
            directory = os.open(
                part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory,
            )
            descriptors.append(directory)
        return os.open(
            parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory,
        )
    except (OSError, NotImplementedError) as error:
        raise UnsafeArtifactPath("artifact_path_missing_or_contains_symlink") from error
    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)


def stage_claimed_artifacts(
    claims: Iterable[str],
    *,
    solver_artifact_root: Path,
    host_artifact_dir: Path,
    solver_write_roots: tuple[Path, ...],
    max_artifact_bytes: int = 256 * 1024 * 1024,
    max_total_bytes: int = 512 * 1024 * 1024,
) -> list[StagedArtifact]:
    """Copy only existing relative solver outputs into a host-owned run folder.

    Stop all solver descendants before staging. Descriptor-relative opening
    additionally rejects link swaps, hard links and nonregular files. A receipt
    remains a claim; copying does not validate geometry or correctness.
    """
    if any(type(limit) is not int or limit < 1 for limit in (max_artifact_bytes, max_total_bytes)):
        raise ValueError("artifact byte limits must be positive integers")
    source_root = solver_artifact_root.resolve(strict=True)
    if not source_root.is_dir():
        raise UnsafeArtifactPath("solver_artifact_root_not_directory")
    if not any(source_root.is_relative_to(root.resolve(strict=True)) for root in solver_write_roots):
        raise UnsafeArtifactPath("artifact_root_outside_solver_write_roots")
    lexical_destination = Path(host_artifact_dir).expanduser()
    if not lexical_destination.is_absolute():
        lexical_destination = Path.cwd() / lexical_destination
    cursor = Path(lexical_destination.anchor)
    for part in lexical_destination.parts[1:]:
        cursor = cursor / part
        if cursor.is_symlink():
            raise UnsafeArtifactPath("host_artifact_dir_contains_symlink")
    destination = validate_external_output_path(lexical_destination, solver_write_roots)
    destination.mkdir(parents=True, exist_ok=False)
    if destination.is_symlink():
        raise UnsafeArtifactPath("host_artifact_dir_is_symlink")

    staged: list[StagedArtifact] = []
    total_size = 0
    for index, claim in enumerate(claims):
        valid, reason = validate_relative_artifact_path(claim)
        if not valid:
            raise UnsafeArtifactPath(reason)
        parts = claim.replace("\\", "/").split("/")
        source_fd = _open_source(source_root, parts)
        basename = Path(parts[-1]).name
        clean_basename = re.sub(r"[^A-Za-z0-9._-]", "_", basename) or "artifact"
        target = destination / f"{index:03d}-{clean_basename}"
        flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0)
        fd = None
        digest = hashlib.sha256()
        size = 0
        try:
            with os.fdopen(source_fd, "rb") as source_stream:
                before = os.fstat(source_stream.fileno())
                if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
                    raise UnsafeArtifactPath("artifact_must_be_regular_file_without_hardlinks")
                if before.st_size > max_artifact_bytes or total_size + before.st_size > max_total_bytes:
                    raise UnsafeArtifactPath("artifact_byte_limit_exceeded")
                fd = os.open(target, flags, 0o600)
                while chunk := source_stream.read(min(1024 * 1024, max_artifact_bytes - size + 1)):
                    digest.update(chunk)
                    size += len(chunk)
                    if size > max_artifact_bytes or total_size + size > max_total_bytes:
                        raise UnsafeArtifactPath("artifact_byte_limit_exceeded")
                    view = memoryview(chunk)
                    while view:
                        written = os.write(fd, view)
                        view = view[written:]
                after = os.fstat(source_stream.fileno())
                if (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (after.st_size, after.st_mtime_ns, after.st_ctime_ns) or size != after.st_size:
                    raise UnsafeArtifactPath("artifact_changed_during_snapshot")
                os.fsync(fd)
        except BaseException:
            if fd is not None:
                target.unlink(missing_ok=True)
            raise
        finally:
            if fd is not None:
                os.close(fd)
        total_size += size
        staged.append(
            StagedArtifact(
                model_claim=claim,
                host_path=str(target.resolve(strict=True)),
                size_bytes=size,
                sha256=digest.hexdigest(),
            )
        )
    return staged
