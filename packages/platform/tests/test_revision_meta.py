from __future__ import annotations

import asyncio
import gzip
import json

import pytest

from aad_platform.daemon.handlers import Handlers
from aad_platform.daemon.projects import create_project
from aad_platform.ops import load_context
from aad_platform.revision_meta import InvalidRevisionMetadata, read_revision_meta, write_revision_meta


def _project_with_revision(tmp_path, monkeypatch, *, meta=None):
    monkeypatch.setenv("HOME", str(tmp_path))
    aad_home = tmp_path / ".aad"
    aad_home.mkdir()
    monkeypatch.setenv("AAD_HOME", str(aad_home))
    project = create_project("p")
    _, job_dir = project.new_job_dir()
    write_revision_meta(job_dir, meta or {"status": "draft", "metrics": {"volume": 24}})
    project.commit_revision(job_dir, "result = None", None)
    return project


def _assert_invalid_metadata(result):
    envelope = result["structured"]
    assert envelope["ok"] is False
    assert envelope["status"] == "failed"
    assert envelope["failure"]["code"] == "INVALID_REVISION_METADATA"
    assert envelope["failure"]["phase"] == "input"
    assert "meta.json.gz" in envelope["failure"]["message"]
    assert "metrics" not in envelope
    assert "measurement" not in envelope
    assert "entities" not in envelope


def test_revision_metadata_is_gzipped_and_round_trips(tmp_path):
    value = {"features": [{"name": "base", "source": "base.py"}], "status": "draft"}

    path = write_revision_meta(tmp_path, value)

    assert path.name == "meta.json.gz"
    assert not (tmp_path / "meta.json").exists()
    with gzip.open(path, "rt", encoding="utf-8") as stream:
        assert json.load(stream) == value
    assert read_revision_meta(tmp_path) == value


def test_revision_metadata_preserves_validator_receipt_fields(tmp_path):
    value = {
        "status": "verified",
        "metrics": {"volume": 125},
        "verification_receipt": {
            "rev": 7,
            "brep_sha256": "brep-hash",
            "project_settings": {"units": "mm", "process": {"material": "steel"}},
            "requirement_results": [{"id": "size", "status": "pass"}],
            "proven_error_requirement_ids": ["size"],
            "revision_evidence_sha256": "evidence-hash",
        },
    }

    write_revision_meta(tmp_path, value)

    assert read_revision_meta(tmp_path) == value


def test_revision_metadata_reads_legacy_json(tmp_path):
    value = {"features": [], "status": "draft"}
    (tmp_path / "meta.json").write_text(json.dumps(value), encoding="utf-8")

    assert read_revision_meta(tmp_path) == value


def test_revision_metadata_prefers_compressed_form_when_both_exist(tmp_path):
    legacy = {"status": "draft"}
    current = {"status": "verified"}
    (tmp_path / "meta.json").write_text(json.dumps(legacy), encoding="utf-8")
    write_revision_meta(tmp_path, current)

    assert read_revision_meta(tmp_path) == current


@pytest.mark.parametrize(
    "contents",
    [b"not gzip", gzip.compress(b"not json"), gzip.compress(b'{"status":"draft"}')[:-4]],
)
def test_corrupt_compressed_revision_metadata_has_one_specific_error(tmp_path, contents):
    (tmp_path / "meta.json.gz").write_bytes(contents)

    with pytest.raises(InvalidRevisionMetadata, match="meta.json.gz") as error:
        read_revision_meta(tmp_path)

    assert error.value.code == "INVALID_REVISION_METADATA"
    assert error.value.phase == "input"


def test_corrupt_legacy_revision_metadata_has_one_specific_error(tmp_path):
    (tmp_path / "meta.json").write_text("{", encoding="utf-8")

    with pytest.raises(InvalidRevisionMetadata, match="meta.json"):
        read_revision_meta(tmp_path)


