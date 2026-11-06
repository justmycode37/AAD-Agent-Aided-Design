from __future__ import annotations

import hashlib
import json
import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from aad_platform import ops
from aad_platform.daemon import ops_client


class _Batch:
    size = 7

    def iter_bytes(self):
        yield b"packet!"


def _fixture_revision(tmp_path):
    project = tmp_path / "review-project"
    revision = project / ".aad" / "revs" / "000003"
    revision.mkdir(parents=True)
    (revision / "request.json").write_text(json.dumps({
        "original": "Design a hollow 20 mm enclosure with two cable exits.",
        "assumptions": ["ASSUMPTION_SENTINEL must stay out of the review packet"],
        "open_questions": ["What is the enclosure wall thickness?"],
        "not_checked": [{"item": "cable routing", "reason": "No route is defined."}],
    }))
    (revision / "meta.json").write_text(json.dumps({
        "status": "draft",
        "source": "MODEL_SOURCE_SENTINEL",
        "reasoning": "REASONING_SENTINEL",
        "checks": [
            {"id": "hard.valid", "suite": "hard", "level": "error", "status": "pass", "message": "Valid."},
            {"id": "spec.wall_skip", "suite": "spec", "level": "warn", "status": "skip",
             "message": "No wall thickness requirement was available.", "assumptions": "CHECK_ASSUMPTION_SENTINEL"},
            {"id": "process.access_skip", "suite": "process", "level": "warn", "status": "skip",
             "message": "Access was not evaluated."},
        ],
    }))
    return project


def test_packet_contains_request_checks_unresolved_images_and_hashes(tmp_path, monkeypatch):
    project = _fixture_revision(tmp_path)
    case = {
        "id": "overview",
        "purpose": "Orthographic overview.",
        "images": ["review-overview-iso.png"],
        "render": {"request": {"views": ["iso"]}, "scene": {}},
    }
    plan = {
        "renders": [case],
        "parts": [{"id": "part-001", "name": "enclosure", "size": [20, 18, 10], "units": "mm"}],
        "coverage": {"component_count": 1, "included_component_ids": ["part-001"], "omitted_component_ids": []},
    }
    monkeypatch.setattr("aad_render.review_packet.prepare_review_packet", lambda _ctx: plan)
    monkeypatch.setattr("aad_render.render_wire.pack_render_batch", lambda _cases: _Batch())
    ctx = SimpleNamespace(project_dir=project, rev=3, kind="part", units="mm")
    out = tmp_path / "operation"
    out.mkdir()

    prepared_result = ops.op_render_prepare(ctx, {"review_packet": True}, out)
    prepared = prepared_result["prepared_review_packet"]
    packet_dir = out / "review-packet"
    request_path = packet_dir / "request.txt"
    assert request_path.read_text() == "Design a hollow 20 mm enclosure with two cable exits."
    (packet_dir / "review-overview-iso.png").write_bytes(b"\x89PNG\r\n\x1a\nimage")

    result = ops_client._finalize_review_packet(
        out,
        prepared,
        {"cases": [{"id": "overview", "images": ["review-overview-iso.png"]}],
         "image_count": 1, "image_bytes": len(b"\x89PNG\r\n\x1a\nimage")},
    )
    manifest_path = packet_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    text = request_path.read_text() + manifest_path.read_text()

    assert result["path"] == str(packet_dir)
    assert manifest["schema"] == "aad.review-packet.v1"
    assert manifest["parts"][0]["size"] == [20, 18, 10]
    assert [check["id"] for check in manifest["checks"]] == [
        "hard.valid", "spec.wall_skip", "process.access_skip",
    ]
    assert [check["status"] for check in manifest["checks"]].count("skip") == 2
    assert {entry["type"] for entry in manifest["unresolved"]} >= {"open_question", "not_checked", "check"}
    assert manifest["files"]["request.txt"]["sha256"] == hashlib.sha256(request_path.read_bytes()).hexdigest()
    assert manifest["files"]["review-overview-iso.png"]["sha256"] == hashlib.sha256((packet_dir / "review-overview-iso.png").read_bytes()).hexdigest()
    assert all(sentinel not in text for sentinel in (
        "MODEL_SOURCE_SENTINEL", "REASONING_SENTINEL", "ASSUMPTION_SENTINEL", "CHECK_ASSUMPTION_SENTINEL",
    ))


def test_packet_rejects_an_image_over_its_size_limit(tmp_path):
    from aad_render.render_limits import MAX_REVIEW_PACKET_IMAGE_BYTES

    packet_dir = tmp_path / "out" / "review-packet"
    packet_dir.mkdir(parents=True)
    (packet_dir / "request.txt").write_bytes(b"request")
    (packet_dir / "review-overview-iso.png").write_bytes(
        b"\x89PNG\r\n\x1a\n" + b"x" * MAX_REVIEW_PACKET_IMAGE_BYTES
    )
    prepared = {
        "case_ids": ["overview"],
        "manifest": {
            "views": [{"id": "overview", "images": ["review-overview-iso.png"]}],
            "files": {},
        },
    }
    rendered = {"cases": [{"id": "overview", "images": ["review-overview-iso.png"]}],
                "image_count": 1, "image_bytes": MAX_REVIEW_PACKET_IMAGE_BYTES + 8}

    with pytest.raises(ValueError, match="size limit"):
        ops_client._finalize_review_packet(tmp_path / "out", prepared, rendered)


