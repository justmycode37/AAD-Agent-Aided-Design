"""Persistent per-project part candidates remain geometry-only optimizations."""

from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest

from aad.engine.jobs import run_build
from aad.engine.types import BuildRequest


SOURCE = '''from aad import *
P = params(size=2.0)
meta(name="cache_probe", units="mm")
_builder_calls = 0
def make_block():
    global _builder_calls
    _builder_calls += 1
    return Box(P.size, 3, 4)
asm = assembly("cache_probe")
for i in range(6):
    shape = cached_part({"family": "test_block", "size": P.size}, make_block)
    asm.add(f"block_{i}", shape, at=Pos(i * 5, 0, 0))
print(f"builder_calls={_builder_calls}")
result = asm
'''


def _build(project: Path, run: int, *, cache_disabled: bool = False, spec: dict | None = None):
    environment = {"AAD_DISABLE_GEOMETRY_CACHE": "1"} if cache_disabled else {}
    with patch.dict(os.environ, environment, clear=False):
        return run_build(BuildRequest(
            source=SOURCE,
            project_dir=project,
            job_dir=project / f"job-{run}",
            kind="assembly",
            spec=spec or {},
            timeout_s=60,
        ))


def _geometry(result):
    from build123d import import_brep

    return import_brep(result.artifacts["part.brep"])


def _assert_same_geometry(first, second):
    assert len(first.solids()) == len(second.solids()) == 6
    assert first.volume == pytest.approx(second.volume, abs=1e-9)
    assert tuple(first.bounding_box().size) == pytest.approx(tuple(second.bounding_box().size), abs=1e-9)
    assert (first - second).volume == pytest.approx(0, abs=1e-9)
    assert (second - first).volume == pytest.approx(0, abs=1e-9)


def test_cached_parts_reuse_geometry_but_recompute_the_full_gate(tmp_path):
    cold = _build(tmp_path, 1)
    assert cold.ok, cold.failure
    assert cold.stdout.strip().endswith("builder_calls=1")
    assert cold.metrics["geometry_cache"] == {
        "enabled": True,
        "calls": 6,
        "persistent_hits": 0,
        "memory_hits": 5,
        "misses": 1,
        "rejected_entries": 0,
        "uncacheable": 0,
        "stored": 1,
        "write_errors": 0,
    }

    hit = _build(tmp_path, 2)
    assert hit.ok, hit.failure
    assert hit.stdout.strip().endswith("builder_calls=0")
    assert hit.metrics["geometry_cache"]["persistent_hits"] == 1
    assert hit.metrics["geometry_cache"]["misses"] == 0
    assert hit.metrics["phase_timings_ms"]["gate.mesh_watertight"] > 0
    mesh_check = next(check for check in hit.checks if check.id == "mesh_watertight")
    assert mesh_check.status == "pass"
    assert "unique_topologies=1" in mesh_check.actual
    assert "unique_meshes=1" in mesh_check.actual
    assert "reused_meshes=5" in mesh_check.actual
    assert all(check.status != "fail" for check in hit.checks if check.suite == "hard")

    uncached = _build(tmp_path, 3, cache_disabled=True)
    assert uncached.ok, uncached.failure
    assert uncached.stdout.strip().endswith("builder_calls=6")
    assert uncached.metrics["geometry_cache"]["enabled"] is False
    _assert_same_geometry(_geometry(cold), _geometry(hit))
    _assert_same_geometry(_geometry(cold), _geometry(uncached))


def test_corrupt_candidate_is_a_miss_and_runs_the_builder(tmp_path):
    first = _build(tmp_path, 1)
    assert first.ok, first.failure
    payloads = list((tmp_path / ".aad" / "cache").glob("[0-9a-f]" * 64 + "/part.brep"))
    assert len(payloads) == 1
    payload = payloads[0]
    payload.chmod(0o600)
    payload.write_bytes(b"corrupt cache bytes")

    rebuilt = _build(tmp_path, 2)
    assert rebuilt.ok, rebuilt.failure
    assert rebuilt.stdout.strip().endswith("builder_calls=1")
    assert rebuilt.metrics["geometry_cache"]["persistent_hits"] == 0
    assert rebuilt.metrics["geometry_cache"]["misses"] == 1
    assert rebuilt.metrics["phase_timings_ms"]["gate.mesh_watertight"] > 0


def test_manifest_key_mismatch_is_ignored_and_rebuilt(tmp_path):
    first = _build(tmp_path, 1)
    assert first.ok, first.failure
    manifests = list((tmp_path / ".aad" / "cache").glob("[0-9a-f]" * 64 + "/manifest.json"))
    assert len(manifests) == 1
    manifest_path = manifests[0]
    manifest_path.chmod(0o600)
    manifest = json.loads(manifest_path.read_text())
    manifest["key"] = "f" * 64
    manifest_path.write_text(json.dumps(manifest))

    rebuilt = _build(tmp_path, 2)
    assert rebuilt.ok, rebuilt.failure
    assert rebuilt.stdout.strip().endswith("builder_calls=1")
    assert rebuilt.metrics["geometry_cache"]["persistent_hits"] == 0
    assert rebuilt.metrics["geometry_cache"]["misses"] == 1