def test_corrupt_compressed_file_does_not_fall_back_to_legacy_metadata(tmp_path):
    (tmp_path / "meta.json").write_text(json.dumps({"status": "verified"}), encoding="utf-8")
    (tmp_path / "meta.json.gz").write_bytes(b"not gzip")

    with pytest.raises(InvalidRevisionMetadata, match="meta.json.gz"):
        read_revision_meta(tmp_path)


@pytest.mark.parametrize(
    ("method", "extra"),
    [
        ("aad_inspect", {"what": "summary"}),
        ("aad_show", {}),
        ("aad_history", {"action": "list"}),
        ("aad_history", {"action": "diff", "against": 1}),
        ("aad_history", {"action": "branch", "name": "fork"}),
        ("aad_export", {"formats": ["step"], "allow_unverified": True}),
        ("aad_drawing", {}),
    ],
)
def test_corrupt_metadata_fails_direct_revision_handlers_without_partial_success(
    tmp_path, monkeypatch, method, extra
):
    project = _project_with_revision(tmp_path, monkeypatch)
    (project.rev_dir(1) / "meta.json.gz").write_bytes(b"not gzip")

    result = asyncio.run(
        Handlers().call(method, {"project": project.name, "rev": 1, **extra})
    )

    _assert_invalid_metadata(result)
    assert not (project.path.parent / "fork").exists()


@pytest.mark.parametrize(
    ("method", "extra"),
    [
        ("aad_query", {"selector": "main"}),
        ("aad_measure", {"kind": "volume"}),
        ("aad_check", {"suites": ["hard"]}),
        ("aad_render", {}),
        ("aad_pick_info", {"kind": "face", "id": 0}),
    ],
)
def test_truncated_metadata_fails_operation_handlers_with_the_same_code(
    tmp_path, monkeypatch, method, extra
):
    project = _project_with_revision(tmp_path, monkeypatch)
    path = project.rev_dir(1) / "meta.json.gz"
    path.write_bytes(gzip.compress(b'{"status":"draft"}')[:-4])

    result = asyncio.run(
        Handlers().call(method, {"project": project.name, "rev": 1, **extra})
    )

    _assert_invalid_metadata(result)


@pytest.mark.parametrize("method", ["aad_build", "aad_set_param"])
def test_corrupt_metadata_fails_param_build_with_the_same_code(tmp_path, monkeypatch, method):
    project = _project_with_revision(tmp_path, monkeypatch)
    (project.rev_dir(1) / "meta.json.gz").write_bytes(b"not gzip")

    params = (
        {"project": project.name, "base_rev": 1, "params": {"width": 2}}
        if method == "aad_build"
        else {"project": project.name, "base_rev": 1, "name": "width", "value": 2}
    )
    result = asyncio.run(Handlers().call(method, params))

    _assert_invalid_metadata(result)
    assert project.head_rev == 1


def test_corrupt_checkout_fails_before_changing_the_head(tmp_path, monkeypatch):
    project = _project_with_revision(tmp_path, monkeypatch)
    _, job_dir = project.new_job_dir()
    write_revision_meta(job_dir, {"status": "draft"})
    project.commit_revision(job_dir, "result = None", None)
    project.checkout(1)
    (project.rev_dir(2) / "meta.json.gz").write_bytes(b"not gzip")

    result = asyncio.run(
        Handlers().call(
            "aad_history", {"project": project.name, "action": "checkout", "rev": 2}
        )
    )

    _assert_invalid_metadata(result)
    assert project.head_rev == 1


def test_operation_loads_compressed_revision_metadata(tmp_path):
    rev_dir = tmp_path / ".aad" / "revs" / "000001"
    rev_dir.mkdir(parents=True)
    out_dir = tmp_path / "job"
    out_dir.mkdir()
    write_revision_meta(
        rev_dir,
        {"features": [], "params": {"width": 25}, "metrics": {"volume": 125}, "meta": {"material": "pla"}},
    )

    context = load_context({"project_dir": str(tmp_path), "rev": 1, "out_dir": str(out_dir)})

    assert context.params == {"width": 25}
    assert context.metrics == {"volume": 125}
    assert context.material == "pla"
