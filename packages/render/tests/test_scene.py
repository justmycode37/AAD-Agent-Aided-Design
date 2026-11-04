from __future__ import annotations

import gzip
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

from build123d import Box, Cylinder, Compound, Location

from aiad_render import make_scene


def _entity(index: int) -> SimpleNamespace:
    return SimpleNamespace(index=index)


def _context(shape, *, features=None, project_dir=None, rev=1, **extra):
    values = {
        "project_dir": Path(project_dir or "test-project"),
        "rev": rev,
        "kind": "part",
        "units": "mm",
        "shape": shape,
        "features": features or [],
        "params": {},
        "metrics": {},
        "spec": {},
        "material": None,
        "process": None,
        "job_dir": Path("."),
    }
    values.update(extra)
    return SimpleNamespace(**values)


def _payload(out: Path):
    return json.loads(gzip.decompress((out / "scene.bin.gz").read_bytes()))


def _shape_tree(payload):
    # TCV 5.1.3 accepts this native instance/ref wrapper and decodes it in
    # Viewer.render; older inline shape trees remain a valid direct format too.
    return payload.get("shapes", payload)


def _ocp_face_order(shape):
    from ocp_tessellate.ocp_utils.topology import get_faces

    return list(get_faces(shape.wrapped))


def _ocp_edge_order(shape):
    from ocp_tessellate.ocp_utils.topology import get_edges

    return [edge for edge, _face in get_edges(shape.wrapped, True)]


def _expected_feature_names(ordered, engine_entities, by_index):
    names = []
    for topology in ordered:
        matches = [
            index
            for index, entity in enumerate(engine_entities)
            if topology.IsSame(getattr(entity, "wrapped", entity))
        ]
        assert len(matches) == 1
        names.append(by_index.get(matches[0], "main"))
    return names


def test_scene_payload_and_manifest_are_byte_stable(tmp_path):
    shape = Box(10, 20, 30)
    ctx = _context(shape)
    first = make_scene(ctx, tmp_path / "first")
    second = make_scene(ctx, tmp_path / "second")

    first_blob = (tmp_path / "first" / "scene.bin.gz").read_bytes()
    second_blob = (tmp_path / "second" / "scene.bin.gz").read_bytes()
    assert first_blob == second_blob
    assert first_blob[4:8] == b"\0\0\0\0"
    assert first.scene_id == hashlib.sha256(first_blob).hexdigest()
    assert second.scene_id == first.scene_id


def test_feature_map_matches_engine_topology_indices(tmp_path):
    # A Boolean cut gives both planar and cylindrical faces and tests the
    # actual entity geometry instead of assuming traversal indices line up.
    shape = Box(20, 20, 6) - Cylinder(2, 8)
    engine_faces = list(shape.faces())
    engine_edges = list(shape.edges())
    features = [
        SimpleNamespace(name="plate", faces=[_entity(0)], edges=[_entity(0)]),
        SimpleNamespace(name="bore", faces=[_entity(2)], edges=[_entity(2)]),
    ]
    ctx = _context(shape, features=features)

    manifest = make_scene(ctx, tmp_path)
    payload = _payload(tmp_path)
    scene_shapes = _shape_tree(payload)
    path = manifest.parts[0]["path"]
    actual = scene_shapes["feature_map"][path]

    feature_faces = {0: "plate", 2: "bore"}
    feature_edges = {0: "plate", 2: "bore"}
    ordered_faces = _ocp_face_order(shape)
    ordered_edges = _ocp_edge_order(shape)
    # ocp-tessellate 3.6.3 walks the TopTools indexed map (get_faces/get_edges).
    # This boolean currently has the same order as build123d's faces()/edges();
    # the production mapper uses IsSame, so it also handles future order changes
    # and assembly locations. Every face/edge in this solid is tessellated.
    assert len(ordered_faces) == len(engine_faces)
    assert len(ordered_edges) <= len(engine_edges)
    assert actual["faces"] == _expected_feature_names(ordered_faces, engine_faces, feature_faces)
    assert actual["edges"] == _expected_feature_names(ordered_edges, engine_edges, feature_edges)
    assert "plate" in actual["faces"] and "bore" in actual["faces"]
    assert "main" in actual["faces"]


