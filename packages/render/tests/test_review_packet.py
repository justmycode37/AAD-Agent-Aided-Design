from __future__ import annotations

from types import SimpleNamespace

from build123d import Box, Compound, Pos
import pytest

from aad_render import review_packet
from aad_render.render_limits import (
    MAX_REVIEW_PACKET_CASES,
    MAX_REVIEW_PACKET_IMAGE_BYTES,
    MAX_REVIEW_PACKET_IMAGES,
    MAX_REVIEW_PACKET_PARTS,
)
from aad_render.render_wire import decode_render_payload, iter_render_batch, pack_render_batch


def _fake_prepare(monkeypatch):
    prepared = []

    def prepare(ctx, request):
        prepared.append((ctx.shape, request))
        return {"render": {"request": request, "scene": {"faces": [], "lines": []}}, "inspection": {}}

    monkeypatch.setattr(review_packet, "prepare_render", prepare)
    return prepared


def test_review_packet_plan_covers_multicomponent_assembly(monkeypatch, tmp_path):
    prepared = _fake_prepare(monkeypatch)
    first = Box(10, 4, 2)
    first.label = "housing"
    second = Box(2, 6, 8).moved(Pos(12, 0, 0))
    second.label = "motor"
    shape = Compound(label="assembly", children=[first, second])
    ctx = SimpleNamespace(
        shape=shape,
        kind="assembly",
        units="mm",
        project_dir=tmp_path / "assembly",
        meta={"assembly": {"components": ["housing", "motor"]}},
    )

    plan = review_packet.prepare_review_packet(ctx)

    assert [part["name"] for part in plan["parts"]] == ["housing", "motor"]
    assert plan["parts"][0]["size"] == [10.0, 4.0, 2.0]
    assert plan["parts"][1]["size"] == [2.0, 6.0, 8.0]
    assert [case["id"] for case in plan["renders"][:4]] == [
        "overview",
        "section-assembly-xy",
        "section-assembly-xz",
        "section-assembly-yz",
    ]
    part_sections = [case for case in plan["renders"] if case["id"].startswith("section-part-")]
    isolated = [case for case in plan["renders"] if case["id"].startswith("isolated-part-")]
    assert len(part_sections) == len(isolated) == 2
    assert all(case["part_id"] in {"part-001", "part-002"} for case in part_sections + isolated)
    isolated_shapes = [rendered_shape for rendered_shape, _request in prepared if rendered_shape is not shape]
    assert sorted(
        tuple(float(getattr(rendered_shape.bounding_box().size, axis)) for axis in ("X", "Y", "Z"))
        for rendered_shape in isolated_shapes
    ) == [(2.0, 6.0, 8.0), (10.0, 4.0, 2.0)]
    assert len(plan["renders"][0]["images"]) == 7
    assert sum(len(case["images"]) for case in plan["renders"]) == 14


def test_review_packet_plan_supports_one_part(monkeypatch, tmp_path):
    prepared = _fake_prepare(monkeypatch)
    shape = Box(12, 8, 4)
    ctx = SimpleNamespace(
        shape=shape,
        kind="part",
        units="mm",
        project_dir=tmp_path / "single-part",
        meta={"name": "bracket"},
    )

    plan = review_packet.prepare_review_packet(ctx)

    assert len(plan["parts"]) == 1
    assert plan["parts"][0]["name"] == "bracket"
    assert plan["parts"][0]["size"] == [12.0, 8.0, 4.0]
    assert len([case for case in plan["renders"] if case["id"].startswith("isolated-part-")]) == 1
    assert len([case for case in plan["renders"] if case["id"].startswith("section-part-")]) == 1
    assert any(rendered_shape is shape for rendered_shape, _request in prepared)
    assert plan["coverage"]["omitted_component_ids"] == []


def test_review_packet_plan_caps_major_part_cases_and_images(monkeypatch, tmp_path):
    _fake_prepare(monkeypatch)
    children = [Box(index + 1, 2, 3) for index in range(10)]
    names = [f"part-{index:02d}" for index in range(10)]
    shape = Compound(label="large-assembly", children=children)
    ctx = SimpleNamespace(
        shape=shape,
        kind="assembly",
        units="mm",
        project_dir=tmp_path / "large-assembly",
        meta={"assembly": {"components": names}},
    )

    plan = review_packet.prepare_review_packet(ctx)
    image_count = sum(len(case["images"]) for case in plan["renders"])

    assert len(plan["renders"]) == MAX_REVIEW_PACKET_CASES
    assert len(plan["coverage"]["included_component_ids"]) == MAX_REVIEW_PACKET_PARTS
    assert len(plan["coverage"]["omitted_component_ids"]) == 2
    assert image_count <= MAX_REVIEW_PACKET_IMAGES
    assert MAX_REVIEW_PACKET_IMAGE_BYTES == 2 * 1024 * 1024


def test_review_packet_wire_batch_is_bounded_and_versioned():
    batch = pack_render_batch([("overview", {}), ("section-center", {})])

    cases = list(iter_render_batch(batch.to_bytes()))

    assert [case_id for case_id, _payload in cases] == ["overview", "section-center"]
    assert all(decode_render_payload(payload)[0] == {"render": {}} for _case_id, payload in cases)
    with pytest.raises(ValueError, match="render cases"):
        pack_render_batch([(f"case-{index}", {}) for index in range(MAX_REVIEW_PACKET_CASES + 1)])
