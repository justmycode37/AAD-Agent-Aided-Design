from __future__ import annotations

import gzip
import json

from aad_platform.ops import load_context
from aad_platform.revision_meta import read_revision_meta, write_revision_meta


def test_revision_metadata_is_gzipped_and_round_trips(tmp_path):
    value = {"features": [{"name": "base", "source": "base.py"}], "status": "draft"}

    path = write_revision_meta(tmp_path, value)

    assert path.name == "meta.json.gz"
    assert not (tmp_path / "meta.json").exists()
    with gzip.open(path, "rt", encoding="utf-8") as stream:
        assert json.load(stream) == value
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
