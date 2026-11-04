"""One method per MCP tool, each returning {structured, text, images?}."""

from __future__ import annotations

import asyncio
import json
import logging
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..rpc import RpcError
from . import backend, scenes
from .envelope import compact, failure_envelope, sort_checks, text_for
from .projects import Project, ProjectError, create_project, open_project

log = logging.getLogger("aiadd.handlers")
DEFAULT_WAIT_S = 30.0
BUILD_TIMEOUT_S = 60.0
NOT_BUILT_YET = "aiad_build has not produced a revision yet."


def reply(envelope: dict[str, Any], images: list[dict[str, str]] | None = None) -> dict[str, Any]:
    result: dict[str, Any] = {"structured": envelope, "text": text_for(envelope)}
    if images:
        result["images"] = images
    return result


@dataclass
class Job:
    id: str
    project: str
    task: asyncio.Task[dict[str, Any]] = field(repr=False)


class Handlers:
    def __init__(self) -> None:
        self._locks: dict[Path, asyncio.Lock] = defaultdict(asyncio.Lock)
        self._jobs: dict[str, Job] = {}
        self._active = 0

    @property
    def busy(self) -> bool:
        return self._active > 0

    async def close(self) -> None:
        for job in self._jobs.values():
            job.task.cancel()

    async def call(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        handler = getattr(self, method, None) if method.startswith("aiad_") else None
        if handler is None:
            return self._not_implemented(method, params)
        try:
            return await handler(params)
        except ProjectError as exc:
            return reply(failure_envelope(params.get("project") or params.get("name") or "", exc.code, "input", exc.message))

    def _not_implemented(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        if not method.startswith("aiad_"):
            raise KeyError(method)
        envelope = failure_envelope(
            params.get("project") or "", "BAD_INPUT", "input", f"{method} is not implemented in this build."
        )
        return reply(envelope)

    async def aiad_new(self, params: dict[str, Any]) -> dict[str, Any]:
        client = params.get("client")
        project = create_project(
            params.get("name", ""),
            client=client,
            kind=params.get("kind", "part"),
            units=params.get("units", "mm"),
            process=params.get("process"),
            material=params.get("material"),
            spec=params.get("spec"),
            source=params.get("source"),
        )
        envelope = {
            "ok": True,
            "project": project.name,
            "status": "draft",
            "summary": f"Created project {project.name} at {project.path}.",
            "next": ["Write model.py and call aiad_build."],
        }
        return reply(envelope)

    async def aiad_build(self, params: dict[str, Any]) -> dict[str, Any]:
        project = open_project(params.get("project", ""), params.get("client"))
        job_id, job_dir = project.new_job_dir()
        task = asyncio.create_task(self._build_job(project, job_id, job_dir, params))
        self._jobs[job_id] = Job(job_id, project.name, task)
        wait_s = params.get("wait_s")
        try:
            return await asyncio.wait_for(asyncio.shield(task), DEFAULT_WAIT_S if wait_s is None else wait_s)
        except TimeoutError:
            return reply(
                {
                    "ok": True,
                    "project": project.name,
                    "status": "running",
                    "job": job_id,
                    "summary": f"Build {job_id} is still running.",
                    "next": [f"Poll with aiad_history action='job' job='{job_id}'."],
                }
            )

    async def _build_job(
        self, project: Project, job_id: str, job_dir: Path, params: dict[str, Any]
    ) -> dict[str, Any]:
        self._active += 1
        try:
            async with self._locks[project.path]:
                return await self._build_locked(project, job_id, job_dir, params)
        except Exception as exc:
            log.exception("build %s failed", job_id)
            project.discard_job(job_dir)
            return reply(failure_envelope(project.name, "WORKER_CRASH", "crash", f"The build job crashed: {exc}"))
        finally:
            self._active -= 1

    async def _build_locked(
        self, project: Project, job_id: str, job_dir: Path, params: dict[str, Any]
    ) -> dict[str, Any]:
        meta = project.meta()
        head = meta.get("head_rev")
        base_rev = params.get("base_rev")
        if base_rev is not None and base_rev != head:
            project.discard_job(job_dir)
            return reply(
                failure_envelope(
                    project.name,
                    "BASE_REV_CONFLICT",
                    "input",
                    f"base_rev {base_rev} is not the head revision {head}. Rebase your edit on the head.",
                    head_rev=head,
                    base_rev=base_rev,
                )
            )
        if params.get("patch"):
            project.discard_job(job_dir)
            return reply(failure_envelope(project.name, "BAD_INPUT", "input", "patch is not implemented in this build. Send full source.", head_rev=head))
        source = params.get("source")
        if source is None:
            model = project.path / "model.py"
            if not model.exists():
                project.discard_job(job_dir)
                return reply(failure_envelope(project.name, "BAD_INPUT", "input", "No model.py yet. Send source.", head_rev=head))
            source = model.read_text()
        spec_text = params.get("spec")
        if spec_text is None and (project.path / "spec.yaml").exists():
            spec_text = (project.path / "spec.yaml").read_text()
        try:
            spec = _parse_spec(spec_text)
        except ValueError as exc:
            project.discard_job(job_dir)
            return reply(failure_envelope(project.name, "BAD_INPUT", "input", f"spec is not valid YAML: {exc}", head_rev=head))
        if not backend.engine_available():
            project.discard_job(job_dir)
            return reply(
                failure_envelope(
                    project.name,
                    "WORKER_CRASH",
                    "crash",
                    "The AIAD engine is not installed in this environment. Run aiad doctor.",
                    head_rev=head,
                )
            )
        rev = project.next_rev()
        result = await asyncio.to_thread(
            backend.run_build,
            source=source,
            project_dir=project.path,
            job_dir=job_dir,
            rev=rev,
            kind=meta.get("kind", "part"),
            units=meta.get("units", "mm"),
            params=params.get("params") or {},
            spec=spec,
            material=meta.get("material"),
            process=meta.get("process"),
            preview=bool(params.get("preview")),
            timeout_s=BUILD_TIMEOUT_S,
        )
        if not result.get("ok"):
            envelope = failure_envelope(
                project.name,
                *(_failure_head(result)),
                head_rev=head,
                base_rev=base_rev,
                **_failure_extra(result),
            )
            envelope["checks"] = sort_checks(compact(result.get("checks") or []))
            envelope["checks_summary"] = result.get("checks_summary")
            project.discard_job(job_dir)
            return reply(compact(envelope))
        if result.get("scene") is None:
            brep = job_dir / "part.brep"
            if brep.exists():
                try:
                    result["scene"] = await asyncio.to_thread(
                        backend.stub_scene, brep, job_dir / "scene", project.name, rev, meta.get("units", "mm")
                    )
                except Exception as exc:
                    log.warning("stub scene failed: %s", exc)
                    _add_warn(result, "scene_generation_failed", f"Scene generation failed: {exc}")
        status = result.get("status", "draft")
        envelope: dict[str, Any] = {
            "ok": True,
            "project": project.name,
            "base_rev": head,
            "status": status,
            "summary": result.get("summary") or f"Built {project.name}.",
            "metrics": result.get("metrics"),
            "checks": sort_checks(compact(result.get("checks") or [])),
            "checks_summary": result.get("checks_summary"),
        }
        if params.get("dry_run"):
            project.discard_job(job_dir)
            envelope["head_rev"] = head
            envelope["summary"] = f"Dry run of {project.name}: {envelope['summary']}"
            return reply(compact(envelope))
        if result.get("scene"):
            result["scene"]["status"] = "verified" if status == "verified" else "draft"
        (job_dir / "meta.json").write_text(
            json.dumps(
                compact(
                    {
                        "rev": rev,
                        "base_rev": head,
                        "job": job_id,
                        "status": status,
                        "metrics": result.get("metrics"),
                        "checks": result.get("checks"),
                        "checks_summary": result.get("checks_summary"),
                        "scene": result.get("scene"),
                    }
                ),
                indent=2,
                default=str,
            )
        )
        rev = project.commit_revision(job_dir, source, params.get("spec"))
        envelope.update(
            rev=rev,
            head_rev=rev,
            summary=f"Built {project.name} rev {rev} ({status}). {envelope['summary']}",
            artifacts={
                name: f"aiad://{project.name}/{rev}/{name}"
                for name in ("model.py", "part.brep")
                if (project.rev_dir(rev) / name).exists()
            },
            next=_next_hints(status),
        )
        return reply(compact(envelope))

    async def aiad_show(self, params: dict[str, Any]) -> dict[str, Any]:
        project = open_project(params.get("project", ""), params.get("client"))
        head = project.head_rev
        rev = params.get("rev") or head
        if rev is None:
            return reply(failure_envelope(project.name, "BAD_INPUT", "input", NOT_BUILT_YET))
        rev_dir = project.rev_dir(rev)
        if not rev_dir.is_dir():
            return reply(failure_envelope(project.name, "BAD_INPUT", "input", f"No revision {rev}.", head_rev=head))
        meta = json.loads((rev_dir / "meta.json").read_text())
        scene = meta.get("scene")
        payload = _scene_payload(rev_dir)
        if not scene or payload is None:
            return reply(
                failure_envelope(project.name, "BAD_INPUT", "input", f"Revision {rev} has no scene to show.", head_rev=head)
            )
        scenes.publish(scene["scene_id"], payload)
        scene["project"] = project.name
        scene["rev"] = rev
        status = meta.get("status", "draft")
        summary = params.get("title") or f"{project.name} rev {rev}"
        envelope = {
            "ok": True,
            "project": project.name,
            "rev": rev,
            "head_rev": head,
            "status": status,
            "summary": f"Showing {summary} ({status}) in the viewer.",
            "metrics": meta.get("metrics"),
            "checks_summary": meta.get("checks_summary"),
            "scene": scene,
            "viewer": {"mode": "inline"},
        }
        return reply(compact(envelope))

    async def aiad_scene_read(self, params: dict[str, Any]) -> dict[str, Any]:
        try:
            chunk = await asyncio.to_thread(
                scenes.read_chunk, params.get("scene_id", ""), params.get("offset", 0), params.get("length")
            )
        except (ValueError, FileNotFoundError) as exc:
            raise RpcError(-32602, str(exc)) from exc
        return {
            "structured": chunk,
            "text": f"Scene chunk at {chunk['offset']}: {chunk['byte_count']} of {chunk['total_bytes']} bytes.",
        }

    async def aiad_history(self, params: dict[str, Any]) -> dict[str, Any]:
        action = params.get("action", "list")
        if action == "job":
            job = self._jobs.get(params.get("job", ""))
            if job is None:
                return reply(failure_envelope(params.get("project", ""), "BAD_INPUT", "input", f"Unknown job {params.get('job')!r}."))
            if job.task.done():
                return job.task.result()
            return reply(
                {
                    "ok": True,
                    "project": job.project,
                    "status": "running",
                    "job": job.id,
                    "summary": f"Build {job.id} is still running.",
                }
            )
        project = open_project(params.get("project", ""), params.get("client"))
        if action != "list":
            return self._not_implemented("aiad_history", params)
        revs = sorted(int(p.name) for p in project.revs_dir.iterdir() if p.name.isdigit())
        return reply(
            {
                "ok": True,
                "project": project.name,
                "status": "draft",
                "head_rev": project.head_rev,
                "summary": f"{project.name} has {len(revs)} revision(s): {revs}. Head is {project.head_rev}.",
            }
        )


def _parse_spec(text: str | None) -> dict[str, Any]:
    if not text:
        return {}
    import yaml

    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ValueError(str(exc)) from exc
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ValueError("the document must be a mapping")
    return data


def _failure_head(result: dict[str, Any]) -> tuple[str, str, str]:
    failure = result.get("failure") or {}
    return (
        failure.get("code", "RUNTIME_ERROR"),
        failure.get("phase", "runtime"),
        failure.get("message") or result.get("summary") or "The build failed.",
    )


def _failure_extra(result: dict[str, Any]) -> dict[str, Any]:
    failure = result.get("failure") or {}
    keys = ("kernel_message", "feature", "file", "line", "selector", "candidates", "probes", "repair")
    return {key: failure.get(key) for key in keys if failure.get(key) not in (None, [], "")}


def _add_warn(result: dict[str, Any], check_id: str, message: str) -> None:
    result.setdefault("checks", []).append(
        {"id": check_id, "suite": "hard", "level": "warn", "status": "warn", "message": message}
    )
    summary = result.setdefault("checks_summary", {})
    summary["warn"] = summary.get("warn", 0) + 1


def _scene_payload(rev_dir: Path) -> Path | None:
    for candidate in (rev_dir / "scene.bin.gz", rev_dir / "scene" / "scene.bin.gz"):
        if candidate.exists():
            return candidate
    return None


def _next_hints(status: str) -> list[str]:
    if status == "verified":
        return ["Review with aiad_render, then aiad_show for the user."]
    return ["Add requirements to spec.yaml to prove the part. draft means not proven.", "Review with aiad_render."]
