"""Opaque byte integrity tests, plus separate real-identity/native probes.

Subprocess programs here are trusted lifecycle fixtures, never a production
fallback for executing unsandboxed model source.
"""

from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import os
import select
import shutil
import stat
import subprocess
import sys
from decimal import Decimal
from pathlib import Path

import pytest

from aiad.engine import cache as module
from aiad.engine.cache import (
    CacheConflict, CacheCorruption, CacheInputError, CacheMiss, CacheUnsupported, GeometryCache,
)


SOURCE_DIR = Path(__file__).resolve().parents[1] / "src"
PAYLOAD = b"opaque candidate, not a native validity claim\x00\xff\r\n"


def record():
    return {
        "schema": "aiad.cache-identity.v1", "source": "# π\r\nresult = Box(2, 3, 4)\n",
        "applied_params": {"width": 2.0, "nested": [True, None, {"n": -0.0}]},
        "spec": {"requirements": [{"id": "body", "solids": 1}], "unknown_extension": [3]},
        "kind": "part", "units": "mm", "material": None, "process": None,
        "kernel_versions": {"build123d": "0.13.0", "occt": "8.0.1.1.0"},
        "tolerances": {"linear": 0.001}, "asset_sha256": {"fixture.step": "a" * 64},
        "pipeline_identity": "trusted-test-pipeline-snapshot:1",
    }


