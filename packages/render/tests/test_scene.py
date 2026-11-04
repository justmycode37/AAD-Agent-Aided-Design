from __future__ import annotations

import gzip
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

from build123d import Box, Compound, Cylinder, Edge, Face, Location, Plane, Wire, export_brep, import_brep, mirror
import pytest

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


def _decoded(mesh):
    import base64
    import struct

    raw = base64.b64decode(mesh["vertices"]["buffer"])
    return list(struct.unpack(f"<{len(raw) // 4}f", raw))


def _world_bbox(payload):
    """Vertices through each leaf's loc, the way the viewer places them."""
    lows, highs = [float("inf")] * 3, [float("-inf")] * 3

    def rotate(point, quaternion):
        x, y, z = point
        qx, qy, qz, qw = quaternion
        cx, cy, cz = 2 * (qy * z - qz * y), 2 * (qz * x - qx * z), 2 * (qx * y - qy * x)
        return [x + qw * cx + qy * cz - qz * cy, y + qw * cy + qz * cx - qx * cz, z + qw * cz + qx * cy - qy * cx]

    def visit(node, translation, quaternion):
        loc = node.get("loc")
        t, q = translation, quaternion
        if loc:
            moved = rotate(loc[0], q)
            t = [a + b for a, b in zip(t, moved, strict=True)]
            ax, ay, az, aw = q
            bx, by, bz, bw = loc[1]
            q = [
                aw * bx + ax * bw + ay * bz - az * by,
                aw * by - ax * bz + ay * bw + az * bx,
                aw * bz + ax * by - ay * bx + az * bw,
                aw * bw - ax * bx - ay * by - az * bz,
            ]
        shape = node.get("shape")
        if isinstance(shape, dict) and "ref" in shape:
            values = _decoded(payload["instances"][shape["ref"]])
            for i in range(0, len(values), 3):
                world = rotate(values[i : i + 3], q)
                for axis in range(3):
                    value = world[axis] + t[axis]
                    lows[axis] = min(lows[axis], value)
                    highs[axis] = max(highs[axis], value)
        for child in node.get("parts") or []:
            visit(child, t, q)

    visit(payload["shapes"], [0.0, 0.0, 0.0], [0.0, 0.0, 0.0, 1.0])
    return [*lows, *highs]


def test_assembly_parents_location_reaches_the_mesh(tmp_path):
    """Regression: a moved assembly kept its manifest bbox while its mesh stayed at the origin."""
    shape = Compound(children=[Box(2, 4, 6).moved(Location((3, 0, 0)))])
    shape.move(Location((100, 20, 0), (0, 0, 90)))
    manifest = make_scene(_context(shape), tmp_path)
    assert manifest.bbox == pytest.approx([98, 22, -3, 102, 24, 3], abs=1e-3)
    assert _world_bbox(_payload(tmp_path)) == pytest.approx(manifest.bbox, abs=1e-3)


def test_nested_assembly_locations_compose(tmp_path):
    inner = Compound(children=[Box(2, 2, 2).moved(Location((5, 0, 0)))])
    inner.move(Location((0, 10, 0)))
    shape = Compound(children=[inner])
    shape.move(Location((0, 0, 100)))
    manifest = make_scene(_context(shape), tmp_path)
    assert _world_bbox(_payload(tmp_path)) == pytest.approx(manifest.bbox, abs=1e-3)
    assert manifest.bbox == pytest.approx([4, 9, 99, 6, 11, 101], abs=1e-3)


def test_located_faces_keep_their_own_feature(tmp_path):
    """Regression: replacing a location gave one face's feature to its neighbour."""
    face = Face.make_rect(3, 5)
    shape = Compound([face.moved(Location((10, 0, 0))), face]).moved(Location((0, 20, 0)))
    features = [SimpleNamespace(name=f"owner{i}", faces=[_entity(i)], edges=[]) for i in range(len(shape.faces()))]
    manifest = make_scene(_context(shape, features=features), tmp_path)
    maps = _payload(tmp_path)["shapes"]["feature_map"][manifest.parts[0]["path"]]
    assert sorted(maps["faces"]) == ["owner0", "owner1"]


def test_feature_map_and_bytes_survive_reloading_one_brep(tmp_path):
    """Regression: a mesh cache hit erased every label and changed the bytes for one revision."""
    base = Box(10, 10, 10) - Cylinder(2, 12)
    brep = tmp_path / "part.brep"
    export_brep(base, brep)
    blobs, labels = [], []
    for index in range(3):
        shape = import_brep(str(brep))
        features = [SimpleNamespace(name="all", faces=[_entity(i) for i in range(len(shape.faces()))], edges=[_entity(i) for i in range(len(shape.edges()))])]
        manifest = make_scene(_context(shape, features=features), tmp_path / str(index))
        blobs.append((tmp_path / str(index) / "scene.bin.gz").read_bytes())
        labels.append(_payload(tmp_path / str(index))["shapes"]["feature_map"][manifest.parts[0]["path"]])
    assert blobs[0] == blobs[1] == blobs[2]
    assert labels[0] == labels[1] == labels[2]
    assert set(labels[0]["faces"]) == {"all"} and set(labels[0]["edges"]) == {"all"}


def test_same_shape_twice_gives_the_same_labels(tmp_path):
    shape = Box(10, 10, 10)
    features = [SimpleNamespace(name="owned", faces=[_entity(i) for i in range(6)], edges=[])]
    first = make_scene(_context(shape, features=features), tmp_path / "a")
    second = make_scene(_context(shape, features=features), tmp_path / "b")
    assert first.scene_id == second.scene_id


def test_mixed_compound_keeps_its_loose_geometry(tmp_path):
    """Regression: a box plus a loose face lost the face, while the manifest bbox still included it."""
    face = Face.make_rect(40, 40).moved(Location((0, 0, 100)))
    shape = Compound([Box(7, 9, 11), face])
    manifest = make_scene(_context(shape), tmp_path)
    world = _world_bbox(_payload(tmp_path))
    assert world[5] == pytest.approx(100, abs=1e-3) and world[3] == pytest.approx(20, abs=1e-3)
    assert world == pytest.approx(manifest.bbox, abs=1e-3)


@pytest.mark.parametrize("shape", [Wire.make_circle(9), Edge.make_line((0, 0, 0), (1, 0, 0))])
def test_wires_alone_are_refused_before_anything_is_written(tmp_path, shape):
    with pytest.raises(ValueError, match="at least one face"):
        make_scene(_context(shape), tmp_path)
    assert not (tmp_path / "scene.json").exists()


def test_mirrored_solid_mesh_matches_its_bbox(tmp_path):
    shape = mirror(Box(2, 4, 6).moved(Location((10, 0, 0))), Plane.YZ)
    manifest = make_scene(_context(shape), tmp_path)
    assert _world_bbox(_payload(tmp_path)) == pytest.approx(manifest.bbox, abs=1e-3)


def test_part_color_is_neutral(tmp_path):
    manifest = make_scene(_context(Box(1, 1, 1)), tmp_path)
    assert manifest.parts[0]["color"] == "#b8c0cc"
