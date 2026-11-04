from __future__ import annotations

import gzip
import hashlib
import json
import math
from pathlib import Path

import pytest

from aiad_drafting import scene2d
from aiad_drafting.model2d import Sheet2D, latest_sheet


def _sample_sheet() -> Sheet2D:
    sheet = Sheet2D("scene_panel", units="mm")
    sheet.rectangle("plate", 20, 10, origin=(-10, -5), layer="cut")
    sheet.circle("hole", (0, 0), 1, layer="cut")
    curve = sheet.bezier("crown", [(30, 0), (30, 12), (42, 12), (42, 0)], layer="score")
    curve.metadata.update({
        "source_name": "upper_crown",
        "source_id": "path-7",
        "feature": "profile",
        "source_element_attributes": {"onclick": "alert(1)", "href": "https://invalid.example/"},
    })
    sheet.ellipse("rotated_ellipse", (50, 0), 3, 7, rotation=30, layer="engrave")
    label = sheet.text("far_label", "DO NOT INCLUDE TEXT EXTENTS", (1000, 2000), 100, layer="engrave")
    label.metadata["rotation_deg"] = 15
    return sheet


def _ctx(sheet, **extra):
    values = {"sheet": sheet, "units": sheet.units, "rev": 4, "project_dir": Path("/tmp/demo")}
    values.update(extra)
    return values


def _decompress(path: Path):
    return json.loads(gzip.decompress(path.read_bytes()).decode("utf-8"))


def test_scene_manifest_payload_bbox_determinism_hash_and_provenance(tmp_path):
    sheet = _sample_sheet()
    serialized = sheet.to_dict()
    latest = latest_sheet()
    ctx = _ctx(
        sheet,
        metrics={"bbox": [-999, -999, -999, 999, 999, 999]},
        status="verified",
        verified=True,
    )
    first_dir = tmp_path / "first"
    second_dir = tmp_path / "second"

    manifest = scene2d.make_scene(ctx, first_dir)
    second = scene2d.make_scene({**ctx, "sheet": serialized}, second_dir)

    assert manifest == second
    assert set(path.name for path in first_dir.iterdir()) == {"scene.bin.gz", "scene.json"}
    compressed = (first_dir / "scene.bin.gz").read_bytes()
    assert compressed == (second_dir / "scene.bin.gz").read_bytes()
    assert compressed[4:8] == b"\0\0\0\0"
    assert hashlib.sha256(compressed).hexdigest() == manifest["scene_id"]
    assert len(compressed) == manifest["total_bytes"]
    assert json.loads((first_dir / "scene.json").read_text()) == manifest
    payload = _decompress(first_dir / "scene.bin.gz")
    assert set(payload) == {"svg", "sheet", "layers", "entities"}
    assert payload["layers"] == ["cut", "score", "engrave"]
    assert payload["sheet"]["units"] == "mm"
    assert payload["sheet"]["w"] > 60  # includes the exact text insertion point in the SVG viewport
    assert payload["sheet"]["h"] > 20
    assert "far_label" in payload["svg"]
    assert manifest["project"] == "demo"
    assert manifest["rev"] == 4
    assert manifest["kind"] == "2d"
    assert manifest["format"] == "svg-1"
    assert manifest["encoding"] == "gzip"
    assert manifest["status"] == "draft"
    assert "checks_summary" not in manifest and "params" not in manifest
    assert manifest["bbox"] == pytest.approx([
        -10, -math.sqrt(39), 0,
        50 + math.sqrt(19), 9, 0,
    ])
    assert manifest["bbox"] != ctx["metrics"]["bbox"]

    assert [part["name"] for part in manifest["parts"]] == [
        "plate", "hole", "crown", "rotated_ellipse", "far_label"
    ]
    assert manifest["parts"][2] == {"path": "crown", "name": "crown", "feature": "profile"}
    crown = next(entity for entity in payload["entities"] if entity["name"] == "crown")
    assert crown["kind"] == "bezier"
    assert crown["layer"] == "score"
    assert crown["point_names"] == ["crown.p0", "crown.p1", "crown.p2", "crown.p3"]
    assert crown["points"] == [[30.0, 0.0], [30.0, 12.0], [42.0, 12.0], [42.0, 0.0]]
    assert crown["provenance"] == {
        "source_name": "upper_crown", "source_id": "path-7", "feature": "profile"
    }
    assert "source_element_attributes" not in crown.get("metadata", {})
    assert "alert(1)" not in json.dumps(payload) and "https://invalid.example/" not in json.dumps(payload)
    json.dumps(payload, ensure_ascii=False, allow_nan=False)
    assert sheet.to_dict() == serialized
    assert latest_sheet() is latest


