import asyncio
import hashlib
from importlib import metadata as importlib_metadata
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from aad_platform.daemon import handlers
from aad_platform.daemon.handlers import Handlers, _project_directory, _publish_exports
from aad_platform.daemon.projects import create_project
from aad_platform.rpc import RpcError
from aad_platform.specs import parse_spec


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("AAD_HOME", str(tmp_path / ".aad"))
    (tmp_path / ".aad").mkdir()
    return create_project("p")


def test_rejects_symlinked_project_directories(project, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    for name in ("imports", "exports"):
        directory = project.path / name
        if directory.exists():
            directory.rmdir()
        directory.symlink_to(outside, target_is_directory=True)
        with pytest.raises(ValueError, match="symlink"):
            _project_directory(project, name)
    (outside / "secret").write_text("keep")
    with pytest.raises(RpcError):
        asyncio.run(Handlers().aad_file_read({"project": "p", "path": "secret"}))


def test_verified_export_needs_no_unverified_flag_or_suffix(project, tmp_path, monkeypatch):
    _write_verified_revision(project)
    inspected = asyncio.run(Handlers().aad_inspect({"project": "p", "what": "summary"}))["structured"]
    assert inspected["status"] == "verified"
    assert "body" in inspected["summary"]
    recorded = {}

    async def fake_op(_self, _project, _rev, _op, args, **_kwargs):
        recorded.update(args)
        out = tmp_path / "op-output"
        out.mkdir()
        source = out / "model.step"
        source.write_text("step")
        return {"ok": True, "files": {"step": str(source)}}, out

    monkeypatch.setattr(Handlers, "_op", fake_op)
    response = asyncio.run(Handlers().aad_export({"project": "p", "formats": ["step"]}))
    envelope = response["structured"]
    assert envelope["ok"] and envelope["status"] == "verified"
    assert "allow_unverified" not in recorded
    assert Path(envelope["artifacts"]["step"]).name == "p-r1.step"
    assert "UNVERIFIED" not in envelope["artifacts"]["step"]


def test_committed_build_receipt_is_rechecked_for_inspect_and_export(project, tmp_path, monkeypatch):
    result = asyncio.run(Handlers().aad_build({
        "project": "p",
        "source": "from build123d import Box\nresult = Box(10, 8, 4)\n",
        "spec": "units: mm\nrequirements:\n  - {id: body, solids: 1}\n",
    }))
    envelope = result["structured"]
    assert envelope["ok"] and envelope["status"] == "verified", envelope.get("summary")
    assert "body" in envelope["summary"]
    inspected = asyncio.run(Handlers().aad_inspect({"project": "p", "what": "summary"}))["structured"]
    assert inspected["status"] == "verified"
    assert "body" in inspected["summary"]

    recorded = {}

    async def fake_op(_self, _project, _rev, _op, args, **_kwargs):
        recorded.update(args)
        out = tmp_path / "committed-output"
        out.mkdir()
        source = out / "model.step"
        source.write_text("step")
        return {"ok": True, "files": {"step": str(source)}}, out

    monkeypatch.setattr(Handlers, "_op", fake_op)
    exported = asyncio.run(Handlers().aad_export({"project": "p", "formats": ["step"]}))["structured"]
    assert exported["ok"] and exported["status"] == "verified"
    assert "allow_unverified" not in recorded
    assert "UNVERIFIED" not in exported["artifacts"]["step"]


def test_warn_only_requirement_stays_verified_after_receipt_compaction(project):
    result = asyncio.run(Handlers().aad_build({
        "project": "p",
        "source": "from build123d import Box\nresult = Box(10, 8, 4)\n",
        "spec": "units: mm\nrequirements:\n  - {id: advisory_size, level: warn, bbox: {x: 8}}\n",
    }))
    envelope = result["structured"]
    assert envelope["ok"] and envelope["status"] == "verified", envelope.get("summary")
    assert any(check["id"] == "advisory_size" and check["status"] == "warn" for check in envelope["checks"])
    meta = json.loads(project.rev_dir(1).joinpath("meta.json").read_text())
    receipt = meta["verification_receipt"]
    assert "proven_error_requirement_ids" not in receipt
    assert handlers._rev_meta(project, 1)["status"] == "verified"
    inspected = asyncio.run(Handlers().aad_inspect({"project": "p", "what": "summary"}))["structured"]
    assert inspected["status"] == "verified"


def test_verified_dry_run_returns_draft_without_crashing(project):
    result = asyncio.run(Handlers().aad_build({
        "project": "p",
        "source": "from build123d import Box\nresult = Box(10, 8, 4)\n",
        "spec": "requirements: [{id: body, solids: 1}]\n",
        "dry_run": True,
    }))
    envelope = result["structured"]
    assert envelope["ok"] and envelope["status"] == "draft"
    assert "no revision was saved" in envelope["summary"]
    assert envelope["next"]
    assert project.head_rev is None


def test_clean_scene_failure_keeps_brep_draft_and_non_exportable(project, monkeypatch):
    from aad.engine import validation

    def scene_failure(evidence, _wire, **_kwargs):
        evidence.scene_error = "RuntimeError: clean scene renderer exited with status 42"

    monkeypatch.setattr(validation, "_accept_scene", scene_failure)
    result = asyncio.run(Handlers().aad_build({
        "project": "p",
        "source": "from build123d import Box\nresult = Box(10, 8, 4)\n",
        "spec": "requirements: [{id: body, solids: 1}]\n",
    }))
    envelope = result["structured"]
    assert envelope["ok"] and envelope["status"] == "draft", envelope.get("summary")
    assert "scene generation failed" in envelope["summary"]
    assert "status 42" in envelope["summary"]
    check = next(check for check in envelope["checks"] if check["id"] == "scene_generation_failed")
    assert check["status"] == "skip" and check["level"] == "error"
    revision = project.rev_dir(1)
    assert (revision / "part.brep").is_file()
    assert handlers._rev_meta(project, 1)["status"] == "draft"
    exported = asyncio.run(Handlers().aad_export({"project": "p", "formats": ["step"]}))["structured"]
    assert not exported["ok"] and exported["failure"]["code"] == "NOT_VERIFIED"


@pytest.mark.parametrize("tamper", ["spec", "brep", "metrics", "checks", "proven", "scene"])
def test_modified_verified_revision_is_downgraded_before_export(project, tmp_path, monkeypatch, tamper):
    revision = _write_verified_revision(project)
    if tamper == "spec":
        (revision / "spec.yaml").write_text("requirements: [{id: altered, solids: 1}]\n")
    elif tamper == "brep":
        (revision / "part.brep").write_bytes(b"swapped BREP")
    else:
        meta_path = revision / "meta.json"
        meta = json.loads(meta_path.read_text())
        if tamper == "metrics":
            meta["metrics"]["bbox"][0] = 999
        elif tamper == "checks":
            meta["checks"][0]["message"] = "edited evidence"
        elif tamper == "proven":
            meta["verification_receipt"]["proven_requirement_ids"] = ["forged"]
        else:
            meta["scene"] = None
            meta["verification_receipt"]["scene_id"] = None
            (revision / "scene.bin.gz").unlink()
        meta_path.write_text(json.dumps(meta))

    from aad_platform.daemon.handlers import _rev_meta
    checked = _rev_meta(project, 1)
    assert checked["status"] == "draft"
    assert any(word in checked["verification_invalid_reason"] for word in ("hash", "match"))
    inspected = asyncio.run(Handlers().aad_inspect({"project": "p", "what": "summary"}))["structured"]
    assert inspected["status"] == "draft"
    assert any(word in inspected["summary"] for word in ("hash", "receipt"))

    response = asyncio.run(Handlers().aad_export({"project": "p", "formats": ["step"]}))
    envelope = response["structured"]
    assert not envelope["ok"]
    assert envelope["failure"]["code"] == "NOT_VERIFIED"
    assert envelope["status"] == "failed"
    assert "treated as draft" in envelope["failure"]["message"]
    assert "allow_unverified" in envelope["failure"]["message"]

    recorded = {}
    async def fake_op(_self, _project, _rev, _op, args, **_kwargs):
        recorded.update(args)
        out = tmp_path / "unverified-output"
        out.mkdir(exist_ok=True)
        source = out / "model.step"
        source.write_text("step")
        return {"ok": True, "files": {"step": str(source)}}, out

    monkeypatch.setattr(Handlers, "_op", fake_op)
    exported = asyncio.run(Handlers().aad_export({
        "project": "p", "formats": ["step"], "allow_unverified": True,
    }))["structured"]
    assert exported["ok"] and exported["status"] == "draft"
    assert recorded["stem"] == "p-r1-UNVERIFIED"
    assert "no longer match" in exported["summary"]


def _write_verified_revision(project):
    revision = project.rev_dir(1)
    revision.mkdir(parents=True)
    brep = b"saved BREP"
    scene = b"saved clean scene"
    (revision / "part.brep").write_bytes(brep)
    (revision / "spec.yaml").write_text("requirements: [{id: body, solids: 1}]\n")
    (revision / "scene.bin.gz").write_bytes(scene)
    versions = {}
    for package in ("build123d", "cadquery-ocp-novtk"):
        try:
            versions[package] = importlib_metadata.version(package)
        except importlib_metadata.PackageNotFoundError:
            versions[package] = "unavailable"
    settings = {"units": "mm", "process": None}
    canonical = lambda value: json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    digest = lambda value: hashlib.sha256(value).hexdigest()
    spec = parse_spec((revision / "spec.yaml").read_text())
    spec_hash = digest(canonical(spec))
    validation_record = {
        "protocol": 1, "snapshot_sha256": digest(brep), "spec_sha256": spec_hash,
        "kernel_versions": versions,
        "inputs": {"kind": "part", "units": "mm", "spec": spec},
    }
    claims = {
        "requirement_results": [{"id": "body", "level": "error", "status": "pass"}],
        "proven_requirement_ids": ["body"],
        "proven_error_requirement_ids": ["body"],
    }
    metrics = {
        "units": "mm", "bbox": [10.0, 8.0, 4.0], "volume": 320.0,
        "solids": 1, "faces": 6, "edges": 12, "valid": True, "build_ms": 1.0,
    }
    checks = [{
        "id": "body", "suite": "spec", "level": "error", "status": "pass",
        "message": "Solid-count requirement passed.", "expected": "1", "actual": "1",
    }]
    checks_summary = {"pass": 1, "fail": 0, "warn": 0, "skip": 0, "not_applicable": 0}
    receipt = {
        "rev": 1,
        "kind": "part",
        "brep_sha256": digest(brep),
        "spec_sha256": spec_hash,
        "kernel_versions": versions,
        "kernel_sha256": digest(canonical(versions)),
        "validation_input_sha256": digest(canonical(validation_record)),
        "project_settings": settings,
        "project_settings_sha256": digest(canonical(settings)),
        **claims,
        "claims_sha256": digest(canonical(claims)),
        "scene_id": digest(scene),
    }
    meta = {
        "rev": 1, "status": "verified", "verification_receipt": receipt,
        "metrics": metrics, "checks": checks, "checks_summary": checks_summary,
        "scene": {"scene_id": receipt["scene_id"]},
    }
    receipt["revision_evidence_sha256"] = handlers._verification_evidence_hash(meta, receipt)
    (revision / "meta.json").write_text(json.dumps(meta))
    meta = project.meta()
    meta["head_rev"] = 1
    project.write_meta(meta)
    return revision


def test_import_replaces_destination_symlink_without_following_it(project, tmp_path, monkeypatch):
    outside = tmp_path / "outside.step"
    outside.write_text("keep")
    source = tmp_path / "part.step"
    source.write_text("model")
    imports = _project_directory(project, "imports")
    (imports / source.name).symlink_to(outside)

    async def fake_op(*_, **__):
        return {"ok": True, "imported": {}}

    monkeypatch.setattr(handlers, "run_op", fake_op)
    result = asyncio.run(Handlers().aad_import({"project": "p", "path": str(source)}))
    assert result["structured"]["ok"]
    assert result["structured"]["imported"]["project_kind"] == "part"
    assert outside.read_text() == "keep"
    assert not (imports / source.name).is_symlink()
    assert (imports / source.name).read_text() == "model"


def test_export_preflights_all_destinations(project, tmp_path):
    exports = _project_directory(project, "exports")
    out = tmp_path / "output"
    out.mkdir()
    (out / "p.step").write_text("step")
    (out / "p.stl").write_text("stl")
    (exports / "p.stl").mkdir()
    with pytest.raises(ValueError):
        _publish_exports(exports, out, {fmt: str(out / f"p.{fmt}") for fmt in ("step", "stl")}, "p")
    assert not (exports / "p.step").exists()


def test_export_rolls_back_published_files(project, tmp_path, monkeypatch):
    exports = _project_directory(project, "exports")
    out = tmp_path / "output"
    out.mkdir()
    for fmt in ("step", "stl"):
        (out / f"p.{fmt}").write_text("new")
        (exports / f"p.{fmt}").write_text("old")
    replace = handlers.os.replace

    def fail_second(source, target):
        if Path(source) == out / "p.stl":
            raise OSError("disk full")
        return replace(source, target)

    monkeypatch.setattr(handlers.os, "replace", fail_second)
    with pytest.raises(OSError, match="disk full"):
        _publish_exports(exports, out, {fmt: str(out / f"p.{fmt}") for fmt in ("step", "stl")}, "p")
    assert all((exports / f"p.{fmt}").read_text() == "old" for fmt in ("step", "stl"))


def test_library_preserves_worker_timeout(monkeypatch):
    async def timeout(*_, **__):
        return {"ok": False, "failure": {"code": "TIMEOUT", "phase": "timeout", "message": "timed out"}}

    monkeypatch.setattr(handlers, "run_op", timeout)
    result = asyncio.run(Handlers().aad_library({"action": "search", "query": "bolt"}))
    assert result["structured"]["failure"]["code"] == "TIMEOUT"
    assert result["structured"]["failure"]["phase"] == "timeout"


def test_editable_guidance_is_discoverable_with_only_worker_read_mounts(tmp_path, monkeypatch):
    from aad_platform.daemon import ops_client
    from aad_quality import catalog

    root = tmp_path / "checkout"
    package = root / "packages" / "quality"
    origin = package / "src" / "aad_quality" / "__init__.py"
    origin.parent.mkdir(parents=True)
    origin.touch()
    (package / "pyproject.toml").write_text('[project]\nname = "aad-quality"\n')
    (package / "unrelated.txt").write_text("not needed by the worker")
    guidance = root / "skills"
    (guidance / "playbooks").mkdir(parents=True)
    monkeypatch.setattr(ops_client.importlib.util, "find_spec", lambda name: SimpleNamespace(origin=str(origin)) if name == "aad_quality" else None)
    monkeypatch.setattr(catalog, "__file__", str(origin))
    monkeypatch.setattr(catalog, "files", lambda name: origin.parent)
    reads = ops_client._package_roots()

    # Linux bubblewrap hides files outside the explicit read mounts. Unlike
    # Seatbelt's metadata permission, even is_file() cannot see an unmounted file.
    is_file, is_dir = Path.is_file, Path.is_dir

    def visible(path):
        return any(path == allowed or (is_dir(allowed) and path.is_relative_to(allowed)) for allowed in reads)

    monkeypatch.setattr(Path, "is_file", lambda path: visible(path) and is_file(path))
    monkeypatch.setattr(Path, "is_dir", lambda path: visible(path) and is_dir(path))
    assert catalog._guidance_root() == guidance
    assert not (package / "unrelated.txt").is_file()


def test_duplicate_yaml_cannot_create_or_advance_project(project):
    duplicate = "requirements:\n  - id: size\n    bbox: {x: 10}\n    bbox: {x: 5}\n"
    with pytest.raises(ValueError, match="duplicate YAML key"):
        parse_spec(duplicate)
    handler = Handlers()
    result = asyncio.run(handler.aad_new({"name": "bad", "spec": duplicate}))
    assert result["structured"]["failure"]["code"] == "BAD_INPUT"
    assert not (project.path.parent / "bad").exists()
    result = asyncio.run(handler.aad_build({"project": "p", "source": "result = None", "spec": duplicate}))
    assert result["structured"]["failure"]["code"] == "BAD_INPUT"
    assert project.head_rev is None


def test_build_timeout_defaults_and_accepts_a_bounded_value():
    assert handlers._build_timeout(None) == handlers.BUILD_TIMEOUT_S
    assert handlers._build_timeout(240) == 240.0
    assert handlers._build_timeout(handlers.MAX_BUILD_TIMEOUT_S) == handlers.MAX_BUILD_TIMEOUT_S


@pytest.mark.parametrize("value", [0, -1, True, "90", float("nan"), float("inf"), handlers.MAX_BUILD_TIMEOUT_S + 1, 10**1000])
def test_build_timeout_refuses_values_outside_its_limit(value):
    with pytest.raises(ValueError, match="timeout_s"):
        handlers._build_timeout(value)


def test_ops_client_passes_configured_address_space_to_sandbox(tmp_path, monkeypatch, project):
    import json

    from aad.engine import sandbox
    from aad_platform import config
    from aad_platform.daemon import ops_client

    config.set_value("engine.memory", "4GiB")
    out = tmp_path / "out"
    seen = {}

    def communicate(data, timeout):
        seen["request"] = json.loads(data)
        return ops_client.RESULT_MARKER + json.dumps({"ok": True}).encode() + b"\n", b""

    process = SimpleNamespace(
        returncode=0,
        communicate=communicate,
    )

    def fake_popen(argv, job_dir, **kwargs):
        seen.update(argv=argv, job_dir=job_dir, **kwargs)
        return process

    monkeypatch.setattr(sandbox, "popen", fake_popen)
    result = ops_client._run_sandboxed({"op": "library", "args": {}, "out_dir": str(out)}, timeout_s=10)
    assert result == {"ok": True}
    from aad.engine import memory

    assert seen["address_space"].bytes == memory.bootstrap_address_space_budget(4 * 1024 ** 3).bytes
    assert seen["request"]["memory_limit_bytes"] == 4 * 1024 ** 3


def test_operation_worker_starts_under_configured_memory_budget(tmp_path, project):
    from aad_platform import config
    from aad_platform.daemon import ops_client

    config.set_value("engine.memory", "4GiB")
    result = ops_client._run_sandboxed(
        {
            "op": "library",
            "args": {"action": "search", "kind": "material", "query": "steel"},
            "out_dir": str(tmp_path / "out"),
        },
        timeout_s=60,
    )
    assert result["ok"], result
    assert "library" in result


def test_graphics_operation_keeps_memory_bootstrap_without_seatbelt(tmp_path, monkeypatch, project):
    import json

    from aad.engine import memory, sandbox
    from aad_platform import config
    from aad_platform.daemon import ops_client

    config.set_value("engine.memory", "5GiB")
    monkeypatch.delenv("AAD_REMOTE", raising=False)
    seen = {}
    prepared = {
        "version": 1,
        "render": {"request": {}},
        "inspection": {},
        "render_payload_file": ops_client.PREPARED_RENDER_FILENAME,
    }

    def worker_communicate(data, timeout):
        seen["request"] = json.loads(data)
        return ops_client.RESULT_MARKER + json.dumps({"ok": True, "prepared_render": prepared}).encode() + b"\n", b""

    worker_process = SimpleNamespace(
        returncode=0,
        communicate=worker_communicate,
    )
    renderer_process = SimpleNamespace(
        returncode=0,
        communicate=lambda data, timeout: (
            ops_client.RESULT_MARKER + json.dumps({"ok": True, "rendered": {}}).encode() + b"\n",
            b"",
        ),
    )

    def fake_sandbox_popen(argv, job_dir, **kwargs):
        seen.update(worker_argv=argv, worker_job_dir=job_dir, worker_kwargs=kwargs)
        (Path(job_dir) / ops_client.PREPARED_RENDER_FILENAME).write_bytes(b"mesh payload")
        return worker_process

    def fake_renderer_popen(argv, **kwargs):
        seen.update(renderer_argv=argv, renderer_kwargs=kwargs)
        return renderer_process

    monkeypatch.setattr(sandbox, "popen", fake_sandbox_popen)
    monkeypatch.setattr(ops_client.subprocess, "Popen", fake_renderer_popen)
    import aad_render

    monkeypatch.setattr(aad_render, "finish_render", lambda _prepared, _rendered, out: [Path(out) / "front.png"])
    result = ops_client._run_sandboxed({"op": "render", "args": {}, "out_dir": str(tmp_path / "out")}, timeout_s=10)
    assert result["ok"]
    assert result["images"][0]["path"].endswith("front.png")
    assert seen["worker_kwargs"]["address_space"].bytes == memory.bootstrap_address_space_budget(5 * 1024 ** 3).bytes
    assert Path(seen["renderer_argv"][4]) == Path(memory.__file__).resolve()
    assert seen["renderer_argv"][5] == str(memory.bootstrap_address_space_budget(5 * 1024 ** 3).bytes)
    assert seen["request"]["memory_limit_bytes"] == 5 * 1024 ** 3


def test_operation_allocation_failure_uses_out_of_memory_code():
    from aad_platform.ops import _classify

    failure = _classify(MemoryError("bounded allocation fixture"))
    assert failure["code"] == "OUT_OF_MEMORY"
    assert failure["phase"] == "oom"


def test_check_operation_does_not_downgrade_memory_error_to_failed_suite(tmp_path, monkeypatch):
    import aad_platform.ops as ops

    def fail(_ctx):
        raise MemoryError("bounded allocation fixture")

    monkeypatch.setattr(ops, "_optional", lambda *_args: fail)
    with pytest.raises(MemoryError, match="bounded allocation fixture"):
        ops.op_check(SimpleNamespace(kind="part"), {"suites": ["hard"]}, tmp_path)


def test_ops_client_reports_memory_setup_failure_as_sandbox_denied(tmp_path, monkeypatch, project):
    from aad.engine import memory, sandbox
    from aad_platform import config
    from aad_platform.daemon import ops_client

    config.set_value("engine.memory", "64MiB")
    process = SimpleNamespace(
        returncode=1,
        communicate=lambda _data, timeout: (
            memory.SETUP_FAILURE_MARKER + b"ValueError: current limit exceeds maximum limit\n",
            b"ValueError: current limit exceeds maximum limit\n",
        ),
    )
    monkeypatch.setattr(sandbox, "popen", lambda *args, **kwargs: process)
    result = ops_client._run_sandboxed(
        {"op": "library", "args": {}, "out_dir": str(tmp_path / "out")}, timeout_s=10
    )
    assert result["failure"]["code"] == "SANDBOX_DENIED"
    assert "operation did not run" in result["failure"]["message"]


def test_ops_client_keeps_unrelated_memory_text_as_worker_crash(tmp_path, monkeypatch, project):
    from aad.engine import sandbox
    from aad_platform.daemon import ops_client

    process = SimpleNamespace(
        returncode=1,
        communicate=lambda _data, timeout: (b"", b"RuntimeError: no out of memory condition was found\n"),
    )
    monkeypatch.setattr(sandbox, "popen", lambda *args, **kwargs: process)
    result = ops_client._run_sandboxed(
        {"op": "library", "args": {}, "out_dir": str(tmp_path / "out")}, timeout_s=10
    )
    assert result["failure"]["code"] == "WORKER_CRASH"


def test_empty_suites_still_allow_the_spec_reference(tmp_path):
    from types import SimpleNamespace

    from aad_platform.daemon import ops_client

    (tmp_path / "imports").mkdir()
    reference = tmp_path / "imports" / "ref.step"
    reference.write_text("data")
    rev_dir = tmp_path / ".aad" / "revs" / "000001"
    rev_dir.mkdir(parents=True)
    (rev_dir / "spec.yaml").write_text(
        "requirements:\n  - {id: r, matches_reference: {path: imports/ref.step, f_score: {min: 0.5}}}\n"
    )
    project = SimpleNamespace(path=tmp_path)
    assert ops_client._spec_reference_reads(project, 1, {"suites": []}) == [str(reference.resolve())]
    assert ops_client._spec_reference_reads(project, 1, {"suites": ["hard"]}) == []