def test_cache_entries_are_project_scoped(tmp_path):
    first_project = tmp_path / "project-one"
    second_project = tmp_path / "project-two"
    first = _build(first_project, 1)
    second = _build(second_project, 1)

    assert first.ok and second.ok
    assert first.stdout.strip().endswith("builder_calls=1")
    assert second.stdout.strip().endswith("builder_calls=1")
    assert first.metrics["geometry_cache"]["persistent_hits"] == 0
    assert second.metrics["geometry_cache"]["persistent_hits"] == 0
    assert (first_project / ".aad" / "cache").is_dir()
    assert (second_project / ".aad" / "cache").is_dir()


def test_failed_check_does_not_publish_part_candidates(tmp_path):
    failed = _build(
        tmp_path,
        1,
        spec={"requirements": [{"id": "wrong_width", "bbox": {"x": "99 ± 0.1"}}]},
    )

    assert not failed.ok
    assert failed.failure is not None
    assert failed.failure.code == "REQUIREMENT_FAILED"
    assert not (tmp_path / ".aad" / "cache").exists()


def test_different_builder_functions_do_not_collide_on_same_key_inputs(tmp_path):
    source = '''from aad import *
meta(name="builder_identity_probe", units="mm")
small = cached_part({"family": "block"}, lambda: Box(2, 3, 4))
large = cached_part({"family": "block"}, lambda: Box(5, 3, 4))
result = assembly("builder_identity_probe")
result.add("small", small)
result.add("large", large, at=Pos(10, 0, 0))
'''
    result = run_build(BuildRequest(
        source=source,
        project_dir=tmp_path,
        job_dir=tmp_path / "job-builders",
        kind="assembly",
        spec={},
        timeout_s=60,
    ))

    assert result.ok, result.failure
    metrics = result.metrics["geometry_cache"]
    assert metrics["calls"] == 2
    assert metrics["misses"] == 2
    assert metrics["persistent_hits"] == 0
    assert metrics["stored"] == 2
    shape = _geometry(result)
    assert len(shape.solids()) == 2
    assert sorted(solid.bounding_box().size.X for solid in shape.solids()) == [2, 5]


def _project(tmp_path: Path) -> Path:
    project = tmp_path / "project"
    project.mkdir()
    return project


def test_offered_cached_geometry_stays_unproven_until_a_clean_rebuild(tmp_path):
    project = _project(tmp_path)
    assert not any(c.id == "part_cache_unreproduced" for c in _build(project, 1).checks)
    second = _build(project, 2)
    unproven = [c for c in second.checks if c.id == "part_cache_unreproduced"]
    assert second.ok and unproven[0].level == "error" and unproven[0].status == "skip"
    clean = run_build(BuildRequest(
        source=SOURCE, project_dir=project, job_dir=project / "job-3", kind="assembly", part_cache=False, timeout_s=120,
    ))
    assert clean.ok, clean.failure
    assert not any(c.id == "part_cache_unreproduced" for c in clean.checks)
    assert clean.metrics["geometry_cache"]["persistent_hits"] == 0


def test_cache_prune_removes_oldest_entries(tmp_path):
    from aad.engine.cache import GeometryCache

    root = tmp_path / "cache"
    root.mkdir()
    for index, name in enumerate(("a" * 64, "b" * 64)):
        entry = root / name
        entry.mkdir()
        (entry / "part.brep").write_bytes(b"x" * 100)
        os.utime(entry, ns=(index + 1, index + 1))
    cache = GeometryCache(root)
    assert cache.prune(150) == 1
    assert not (root / ("a" * 64)).exists() and (root / ("b" * 64)).exists()


def test_oversized_candidate_record_is_not_published(tmp_path):
    from aad.engine import jobs

    pending = tmp_path / "exchange" / "pending" / ("a" * 64)
    pending.mkdir(parents=True)
    (pending / "part.brep").write_bytes(b"x")
    (pending / "input_record.json").write_bytes(b" " * (jobs._MAX_CACHE_RECORD_BYTES + 1))
    (tmp_path / ".aad").mkdir()
    stored, errors = jobs._publish_part_cache(
        tmp_path, tmp_path / "exchange", _Result(), source="", pipeline_identity="",
        kernel_versions={}, asset_sha256={}, spec={}, kind="part", units="mm",
    )
    assert (stored, errors) == (0, 0)


class _Context:
    material = None
    process = None
    params: dict = {}


class _Result:
    ok = True
    context = _Context()