def encoded(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")).encode()


def key_for(value):
    return hashlib.sha256(encoded(value)).hexdigest()


def candidate(directory, payload=PAYLOAD):
    directory.mkdir(mode=0o700)
    source = directory / "part.brep"
    source.write_bytes(payload)
    return source


def rewrite(path, contents):
    path.chmod(0o600)
    path.write_bytes(contents)
    path.chmod(0o400)


@pytest.fixture
def stored(tmp_path):
    inputs = record()
    source = candidate(tmp_path / "source")
    cache = GeometryCache(tmp_path / "cache")
    entry = cache.put(key_for(inputs), inputs, source, candidate_dir=source.parent)
    return cache, inputs, source, entry


def test_exact_roundtrip_private_copy_and_independent_deletion(tmp_path, stored):
    cache, inputs, source, entry = stored
    # Neither this Python file nor the opaque payload is imported/executed.
    (source.parent / "model.py").write_text("raise AssertionError('must not execute')")
    revision = tmp_path / "revs" / "000001"
    revision.mkdir(parents=True)
    (revision / "part.brep").write_bytes(b"numbered history")
    before = source.stat()
    assert entry == cache.lookup(entry.key, inputs)
    assert entry.byte_length == len(PAYLOAD)
    assert entry.sha256 == hashlib.sha256(PAYLOAD).hexdigest()
    assert {p.name for p in entry.brep_path.parent.iterdir()} == {"part.brep", "manifest.json"}
    restored = cache.restore(entry.key, inputs, tmp_path / "restored")
    assert restored.read_bytes() == source.read_bytes() == entry.brep_path.read_bytes() == PAYLOAD
    assert len({(p.stat().st_dev, p.stat().st_ino) for p in (source, entry.brep_path, restored)}) == 3
    assert all(p.stat().st_nlink == 1 for p in (source, entry.brep_path, restored))
    assert stat.S_ISREG(restored.stat().st_mode) and stat.S_IMODE(restored.stat().st_mode) == 0o600
    assert stat.S_IMODE(restored.parent.stat().st_mode) == 0o700
    assert (source.stat().st_ino, source.stat().st_mtime_ns) == (before.st_ino, before.st_mtime_ns)
    restored.write_bytes(b"independent candidate edit")
    assert source.read_bytes() == entry.brep_path.read_bytes() == PAYLOAD
    shutil.rmtree(cache.cache_dir)
    assert (revision / "part.brep").read_bytes() == b"numbered history"
    assert restored.read_bytes() == b"independent candidate edit"


def test_miss_creates_neither_cache_nor_destination(tmp_path):
    inputs = record()
    cache = GeometryCache(tmp_path / "cache")
    with pytest.raises(CacheMiss):
        cache.lookup(key_for(inputs), inputs)
    with pytest.raises(CacheMiss):
        cache.restore(key_for(inputs), inputs, tmp_path / "restored")
    assert not cache.cache_dir.exists() and not (tmp_path / "restored").exists()


@pytest.mark.parametrize("operation", ["put", "lookup", "restore"])
@pytest.mark.parametrize("key", ["a" * 64, "A" * 64, "a" * 63, "../escape"])
def test_key_binding_and_safe_key_paths(tmp_path, operation, key):
    inputs = record()
    source = candidate(tmp_path / "source")
    cache = GeometryCache(tmp_path / "cache")
    with pytest.raises(CacheInputError):
        if operation == "put":
            cache.put(key, inputs, source, candidate_dir=source.parent)
        elif operation == "lookup":
            cache.lookup(key, inputs)
        else:
            cache.restore(key, inputs, tmp_path / "restored")
    assert not cache.cache_dir.exists()


@pytest.mark.parametrize("field", sorted(record()))
def test_every_identity_field_is_required(tmp_path, field):
    inputs = record()
    del inputs[field]
    with pytest.raises(CacheInputError):
        GeometryCache(tmp_path / "cache").lookup(key_for(inputs), inputs)


@pytest.mark.parametrize(("field", "value"), [
    ("extra", 1), ("schema", "other"), ("source", None), ("kind", " "), ("units", ""),
    ("material", ""), ("process", 3), ("pipeline_identity", None),
    ("applied_params", []), ("spec", None), ("tolerances", []),
    ("kernel_versions", {}), ("kernel_versions", {"": "1"}),
    ("kernel_versions", {"occt": 1}), ("kernel_versions", {"occt": " "}),
    ("asset_sha256", []), ("asset_sha256", {" ": "a" * 64}),
    ("asset_sha256", {"x": "A" * 64}), ("asset_sha256", {"x": "a" * 63}),
])
def test_complete_schema_has_no_inferred_defaults(tmp_path, field, value):
    inputs = record()
    inputs[field] = value
    with pytest.raises(CacheInputError):
        GeometryCache(tmp_path / "cache").lookup(key_for(inputs), inputs)


@pytest.mark.parametrize("bad", [(1, 2), b"bytes", {1}, Decimal("1.2"), object(),
                                  float("nan"), float("inf"), float("-inf"), {1: "key"}, "\ud800"])
def test_non_plain_or_nonfinite_json_is_never_coerced(tmp_path, bad):
    inputs = record()
    key = key_for(inputs)
    inputs["applied_params"]["bad"] = bad
    with pytest.raises(CacheInputError):
        GeometryCache(tmp_path / "cache").lookup(key, inputs)


def test_custom_shared_and_cyclic_values_are_rejected(tmp_path):
    class CustomInt(int):
        pass

    class CustomDict(dict):
        pass

    shared = []
    cyclic = []
    cyclic.append(cyclic)
    for bad in (CustomInt(1), CustomDict(), [shared, shared], cyclic):
        inputs = record()
        key = key_for(inputs)
        inputs["spec"]["bad"] = bad
        with pytest.raises(CacheInputError):
            GeometryCache(tmp_path / "cache").lookup(key, inputs)


def test_numeric_types_and_signed_zero_remain_distinct(tmp_path):
    source = candidate(tmp_path / "source")
    cache = GeometryCache(tmp_path / "cache")
    keys = set()
    for number in (True, 1, 1.0, 0.0, -0.0):
        inputs = record()
        inputs["applied_params"] = {"n": number}
        key = key_for(inputs)
        keys.add(key)
        entry = cache.put(key, inputs, source, candidate_dir=source.parent)
        manifest = json.loads((entry.brep_path.parent / "manifest.json").read_bytes())
        assert encoded(manifest["input_record"]) == encoded(inputs)
        assert cache.lookup(key, inputs) == entry
    assert len(keys) == 5


def test_caller_record_is_detached_before_file_io(tmp_path, monkeypatch):
    inputs = record()
    original = copy.deepcopy(inputs)
    key = key_for(inputs)
    source = candidate(tmp_path / "source")
    cache = GeometryCache(tmp_path / "cache")
    real_copy = module._copy_and_hash

    def mutate_after_snapshot(path, output=None):
        if output is not None:
            inputs["applied_params"]["nested"][2]["n"] = 99
            inputs["spec"]["requirements"][0]["id"] = "changed"
        return real_copy(path, output)

    monkeypatch.setattr(module, "_copy_and_hash", mutate_after_snapshot)
    entry = cache.put(key, inputs, source, candidate_dir=source.parent)
    assert cache.lookup(key, original) == entry
    with pytest.raises(CacheInputError, match="key does not match"):
        cache.lookup(key, inputs)
    assert source.read_bytes() == PAYLOAD


def test_identical_publication_reuses_and_conflict_preserves(stored, tmp_path):
    cache, inputs, source, entry = stored
    manifest = entry.brep_path.parent / "manifest.json"
    before = (entry.brep_path.stat().st_ino, manifest.stat().st_ino, manifest.read_bytes())
    identical = candidate(tmp_path / "identical")
    assert cache.put(entry.key, inputs, identical, candidate_dir=identical.parent) == entry
    different = candidate(tmp_path / "different", b"different candidate")
    with pytest.raises(CacheConflict, match="entry preserved"):
        cache.put(entry.key, inputs, different, candidate_dir=different.parent)
    assert (entry.brep_path.stat().st_ino, manifest.stat().st_ino, manifest.read_bytes()) == before
    assert entry.brep_path.read_bytes() == source.read_bytes() == PAYLOAD
    assert different.read_bytes() == b"different candidate"
    assert {p.name for p in cache.cache_dir.iterdir()} == {".lock", entry.key}


@pytest.mark.parametrize("damage", [
    "json", "utf8", "utf16", "array", "duplicate", "missing_field", "path", "key", "record", "schema",
    "size", "size_bool", "size_float", "size_zero", "hash", "bad_hash", "empty", "missing", "missing_manifest", "bytes", "extra",
])
def test_corrupt_entries_are_neither_hits_nor_overwritten(stored, tmp_path, damage):
    cache, inputs, source, entry = stored
    manifest_path = entry.brep_path.parent / "manifest.json"
    manifest = json.loads(manifest_path.read_bytes())
    if damage in {"json", "utf8", "utf16", "array", "duplicate"}:
        bad = {"json": b"{", "utf8": b"\xff", "utf16": encoded(manifest).decode().encode("utf-16"), "array": b"[]",
               "duplicate": b'{"key":"a","key":"b"}'}[damage]
        rewrite(manifest_path, bad)
    elif damage in {"empty", "missing", "missing_manifest", "bytes", "extra"}:
        if damage == "missing":
            entry.brep_path.unlink()
        elif damage == "missing_manifest":
            manifest_path.unlink()
        elif damage == "extra":
            (entry.brep_path.parent / "receipt.json").write_text("not cache evidence")
        else:
            rewrite(entry.brep_path, b"" if damage == "empty" else b"X" * len(PAYLOAD))
    else:
        if damage == "missing_field":
            del manifest["input_record"]
        elif damage == "path":
            manifest["path"] = "../../outside.brep"
        elif damage == "key":
            manifest["key"] = "a" * 64
        elif damage == "record":
            # These compare equal in Python; their canonical bytes must differ.
            manifest["input_record"]["applied_params"]["nested"][0] = 1
        elif damage == "schema":
            manifest["input_record"]["schema"] = "unknown"
        elif damage.startswith("size"):
            manifest["byte_length"] = {"size": len(PAYLOAD) + 1, "size_bool": True,
                                       "size_float": float(len(PAYLOAD)), "size_zero": 0}[damage]
        else:
            manifest["sha256"] = "0" * 64 if damage == "hash" else "Z" * 64
        rewrite(manifest_path, encoded(manifest))
    before = manifest_path.read_bytes() if manifest_path.exists() else None
    with pytest.raises(CacheCorruption):
        cache.lookup(entry.key, inputs)
    with pytest.raises(CacheCorruption):
        cache.restore(entry.key, inputs, tmp_path / "restored")
    with pytest.raises(CacheCorruption):
        cache.put(entry.key, inputs, source, candidate_dir=source.parent)
    assert (manifest_path.read_bytes() if manifest_path.exists() else None) == before
    assert not (tmp_path / "restored").exists()


@pytest.mark.parametrize(("member", "replacement"), [
    (member, replacement)
    for member in ("part.brep", "manifest.json", "entry")
    for replacement in ("symlink", "file_or_directory", "fifo", "hardlink")
    if (member, replacement) != ("entry", "hardlink")
])
def test_entry_path_and_file_types_are_checked(stored, tmp_path, member, replacement):
    cache, inputs, _, entry = stored
    path = entry.brep_path.parent if member == "entry" else entry.brep_path.parent / member
    outside = tmp_path / "outside"
    if member == "entry":
        shutil.copytree(path, outside)
        shutil.rmtree(path)
    else:
        outside.write_bytes(path.read_bytes())
        path.unlink()
    if replacement == "symlink":
        path.symlink_to(outside, target_is_directory=member == "entry")
    elif replacement == "hardlink":
        os.link(outside, path)
    elif replacement == "fifo":
        os.mkfifo(path)
    elif member == "entry":
        path.write_text("not a directory")
    else:
        path.mkdir()
    with pytest.raises(CacheCorruption):
        cache.lookup(entry.key, inputs)
    with pytest.raises(CacheCorruption):
        cache.restore(entry.key, inputs, tmp_path / "restored")
    assert not (tmp_path / "restored").exists()


@pytest.mark.parametrize("bad", ["empty", "outside", "traversal", "symlink", "hardlink", "directory", "fifo",
                                 "linked_parent_inside", "linked_parent_outside", "linked_candidate"])
def test_source_admission_rejects_unsafe_files(tmp_path, bad):
    inputs = record()
    source = candidate(tmp_path / "source")
    candidate_dir = source.parent
    outside = candidate(tmp_path / "outside")
    before = outside.read_bytes()
    if bad == "outside":
        source = outside
    elif bad == "traversal":
        source = candidate_dir / ".." / "outside" / "part.brep"
    elif bad.startswith("linked_parent"):
        directory = candidate_dir / "nested"
        directory.mkdir()
        (directory / "part.brep").write_bytes(PAYLOAD)
        (candidate_dir / "link").symlink_to(directory if bad.endswith("inside") else outside.parent)
        source = candidate_dir / "link" / "part.brep"
    elif bad == "linked_candidate":
        alias = tmp_path / "alias"
        alias.symlink_to(candidate_dir)
        candidate_dir, source = alias, alias / "part.brep"
    else:
        source.unlink()
        if bad == "empty":
            source.touch()
        elif bad == "symlink":
            source.symlink_to(outside)
        elif bad == "hardlink":
            os.link(outside, source)
        elif bad == "directory":
            source.mkdir()
        else:
            os.mkfifo(source)
    cache = GeometryCache(tmp_path / "cache")
    with pytest.raises(CacheInputError):
        cache.put(key_for(inputs), inputs, source, candidate_dir=candidate_dir)
    assert outside.read_bytes() == before and not cache.cache_dir.exists()


def test_cache_root_separation_and_symlink_lock_rejection(tmp_path):
    inputs = record()
    source = candidate(tmp_path / "source")
    for root in (source.parent, source.parent / "cache", tmp_path):
        with pytest.raises(CacheInputError, match="overlap"):
            GeometryCache(root).put(key_for(inputs), inputs, source, candidate_dir=source.parent)
    assert {p.name for p in source.parent.iterdir()} == {"part.brep"}
    (tmp_path / "alias").symlink_to(source.parent)
    with pytest.raises(CacheInputError):
        GeometryCache(tmp_path / "alias").put(key_for(inputs), inputs, source, candidate_dir=source.parent)
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    outside = tmp_path / "untouched"
    outside.write_bytes(b"outside")
    (cache_dir / ".lock").symlink_to(outside)
    with pytest.raises(OSError):
        GeometryCache(cache_dir).put(key_for(inputs), inputs, source, candidate_dir=source.parent)
    assert outside.read_bytes() == b"outside"


@pytest.mark.parametrize("existing", ["empty_directory", "candidate", "symlink", "hardlink"])
def test_restore_never_overwrites_existing_destination(stored, tmp_path, existing):
    cache, inputs, _, entry = stored
    target = tmp_path / "destination"
    if existing == "symlink":
        target.symlink_to(tmp_path / "other")
    else:
        target.mkdir()
        if existing == "candidate":
            (target / "part.brep").write_bytes(b"keep this candidate")
        elif existing == "hardlink":
            outside = tmp_path / "outside"
            outside.write_bytes(b"keep this candidate")
            os.link(outside, target / "part.brep")
    with pytest.raises(FileExistsError):
        cache.restore(entry.key, inputs, target)
    if existing in {"candidate", "hardlink"}:
        assert (target / "part.brep").read_bytes() == b"keep this candidate"
    assert entry.brep_path.read_bytes() == PAYLOAD


def test_restore_rejects_cache_overlap_and_traversal(stored, tmp_path):
    cache, inputs, _, entry = stored
    for path in (cache.cache_dir / "destination", tmp_path, tmp_path / "x" / ".." / "destination"):
        with pytest.raises(CacheInputError):
            cache.restore(entry.key, inputs, path)


def test_restore_revalidates_during_copy_and_retains_failure_diagnostics(stored, tmp_path, monkeypatch):
    cache, inputs, _, entry = stored
    real_copy = module._copy_and_hash

    def change_between_validation_and_copy(path, output=None):
        if output is not None:
            rewrite(path, b"X" * len(PAYLOAD))
        return real_copy(path, output)

    monkeypatch.setattr(module, "_copy_and_hash", change_between_validation_and_copy)
    with pytest.raises(CacheCorruption, match="copied payload") as error:
        cache.restore(entry.key, inputs, tmp_path / "failed")
    assert (tmp_path / "failed" / "part.brep").read_bytes() == b"X" * len(PAYLOAD)
    assert any(str(tmp_path / "failed") in note for note in error.value.__notes__)


def test_restore_io_failure_retains_partial_data_and_original_error(stored, tmp_path, monkeypatch):
    cache, inputs, _, entry = stored
    real_copy = module._copy_and_hash

    def fail_copy(path, output=None):
        if output is not None:
            output.write(b"partial")
            raise OSError("destination write fixture failure")
        return real_copy(path, output)

    monkeypatch.setattr(module, "_copy_and_hash", fail_copy)
    with pytest.raises(OSError, match="destination write fixture failure") as error:
        cache.restore(entry.key, inputs, tmp_path / "failed")
    assert (tmp_path / "failed" / "part.brep").read_bytes() == b"partial"
    assert any(str(tmp_path / "failed") in note for note in error.value.__notes__)
    assert entry.brep_path.read_bytes() == PAYLOAD


@pytest.mark.parametrize("operation", ["fsync", "rename"])
def test_failed_publication_is_not_a_partial_hit(tmp_path, monkeypatch, operation):
    inputs = record()
    source = candidate(tmp_path / "source")
    cache = GeometryCache(tmp_path / "cache")

    def fail(*args):
        raise OSError("publication fixture failure")

    monkeypatch.setattr(module.os, operation, fail)
    with pytest.raises(OSError, match="publication fixture failure"):
        cache.put(key_for(inputs), inputs, source, candidate_dir=source.parent)
    with pytest.raises(CacheMiss):
        cache.lookup(key_for(inputs), inputs)
    assert {p.name for p in cache.cache_dir.iterdir()} == {".lock"}
    assert source.read_bytes() == PAYLOAD


WRITER = """
import json, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from aiad.engine.cache import GeometryCache, CacheConflict
inputs = json.loads(sys.argv[4])
cache, source = GeometryCache(Path(sys.argv[2])), Path(sys.argv[3])
print('ready', flush=True)
sys.stdin.readline()
try:
    entry = cache.put(sys.argv[5], inputs, source, candidate_dir=source.parent)
    print(json.dumps({'result': 'ok', 'sha256': entry.sha256}), flush=True)
except CacheConflict:
    print(json.dumps({'result': 'conflict'}), flush=True)
"""


def writer(tmp_path, source, inputs, code=WRITER):
    return subprocess.Popen(
        [sys.executable, "-I", "-B", "-c", code, str(SOURCE_DIR), str(tmp_path / "cache"),
         str(source), encoded(inputs).decode(), key_for(inputs)],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, cwd=tmp_path,
    )


def ready(process, expected="ready"):
    assert select.select([process.stdout], [], [], 10)[0], "trusted writer did not reach its barrier"
    assert process.stdout.readline().strip() == expected


@pytest.mark.parametrize("different", [False, True])
def test_multiprocess_trusted_writers_serialize_publication(tmp_path, different):
    inputs = record()
    payloads = [b"opaque" * 400_000 + (str(index).encode() if different else b"") for index in range(4)]
    sources = [candidate(tmp_path / f"source-{index}", payload) for index, payload in enumerate(payloads)]
    processes = [writer(tmp_path, source, inputs) for source in sources]
    try:
        for process in processes:
            ready(process)
        for process in processes:
            process.stdin.write("go\n")
            process.stdin.flush()
        results = []
        for process in processes:
            stdout, stderr = process.communicate(timeout=20)
            assert process.returncode == 0, stderr
            results.append(json.loads(stdout))
        assert sum(result["result"] == "ok" for result in results) == (1 if different else 4)
        entry = GeometryCache(tmp_path / "cache").lookup(key_for(inputs), inputs)
        assert entry.brep_path.read_bytes() in payloads
        assert all(source.read_bytes() == payload for source, payload in zip(sources, payloads))
        assert {p.name for p in entry.brep_path.parent.parent.iterdir()} == {".lock", entry.key}
    finally:
        for process in processes:
            if process.poll() is None:
                process.kill()
            process.communicate(timeout=5)


def test_killed_writer_leaves_only_uncommitted_staging_and_releases_lock(tmp_path):
    inputs = record()
    source = candidate(tmp_path / "source")
    program = WRITER.replace("print('ready', flush=True)", """
import aiad.engine.cache as cache_module
real_rename = cache_module.os.rename
def stop_before_commit(*args):
    print('staged', flush=True)
    sys.stdin.readline()
    return real_rename(*args)
cache_module.os.rename = stop_before_commit
print('ready', flush=True)
""")
    process = writer(tmp_path, source, inputs, program)
    try:
        ready(process)
        process.stdin.write("go\n")
        process.stdin.flush()
        ready(process, "staged")
        process.kill()
        process.communicate(timeout=5)
        assert process.returncode < 0
    finally:
        if process.poll() is None:
            process.kill()
        process.communicate(timeout=5)
    cache = GeometryCache(tmp_path / "cache")
    stages = list(cache.cache_dir.glob(".stage-*"))
    assert len(stages) == 1 and (stages[0] / "part.brep").read_bytes() == PAYLOAD
    with pytest.raises(CacheMiss):
        cache.lookup(key_for(inputs), inputs)
    entry = cache.put(key_for(inputs), inputs, source, candidate_dir=source.parent)
    assert cache.lookup(entry.key, inputs) == entry
    assert stages[0].is_dir()  # No automatic janitor races with another writer's staging.


def test_kernel_free_import_and_opaque_operations_without_site_packages(tmp_path):
    script = """
import ast, json, sys, tempfile
from pathlib import Path
sys.path.insert(0, sys.argv[1])
class BlockNative:
    def find_spec(self, fullname, path=None, target=None):
        if fullname.partition('.')[0] in {'build123d', 'OCP'} or fullname in {
            'aiad.engine.identity', 'aiad.engine.jobs', 'aiad.engine.gate', 'aiad.engine.revisions'
        }:
            raise AssertionError('forbidden import: ' + fullname)
sys.meta_path.insert(0, BlockNative())
from aiad.engine import cache
for node in ast.walk(ast.parse(Path(cache.__file__).read_text())):
    if isinstance(node, (ast.Import, ast.ImportFrom)):
        names = [item.name for item in node.names] if isinstance(node, ast.Import) else [node.module]
        assert all(name.partition('.')[0] in sys.stdlib_module_names for name in names)
assert not any(name in cache.__dict__ for name in ['BuildResult', 'Check', 'checkout', 'verified', 'receipt'])
inputs = json.loads(sys.argv[2])
with tempfile.TemporaryDirectory() as temporary:
    root = Path(temporary)
    source = root / 'source'
    source.mkdir()
    (source / 'part.brep').write_bytes(b'opaque')
    store = cache.GeometryCache(root / 'cache')
    store.put(sys.argv[3], inputs, source / 'part.brep', candidate_dir=source)
    assert store.lookup(sys.argv[3], inputs).byte_length == 6
    assert store.restore(sys.argv[3], inputs, root / 'restored').read_bytes() == b'opaque'
assert not any(name.partition('.')[0] in {'build123d', 'OCP'} for name in sys.modules)
"""
    inputs = record()
    result = subprocess.run(
        [sys.executable, "-I", "-S", "-B", "-c", script, str(SOURCE_DIR), encoded(inputs).decode(), key_for(inputs)],
        cwd=tmp_path, capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == 0, result.stderr


def test_unsupported_locking_fails_explicitly(tmp_path, monkeypatch):
    monkeypatch.setattr(module, "fcntl", None)
    with pytest.raises(CacheUnsupported, match="POSIX flock"):
        GeometryCache(tmp_path / "cache")
    assert not (tmp_path / "cache").exists()


def test_identity_compatibility(tmp_path):
    from aiad.engine import identity as actual
    cache = GeometryCache(tmp_path / "cache")
    payload = candidate(tmp_path / "source")
    keys = set()
    for number in (True, 1, 1.0, 0.0, -0.0):
        inputs = record()
        del inputs["schema"]
        inputs["applied_params"]["number"] = number
        key, generated = actual.cache_identity(**inputs)
        keys.add(key)
        entry = cache.put(key, generated, payload, candidate_dir=payload.parent)
        assert cache.lookup(key, generated) == entry
        assert cache.restore(key, generated, tmp_path / f"restored-{len(keys)}").read_bytes() == PAYLOAD
        manifest = json.loads((entry.brep_path.parent / "manifest.json").read_bytes())
        assert encoded(manifest["input_record"]) == encoded(generated)
    assert len(keys) == 5


def test_native_box_brep_store_restore_and_fresh_reload_probe(tmp_path):
    """Real native interoperability, distinct from storage's opaque-byte proof."""
    source_dir = tmp_path / "native-source"
    source_dir.mkdir(mode=0o700)
    source = source_dir / "part.brep"
    producer = """
import sys
from build123d import Box, export_brep
assert export_brep(Box(2, 3, 4), sys.argv[1])
"""
    # Trusted fixture has exited with no descendants before storage reads its output.
    subprocess.run([sys.executable, "-I", "-B", "-c", producer, str(source)],
                   check=True, capture_output=True, text=True, timeout=120, cwd=tmp_path)
    inputs = record()
    inputs["applied_params"] = {"width": 2, "depth": 3, "height": 4}
    inputs["asset_sha256"] = {}
    cache = GeometryCache(tmp_path / "cache")
    entry = cache.put(key_for(inputs), inputs, source, candidate_dir=source_dir)
    restored = cache.restore(entry.key, inputs, tmp_path / "native-restored")
    inspector = """
import json, sys
from build123d import import_brep
from OCP.BRepCheck import BRepCheck_Analyzer
shape = import_brep(sys.argv[1])
size = shape.bounding_box().size
print(json.dumps({'valid': BRepCheck_Analyzer(shape.wrapped).IsValid(),
                  'solids': len(shape.solids()), 'volume': shape.volume,
                  'bbox': [size.X, size.Y, size.Z]}))
"""
    result = subprocess.run([sys.executable, "-I", "-B", "-c", inspector, str(restored)],
                            check=True, capture_output=True, text=True, timeout=120, cwd=tmp_path)
    observed = json.loads(result.stdout)
    assert observed["valid"] is True and observed["solids"] == 1
    assert observed["volume"] == pytest.approx(24.0)
    assert observed["bbox"] == pytest.approx([2.0, 3.0, 4.0])
    assert source.read_bytes() == restored.read_bytes() == entry.brep_path.read_bytes()
    assert restored.stat().st_nlink == 1 and restored.stat().st_ino != entry.brep_path.stat().st_ino