def test_assembly_parts_keep_paths_and_world_topology_mapping(tmp_path):
    left = Box(8, 10, 4).moved(Location((-10, 0, 0)))
    left.label = "left"
    right = Cylinder(3, 7).moved(Location((10, 0, 0)))
    right.label = "right"
    shape = Compound(children=[left, right])

    global_faces = list(shape.faces())
    global_edges = list(shape.edges())
    left_face = next(
        i for i, face in enumerate(global_faces) if face.wrapped.IsSame(left.faces()[0].wrapped)
    )
    right_face = next(
        i for i, face in enumerate(global_faces) if face.wrapped.IsSame(right.faces()[0].wrapped)
    )
    left_edge = next(
        i for i, edge in enumerate(global_edges) if edge.wrapped.IsSame(left.edges()[0].wrapped)
    )
    right_edge = next(
        i for i, edge in enumerate(global_edges) if edge.wrapped.IsSame(right.edges()[0].wrapped)
    )
    ctx = _context(
        shape,
        features=[
            SimpleNamespace(name="left-feature", faces=[_entity(left_face)], edges=[_entity(left_edge)]),
            SimpleNamespace(name="right-feature", faces=[_entity(right_face)], edges=[_entity(right_edge)]),
        ],
    )

    manifest = make_scene(ctx, tmp_path)
    payload = _payload(tmp_path)
    scene_shapes = _shape_tree(payload)

    assert [part["name"] for part in manifest.parts] == ["left", "right"]
    assert len({part["path"] for part in manifest.parts}) == 2
    assert set(scene_shapes["feature_map"]) == {part["path"] for part in manifest.parts}
    all_mapped_names = {
        name
        for part_map in scene_shapes["feature_map"].values()
        for name in part_map["faces"] + part_map["edges"]
    }
    assert {"left-feature", "right-feature"} <= all_mapped_names


def test_curved_part_bbox_and_manifest_schema(tmp_path):
    shape = Cylinder(5, 20)
    ctx = _context(
        shape,
        project_dir=tmp_path / "project-a",
        rev=8,
        params={"diameter": SimpleNamespace(value=10, min=2, max=20, unit="mm")},
        checks_summary={"pass": 4, "fail": 1, "warn": 0, "skip": 2},
        failed_features=["bore"],
    )

    manifest = make_scene(ctx, tmp_path / "scene")
    data = json.loads((tmp_path / "scene" / "scene.json").read_text())
    payload = _payload(tmp_path / "scene")
    scene_shapes = _shape_tree(payload)

    assert manifest.triangles and manifest.triangles > 12
    assert manifest.bbox == [-5.0, -5.0, -10.0, 5.0, 5.0, 10.0]
    assert data["bbox"] == manifest.bbox
    assert data["format"] == "tcv-5"
    assert data["encoding"] == "gzip"
    assert data["project"] == "project-a"
    assert data["rev"] == 8
    assert data["status"] == "draft"
    assert data["total_bytes"] == (tmp_path / "scene" / "scene.bin.gz").stat().st_size
    assert data["scene_id"] == hashlib.sha256((tmp_path / "scene" / "scene.bin.gz").read_bytes()).hexdigest()
    assert data["params"] == [{"name": "diameter", "value": 10, "min": 2, "max": 20, "unit": "mm"}]
    assert data["checks_summary"] == {"pass": 4, "fail": 1, "warn": 0, "skip": 2}
    assert data["failed_features"] == ["bore"]
    assert "instances" in payload and "shapes" in payload
    assert scene_shapes["feature_map"]


def test_compare_scene_is_written_beside_primary_and_embedded(tmp_path):
    primary = _context(Box(10, 10, 10), rev=1)
    compare = _context(Cylinder(4, 8), rev=0)

    manifest = make_scene(primary, tmp_path / "primary", compare=compare)

    compare_dir = tmp_path / "primary" / "compare"
    compare_data = json.loads((compare_dir / "scene.json").read_text())
    primary_data = json.loads((tmp_path / "primary" / "scene.json").read_text())
    assert (compare_dir / "scene.bin.gz").is_file()
    assert manifest.compare is not None
    assert manifest.compare.scene_id == compare_data["scene_id"]
    assert primary_data["compare"]["scene_id"] == compare_data["scene_id"]