def test_packet_rejects_aggregate_size_over_its_limit(monkeypatch, tmp_path):
    from aad_render.render_limits import MAX_REVIEW_PACKET_IMAGE_BYTES, MAX_REVIEW_PACKET_IMAGES

    image = b"\x89PNG\r\n\x1a\n" + b"x" * (MAX_REVIEW_PACKET_IMAGE_BYTES - 8)
    cases = []
    views = []
    for index in range(MAX_REVIEW_PACKET_IMAGES // 2):
        case_id = f"case-{index:02d}"
        names = [f"review-{case_id}-{view}.png" for view in ("iso", "front")]
        cases.append({"id": case_id, "images": names})
        views.append({"id": case_id, "images": names})
    prepared = {"case_ids": [case["id"] for case in cases], "manifest": {"views": views, "files": {}}}
    rendered = {
        "cases": cases,
        "image_count": MAX_REVIEW_PACKET_IMAGES,
        "image_bytes": len(image) * MAX_REVIEW_PACKET_IMAGES,
    }

    def read_file(path, _maximum):
        return b"request" if path.name == "request.txt" else image

    monkeypatch.setattr(ops_client, "_read_review_packet_file", read_file)
    with pytest.raises(ValueError, match="aggregate byte limit"):
        ops_client._finalize_review_packet(tmp_path, prepared, rendered)


@pytest.mark.skipif(
    sys.platform != "darwin" and not sys.platform.startswith("linux"),
    reason="OS sandbox render path",
)
def test_single_part_packet_runs_through_sandbox_and_clean_mesh_renderer(tmp_path):
    from build123d import Box, export_brep

    project = _fixture_revision(tmp_path)
    revision = project / ".aad" / "revs" / "000003"
    assert export_brep(Box(8, 6, 4), str(revision / "part.brep"))
    out = tmp_path / "render-operation"

    result = ops_client._run_sandboxed({
        "op": "render",
        "project": project.name,
        "project_dir": str(project),
        "rev": 3,
        "kind": "part",
        "units": "mm",
        "args": {"review_packet": True},
        "out_dir": str(out),
        "read_paths": [str(project / ".aad" / "revs")],
    }, 120.0)

    assert result["ok"], result
    assert "images" not in result
    packet_dir = Path(result["review_packet"]["path"])
    manifest = json.loads((packet_dir / "manifest.json").read_text())
    image_names = [name for name in manifest["files"] if name.endswith(".png")]
    assert len(image_names) == result["review_packet"]["image_count"] == 12
    assert manifest["checks_available"] is True
    assert sum(check["status"] == "skip" for check in manifest["checks"]) == 2
    assert (packet_dir / "request.txt").read_text() == "Design a hollow 20 mm enclosure with two cable exits."
    assert set(path.name for path in packet_dir.iterdir()) == {"request.txt", "manifest.json", *image_names}
    assert "MODEL_SOURCE_SENTINEL" not in (packet_dir / "manifest.json").read_text()


def test_aad_render_returns_packet_handle_without_inline_images(monkeypatch):
    from aad_platform.daemon import handlers

    class Project:
        name = "rendered-part"

    instance = handlers.Handlers()
    monkeypatch.setattr(handlers, "open_project", lambda *_args: Project())
    monkeypatch.setattr(instance, "_rev_or_failure", lambda *_args: (3, None))
    monkeypatch.setattr(handlers, "_rev_meta", lambda *_args: {"status": "draft"})

    async def run(_project, _rev, _op, args):
        assert args == {"review_packet": True}
        return ({"ok": True, "review_packet": {
            "path": "/tmp/review-packet", "manifest": "/tmp/review-packet/manifest.json",
            "image_count": 9, "bytes": 8192,
        }}, Path("/tmp/operation"))

    monkeypatch.setattr(instance, "_op", run)
    result = asyncio.run(instance.aad_render({"project": "rendered-part", "review_packet": True}))

    assert "images" not in result
    assert result["structured"]["status"] == "draft"
    assert result["structured"]["review_packet"]["path"] == "/tmp/review-packet"


def test_unresolved_items_leave_out_the_author_reasons():
    unresolved = ops._review_unresolved(
        {"not_checked": [{"item": "bearing fit", "reason": "I assumed the bearing spec"}]}, [], True, True
    )
    assert unresolved == [{"type": "not_checked", "item": "bearing fit"}]


def test_manifest_reports_a_failed_revision_status(monkeypatch, tmp_path):
    project = tmp_path / "p"
    revision = project / ".aad" / "revs" / "000003"
    revision.mkdir(parents=True)
    (revision / "meta.json").write_text(json.dumps({"status": "failed", "checks": []}))
    plan = {"renders": [], "parts": [], "coverage": {}}
    monkeypatch.setattr("aad_render.review_packet.prepare_review_packet", lambda _ctx: plan)
    monkeypatch.setattr("aad_render.render_wire.pack_render_batch", lambda _cases: _Batch())
    out = tmp_path / "operation"
    out.mkdir()
    ctx = SimpleNamespace(project_dir=project, rev=3, kind="part", units="mm")
    manifest = ops.op_render_prepare(ctx, {"review_packet": True}, out)["prepared_review_packet"]["manifest"]
    assert manifest["status"] == "failed"
    assert manifest["unresolved"][0] == {"type": "status", "item": "Revision status is failed, not verified."}
