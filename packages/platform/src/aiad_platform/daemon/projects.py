"""Projects on disk (CONTRACTS section 2). Sandboxed code never reaches this module."""

from __future__ import annotations

import json
import os
import re
import shutil
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .. import __version__, paths

NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}\Z")
JOB_ID_RE = re.compile(r"[0-9a-f]{12}")
RETAINED_MARKER = "failed.json"
MAX_RETAINED_FAILED_JOBS = 5
GITIGNORE = ".aiad/\nexports/\n"


class ProjectError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def write_atomic(path: Path, text: str) -> None:
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(text)
    os.replace(tmp, path)


def _registry_path() -> Path:
    return paths.aiad_home() / "projects.json"


def _registry() -> dict[str, str]:
    try:
        return json.loads(_registry_path().read_text())
    except (OSError, ValueError):
        return {}


def _remember(name: str, path: Path) -> None:
    registry = _registry()
    registry[name] = str(path)
    write_atomic(_registry_path(), json.dumps(registry, indent=2, sort_keys=True))


@dataclass(frozen=True)
class Project:
    path: Path

    @property
    def meta_path(self) -> Path:
        return self.path / "project.json"

    @property
    def revs_dir(self) -> Path:
        return self.path / ".aiad" / "revs"

    @property
    def jobs_dir(self) -> Path:
        return self.path / ".aiad" / "jobs"

    def meta(self) -> dict[str, Any]:
        return json.loads(self.meta_path.read_text())

    def write_meta(self, meta: dict[str, Any]) -> None:
        write_atomic(self.meta_path, json.dumps(meta, indent=2) + "\n")

    @property
    def name(self) -> str:
        return self.meta()["name"]

    @property
    def head_rev(self) -> int | None:
        return self.meta().get("head_rev")

    def rev_dir(self, rev: int) -> Path:
        return self.revs_dir / f"{rev:06d}"

    def revisions(self) -> list[int]:
        return sorted(int(p.name) for p in self.revs_dir.iterdir() if p.name.isdigit())

    def next_rev(self) -> int:
        return max(self.revisions(), default=0) + 1

    def checkout(self, rev: int) -> None:
        """Move the head to an existing revision. Later revisions stay in history."""
        source = self.rev_dir(rev)
        # Read every required artifact before changing the working copy or head.
        model = (source / "model.py").read_bytes().decode("utf-8")
        spec_path = source / "spec.yaml"
        spec = spec_path.read_bytes().decode("utf-8") if spec_path.exists() else None
        meta = self.meta()
        meta["head_rev"] = rev
        targets = [self.path / "model.py", self.path / "spec.yaml", self.meta_path]
        originals = {path: path.read_bytes().decode("utf-8") if path.exists() else None for path in targets}
        try:
            write_atomic(targets[0], model)
            if spec is None:
                targets[1].unlink(missing_ok=True)
            else:
                write_atomic(targets[1], spec)
            self.write_meta(meta)
        except OSError:
            for path, original in originals.items():
                if original is None:
                    path.unlink(missing_ok=True)
                else:
                    write_atomic(path, original)
            raise

    def new_job_dir(self) -> tuple[str, Path]:
        job = uuid.uuid4().hex[:12]
        job_dir = self.jobs_dir / job
        job_dir.mkdir(parents=True)
        return job, job_dir

    def commit_revision(self, job_dir: Path, source: str, spec: str | None) -> int:
        """Promote a finished job to the next immutable revision and move the head."""
        rev = self.next_rev()
        target = self.rev_dir(rev)
        (job_dir / "model.py").write_text(source)
        if spec is not None:
            (job_dir / "spec.yaml").write_text(spec)
        os.replace(job_dir, target)
        self.checkout(rev)
        return rev

    def retain_failed_job(self, job_dir: Path, job_id: str, envelope: dict[str, Any]) -> None:
        """Keep a built, failed candidate as evidence, bounded per project. Never touches the head."""
        retained = {**envelope, "job": job_id, "artifacts": {"part.brep": str(job_dir / "part.brep")}}
        (job_dir / RETAINED_MARKER).write_text(json.dumps(retained, default=str))
        kept = sorted(
            (path for path in self.jobs_dir.iterdir() if (path / RETAINED_MARKER).is_file()),
            key=lambda path: (path / RETAINED_MARKER).stat().st_mtime,
            reverse=True,
        )
        for old_job in kept[MAX_RETAINED_FAILED_JOBS:]:
            self.discard_job(old_job)

    def retained_job(self, job_id: str) -> dict[str, Any] | None:
        if not JOB_ID_RE.fullmatch(job_id or ""):
            return None
        try:
            return json.loads((self.jobs_dir / job_id / RETAINED_MARKER).read_text())
        except (OSError, ValueError):
            return None

    def discard_job(self, job_dir: Path) -> None:
        shutil.rmtree(job_dir, ignore_errors=True)


def resolve_root(client: dict[str, Any] | None) -> Path:
    """MCP roots when available, else ~/AIAD. Never an arbitrary working directory."""
    root = (client or {}).get("root")
    return Path(root).resolve() if root else paths.default_projects_root()


def create_project(
    name: str,
    *,
    client: dict[str, Any] | None = None,
    kind: str = "part",
    units: str = "mm",
    process: str | None = None,
    material: str | None = None,
    spec: str | None = None,
    source: str | None = None,
) -> Project:
    if not NAME_RE.match(name or ""):
        raise ProjectError(
            "BAD_INPUT",
            f"Project name {name!r} is invalid. Use letters, digits, '.', '_' or '-', at most 64 characters.",
        )
    if kind not in ("part", "assembly", "2d"):
        raise ProjectError("BAD_INPUT", f"kind must be part, assembly or 2d, not {kind!r}.")
    if units not in ("mm", "in"):
        raise ProjectError("BAD_INPUT", f"units must be mm or in, not {units!r}.")
    path = (resolve_root(client) / name).resolve()
    if (path / "project.json").exists():
        raise ProjectError("BAD_INPUT", f"Project {name!r} already exists at {path}. Use a new name.")
    (path / ".aiad" / "revs").mkdir(parents=True, exist_ok=True)
    (path / ".aiad" / "jobs").mkdir(exist_ok=True)
    (path / ".aiad" / "cache").mkdir(exist_ok=True)
    (path / "exports").mkdir(exist_ok=True)
    project = Project(path)
    project.write_meta(
        {
            "id": uuid.uuid4().hex,
            "name": name,
            "kind": kind,
            "units": units,
            "process": process,
            "material": material,
            "head_rev": None,
            "created": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "aiad_version": __version__,
        }
    )
    if not (path / ".gitignore").exists():
        (path / ".gitignore").write_text(GITIGNORE)
    if spec is not None:
        write_atomic(path / "spec.yaml", spec)
    if source is not None:
        write_atomic(path / "model.py", source)
    _remember(name, path)
    return project


def open_project(name: str, client: dict[str, Any] | None = None) -> Project:
    if not NAME_RE.match(name or ""):
        raise ProjectError("BAD_INPUT", f"Project name {name!r} is invalid.")
    candidates = [resolve_root(client) / name]
    if registered := _registry().get(name):
        candidates.append(Path(registered))
    candidates.append(paths.default_projects_root() / name)
    for path in candidates:
        if (path / "project.json").exists():
            return Project(path.resolve())
    raise ProjectError("BAD_INPUT", f"No project named {name!r}. Create it with aiad_new.")