def test_scene_status_is_always_draft_and_context_is_structural(tmp_path):
    sheet = _sample_sheet()
    ctx = {
        "sheet": sheet.to_dict(),
        "units": "mm",
        "project": "explicit-project",
        "rev": 0,
        "status": "verified",
        "checks": [{"status": "pass"}],
        "metrics": {"bbox": [1, 2, 3]},
    }
    manifest = scene2d.make_scene(ctx, tmp_path / "scene")
    assert manifest["project"] == "explicit-project"
    assert manifest["rev"] == 0
    assert manifest["status"] == "draft"


@pytest.mark.parametrize("rev", [True, -1, 1.5, "2", None])
def test_scene_rejects_non_integer_or_negative_revision(tmp_path, rev):
    with pytest.raises(ValueError, match="nonnegative integer"):
        scene2d.make_scene(_ctx(_sample_sheet(), rev=rev), tmp_path / "scene")
    assert not (tmp_path / "scene").exists()


def test_scene_requires_project_and_confines_output_to_job_dir(tmp_path):
    sheet = _sample_sheet()
    missing_project = {"sheet": sheet.to_dict(), "units": "mm", "rev": 1}
    with pytest.raises(ValueError, match="ctx.project or ctx.project_dir.name"):
        scene2d.make_scene(missing_project, tmp_path / "missing-project")
    assert not (tmp_path / "missing-project").exists()

    job = tmp_path / "job"
    job.mkdir()
    outside = tmp_path / "outside" / "scene"
    with pytest.raises(ValueError, match="must be under ctx.job_dir"):
        scene2d.make_scene(_ctx(sheet, job_dir=job), outside)
    assert not outside.parent.exists()


def test_scene_pair_atomic_failure_restores_existing_artifacts(tmp_path, monkeypatch):
    sheet = _sample_sheet()
    out = tmp_path / "scene"
    out.mkdir()
    old_bin = b"previous compressed scene"
    old_json = b"previous manifest"
    (out / "scene.bin.gz").write_bytes(old_bin)
    (out / "scene.json").write_bytes(old_json)
    original_replace = scene2d.os.replace
    failed = False

    def fail_second_publish(source, destination):
        nonlocal failed
        source_path, target_path = Path(source), Path(destination)
        if target_path == out / "scene.json" and source_path.suffix == ".tmp" and not failed:
            failed = True
            raise OSError("simulated second scene artifact failure")
        return original_replace(source, destination)

    monkeypatch.setattr(scene2d.os, "replace", fail_second_publish)
    with pytest.raises(OSError, match="simulated second"):
        scene2d.make_scene(_ctx(sheet), out)

    assert (out / "scene.bin.gz").read_bytes() == old_bin
    assert (out / "scene.json").read_bytes() == old_json
    assert sorted(path.name for path in out.iterdir()) == ["scene.bin.gz", "scene.json"]


def test_scene_invalid_units_and_text_only_geometry_bbox(tmp_path):
    sheet = Sheet2D("annotations", units="in")
    sheet.text("label", "text only", (12, -4), 2)
    ctx = {"sheet": sheet.to_dict(), "units": "mm", "project": "annotations", "rev": 1}
    with pytest.raises(ValueError, match="do not match"):
        scene2d.make_scene(ctx, tmp_path / "wrong-units")
    assert not (tmp_path / "wrong-units").exists()

    ctx["units"] = "in"
    manifest = scene2d.make_scene(ctx, tmp_path / "text-only")
    assert manifest["bbox"] == [0.0, 0.0, 0.0, 0.0, 0.0, 0.0]
    payload = _decompress(tmp_path / "text-only" / "scene.bin.gz")
    assert payload["sheet"] == {"w": 1.0, "h": 1.0, "units": "in"}
    assert payload["entities"][0]["text"] == "text only"
