from __future__ import annotations

import tempfile
import unittest
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from aiad._runtime import (
    ModelInputError,
    Param,
    configure_params,
    current_param_definitions,
    current_params,
    params,
    validate_params,
)
from aiad.engine import _worker
from aiad.engine.jobs import run_build
from aiad.engine.select import SelectorError, entity_shape, resolve
from aiad.engine.types import BuildRequest, Entity, FeatureInfo, ModelContext

ROOT = Path(__file__).resolve().parents[3]
MOUNT = (ROOT / "fixtures/models/named_mount.py").read_text(encoding="utf-8")
BOX = """from aiad import *
with part("block") as bp:
    with feature("stock", intent="rectangular stock"):
        Box(10, 8, 4)
        corner = vertex(feature="stock", nth=0)
        uprights = edge(feature="stock", where="parallel_z", expect=4)
        assert len(uprights) == 4
"""


class ParameterTests(unittest.TestCase):
    def setUp(self) -> None:
        configure_params()

    def test_ranges_overrides_and_slider_metadata(self) -> None:
        configure_params({"width": 24, "spacing": [8, 12], "label": "edited"})
        values = params(width=Param(20.0, 10, 30, "mm"), spacing=(6.0, 10.0), label="original")
        self.assertEqual((values.width, values.spacing, values.label), (24, (8, 12), "edited"))
        self.assertEqual(current_params()["width"], 24)
        self.assertEqual(current_param_definitions()["width"], {"value": 24, "min": 10, "max": 30, "unit": "mm"})
        validate_params()

    def test_invalid_defaults_and_bounds_are_rejected(self) -> None:
        invalid = (
            lambda: Param(float("nan")),
            lambda: Param(float("inf")),
            lambda: Param(1, min=float("-inf")),
            lambda: Param(1, max=float("nan")),
            lambda: Param(1, min=2, max=0),
            lambda: Param(4, min=5),
            lambda: Param("text", max=3),
            lambda: Param(True),
            lambda: params(spacing=(1, [float("nan")])),
        )
        for declaration in invalid:
            with self.subTest(declaration=declaration), self.assertRaises(ModelInputError):
                configure_params()
                declaration()

    def test_unknown_missing_nonfinite_and_out_of_range_overrides(self) -> None:
        for overrides in ({"typo": 3}, {"width": 31}, {"width": 9}, {"width": float("inf")}, {"width": "24"}, {"width": True}):
            with self.subTest(overrides=overrides), self.assertRaises(ModelInputError):
                configure_params(overrides)
                params(width=Param(20, 10, 30))
        configure_params({"width": 24})
        with self.assertRaisesRegex(ModelInputError, "params\\(\\) declaration"):
            validate_params()
        configure_params({"spacing": [[1, float("nan")]]})
        with self.assertRaises(ModelInputError):
            params(spacing=((2, 3),))


class ModelingTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory(prefix="aiad-modeling-test-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.jobs = 0

    def live_build(self, source: str = MOUNT, **changes):
        """Run the real pipeline and inspect its context before JSON drops shapes."""
        self.jobs += 1
        job = self.root / str(self.jobs)
        job.mkdir()
        payload = dict(source=source, project_dir=str(self.root), job_dir=str(job), units="mm", params={}, spec={})
        payload.update(changes)
        with patch.object(_worker, "_result_wire", lambda result: result):
            return _worker._build(payload)

    def test_named_box_live_helpers_and_exact_same_context_measurement(self) -> None:
        # A later construction builder must not replace the declared part.
        multiline = BOX.replace('with feature("stock", intent="rectangular stock"):', 'with feature(\n        "stock",\n        intent="rectangular stock",\n    ):')
        built = self.live_build(multiline + "with BuildPart(mode=Mode.PRIVATE):\n    Box(1, 1, 1)\n")
        self.assertTrue(built.ok, built.failure)
        ctx = built.context
        self.assertEqual(ctx.part_name, "block")
        self.assertEqual(ctx.shape.label, "block")
        self.assertEqual(built.metrics["bbox"], [10, 8, 4])
        stock, = ctx.features
        self.assertEqual((stock.line_start, stock.line_end), (3, 10))
        self.assertEqual((len(stock.faces), len(stock.edges), len(stock.vertices)), (6, 12, 8))
        top, = resolve(ctx, "face(feature='stock', where='max_z')")
        self.assertEqual(top.geometry["normal"], [0, 0, 1])
        self.assertAlmostEqual(top.geometry["area"], 80)
        self.assertAlmostEqual(entity_shape(ctx, top).area, 80)
        self.assertNotIn("nth", top.selector)
        self.assertEqual(len(resolve(ctx, "vertex(feature='stock', max_min_xyz='max_x')", expect=4)), 4)
        with self.assertRaises(ValueError):
            entity_shape(ModelContext(shape=ctx.shape, features=ctx.features), top)
        with self.assertRaises(ValueError):
            entity_shape(ctx, Entity(**asdict(top)))
        from build123d import Box
        ctx.shape = Box(2, 2, 2)
        with self.assertRaises(ValueError):
            entity_shape(ctx, top)
        with self.assertRaises(SelectorError):
            resolve(ctx, "face(feature='stock', where='max_z')")

    def test_box_hole_fillet_names_lines_intent_dependencies_and_coverage(self) -> None:
        built = self.live_build()
        self.assertTrue(built.ok, built.failure)
        ctx = built.context
        self.assertTrue(ctx.shape.is_valid)
        self.assertEqual(built.status, "draft")
        features = {f.name: f for f in ctx.features}
        self.assertEqual(list(features), ["plate", "mount_holes", "edge_round"])
        ranges = {"plate": (16, 17), "mount_holes": (19, 23), "edge_round": (25, 26)}
        previous = set()
        for name, info in features.items():
            self.assertEqual((info.line_start, info.line_end), ranges[name])
            self.assertTrue(set(info.deps).issubset(previous), info.deps)
            previous.add(name)
        self.assertEqual(features["mount_holes"].intent, "M4 clearance")
        self.assertIn("plate", features["mount_holes"].deps)
        self.assertIn("plate", features["edge_round"].deps)
        for kind, attr in (("face", "faces"), ("edge", "edges"), ("vertex", "vertices")):
            entities = [e for f in ctx.features for e in getattr(f, attr)]
            self.assertEqual(sorted(e.index for e in entities), list(range(len(getattr(ctx.shape, attr)()))))
        holes = resolve(ctx, "face(feature='mount_holes', kind='cylinder')", expect=4)
        for hole in holes:
            self.assertAlmostEqual(hole.geometry["radius"], 2.2)
            self.assertAlmostEqual(abs(hole.geometry["center"][0]), 23)
            self.assertAlmostEqual(abs(hole.geometry["center"][1]), 13)
            self.assertAlmostEqual(entity_shape(ctx, hole).radius, 2.2)
        self.assertEqual(len(resolve(ctx, "face(feature='edge_round', kind='cylinder')", expect=4)), 4)
        self.assertEqual(ctx.param_definitions["width"]["max"], 100)

    def test_parent_result_preserves_named_metadata_and_semantic_selection(self) -> None:
        source = '''from aiad import *
P = params(width=Param(10, 5, 20, "mm"))
meta(name="roundtrip", units="mm", purpose={"description": "fixture"})
with part("block"):
    with feature("stock", intent="rectangular stock"):
        Box(P.width, 8, 4)
'''
        built = run_build(BuildRequest(source=source, project_dir=self.root, params={"width": 12}))
        self.assertTrue(built.ok, built.failure)
        ctx = built.context
        self.assertIsNone(ctx.shape)
        self.assertEqual(ctx.part_name, "block")
        self.assertEqual(ctx.meta["purpose"], {"description": "fixture"})
        self.assertEqual(ctx.param_definitions["width"], {"value": 12, "min": 5, "max": 20, "unit": "mm"})
        stock, = ctx.features
        self.assertEqual(stock.intent, "rectangular stock")
        self.assertEqual(len(stock.vertices), 8)
        top, = resolve(ctx, "face(feature='stock', where='max_z')")
        self.assertEqual(top.geometry["area"], 96)
        with self.assertRaises(ValueError):
            entity_shape(ctx, top)

    def test_in_place_native_changes_invalidate_exact_handles(self) -> None:
        from build123d import Box, Location, Shell
        from OCP.BRep import BRep_Builder

        for operation in (lambda shape: shape.move(Location((0, 0, 10))),
                          lambda shape: shape.wrapped.Reverse()):
            with self.subTest(operation=operation):
                ctx = ModelContext(shape=Box(10, 8, 4))
                top, = resolve(ctx, "face(where='max_z')")
                self.assertEqual(entity_shape(ctx, top).center().Z, 2)
                operation(ctx.shape)
                with self.assertRaisesRegex(ValueError, "native placement or topology changed"):
                    entity_shape(ctx, top)
                fresh, = resolve(ctx, "face(where='max_z')")
                self.assertAlmostEqual(entity_shape(ctx, fresh).center().Z, fresh.geometry["center"][2])

        ctx = ModelContext(shape=Shell(list(Box(10, 8, 4).faces())))
        top, = resolve(ctx, "face(where='max_z')")
        bottom = min(ctx.shape.faces(), key=lambda face: face.center().Z)
        ctx.shape.wrapped.Free(True)
        BRep_Builder().Remove(ctx.shape.wrapped, bottom.wrapped)
        with self.assertRaisesRegex(ValueError, "native placement or topology changed"):
            entity_shape(ctx, top)

    def test_param_edits_retain_names_or_return_actual_candidates(self) -> None:
        edited = self.live_build(params={"width": 70, "thickness": 8})
        self.assertTrue(edited.ok, edited.failure)
        self.assertEqual(edited.metrics["bbox"], [70, 40, 8])
        self.assertEqual(len(resolve(edited.context, "face(feature='mount_holes', kind='cylinder')", expect=4)), 4)
        without_holes = self.live_build(params={"width": 40})
        self.assertTrue(without_holes.ok, without_holes.failure)
        with self.assertRaises(SelectorError) as raised:
            resolve(without_holes.context, "face(feature='mount_holes', kind='cylinder')", expect=4)
        self.assertEqual(raised.exception.failure.code, "SELECTOR_EMPTY")
        self.assertEqual(len(raised.exception.failure.candidates), without_holes.metrics["faces"])
        self.assertTrue(all(candidate.geometry.get("area", 0) > 0 for candidate in raised.exception.failure.candidates))

    def test_identity_is_preserved_but_replacement_does_not_guess_ancestry(self) -> None:
        drilled = self.live_build("""from aiad import *
with part("block"):
    with feature("stock"):
        Box(10, 8, 4)
    original_side = face(feature="stock", where="max_x")
    with feature("drill"):
        Hole(1)
    assert original_side.is_same(face(feature="stock", where="max_x"))
""")
        self.assertTrue(drilled.ok, drilled.failure)
        self.assertIn("stock", next(f for f in drilled.context.features if f.name == "drill").deps)
        replaced = self.live_build("""from aiad import *
with part("block"):
    with feature("stock"):
        Box(10, 8, 4)
    with feature("replacement"):
        add(Box(10, 8, 4, mode=Mode.PRIVATE), mode=Mode.REPLACE)
""")
        self.assertTrue(replaced.ok, replaced.failure)
        self.assertFalse(next(f for f in replaced.context.features if f.name == "stock").faces)
        self.assertEqual(len(resolve(replaced.context, "face(feature='replacement')", expect=6)), 6)
        separate = self.live_build(BOX + "result = Box(10, 8, 4)\n")
        self.assertTrue(separate.ok, separate.failure)
        with self.assertRaises(SelectorError) as raised:
            resolve(separate.context, "face(feature='stock', where='max_z')")
        self.assertTrue(raised.exception.failure.candidates)
        self.assertTrue(all(c.feature is None for c in raised.exception.failure.candidates))

    def test_count_failures_have_real_metadata_and_semantic_candidate_selectors(self) -> None:
        ctx = self.live_build().context
        with self.assertRaises(SelectorError) as raised:
            resolve(ctx, "face(feature='mount_holes', kind='cylinder')", expect=1)
        failure = raised.exception.failure
        self.assertEqual(failure.code, "SELECTOR_AMBIGUOUS")
        self.assertEqual(len(failure.candidates), 4)
        for candidate in failure.candidates:
            self.assertEqual(candidate.feature, "mount_holes")
            self.assertEqual(candidate.geometry["type"], "CYLINDER")
            self.assertIn("center", candidate.geometry)
            self.assertIn("area", candidate.geometry)
            self.assertNotIn("index", candidate.selector)
            resolved, = resolve(ctx, candidate.selector)
            self.assertEqual(resolved.geometry, candidate.geometry)
        with self.assertRaises(SelectorError) as empty:
            resolve(ctx, "face(feature='missing')")
        self.assertEqual(empty.exception.failure.code, "SELECTOR_EMPTY")
        for candidate in empty.exception.failure.candidates:
            self.assertEqual(resolve(ctx, candidate.selector)[0].geometry, candidate.geometry)
        self.assertEqual(resolve(ctx, "face(feature='missing')", expect=0), [])

    def test_ordering_ignores_kernel_enumeration_and_ties_are_explicit(self) -> None:
        from build123d import Box, Compound
        shape = Box(10, 8, 4)
        forward = ModelContext(shape=shape)
        reverse = ModelContext(shape=SimpleNamespace(wrapped=shape.wrapped, faces=lambda: list(reversed(shape.faces()))))
        first, = resolve(forward, "face(nth=0)")
        second, = resolve(reverse, "face(nth=0)")
        self.assertEqual(first.geometry, second.geometry)
        self.assertTrue(entity_shape(forward, first).is_same(entity_shape(reverse, second)))
        coincident = ModelContext(shape=Compound([Box(2, 2, 2), Box(2, 2, 2)]))
        for selector, count in (("face(nth=0)", None), ("face()", 12)):
            with self.subTest(selector=selector), self.assertRaises(SelectorError) as raised:
                resolve(coincident, selector, expect=count)
            self.assertEqual(raised.exception.failure.code, "SELECTOR_AMBIGUOUS")
            self.assertIn("tied", raised.exception.failure.message)
            self.assertEqual(len(raised.exception.failure.candidates), 12)
            self.assertTrue(all("nth" not in c.selector for c in raised.exception.failure.candidates))
        partial = ModelContext(features=[FeatureInfo(faces=[
            Entity(kind="face", index=0, geometry={"type": "PLANE", "center": [0, 0, 0]}),
            Entity(kind="face", index=1, geometry={"type": "PLANE", "center": [0, 0, 0], "normal": [0, 0, 1]}),
        ])])
        self.assertEqual(len(resolve(partial, "face()", expect=2)), 2)

    def test_literal_selector_parser_rejects_injection_and_raw_indices(self) -> None:
        ctx = self.live_build(BOX).context
        marker = self.root / "injected"
        injected = f"face(where=__import__('pathlib').Path({str(marker)!r}).write_text('executed'))"
        selectors = (
            injected, "face()[0]", "face(index=0)", "face(nth=1+1)", "face(nth=True)",
            "face(nth=-1)", "face(**{'where': 'max_z'})", "ctx.shape.faces()[0]",
            "face(where='max_z'); __import__('os')", "face(parallel_xyz=(0,0,0))",
            "edge(parallel_xyz=(1e309,0,0))", "face(where='max_z', max_min_xyz='max_x')",
        )
        for selector in selectors:
            with self.subTest(selector=selector), self.assertRaises(SelectorError) as raised:
                resolve(ctx, selector)
            self.assertEqual(raised.exception.failure.code, "BAD_INPUT")
        self.assertFalse(marker.exists())
        self.assertEqual(len(resolve(ctx, "face(max_min_xyz={'axis':'z', 'extreme':'max'})")), 1)
        self.assertEqual(len(resolve(ctx, "edge(parallel_xyz=(0,0,2))", expect=4)), 4)

    def test_metadata_reaches_pipeline_consistently_and_conflicts_fail(self) -> None:
        source = 'from aiad import *\nmeta(units="in", material="steel_1018", process="cnc_3axis", note={"test": 1})\nresult = Box(2, 3, 4)\n'
        built = self.live_build(source, units="in", spec={"units": "in", "material": "steel_1018", "process": "cnc_3axis"})
        self.assertTrue(built.ok, built.failure)
        self.assertEqual((built.context.units, built.context.material, built.context.process), ("in", "steel_1018", "cnc_3axis"))
        self.assertEqual(built.context.meta["note"], {"test": 1})
        self.assertEqual(built.metrics["units"], "in")
        self.assertEqual(built.metrics["bbox"], [2, 3, 4])
        for changes, key in (({}, "units"), ({"units": "in", "material": "aluminum"}, "material"),
                             ({"units": "in", "process": "laser"}, "process"),
                             ({"units": "in", "spec": {"units": "mm"}}, "units")):
            with self.subTest(changes=changes):
                failed = self.live_build(source, **changes)
                self.assertEqual(failed.failure.code, "BAD_INPUT")
                self.assertIn(f"conflicting {key}", failed.failure.message)
                self.assertFalse(failed.artifacts)
        invalid = self.live_build('meta(note={"invalid": float("nan")})\nresult = Box(2,3,4)')
        self.assertEqual(invalid.failure.code, "BAD_INPUT")

    def test_plain_build123d_context_and_algebra_reset_to_implicit_main(self) -> None:
        self.assertTrue(self.live_build().ok)
        for source in ("from build123d import *\nwith BuildPart() as bp:\n    Box(2,3,4)\n", "result = Box(2,3,4)"):
            with self.subTest(source=source):
                built = self.live_build(source)
                self.assertTrue(built.ok, built.failure)
                main, = built.context.features
                self.assertEqual(main.name, "main")
                self.assertEqual((len(main.faces), len(main.edges), len(main.vertices)), (6, 12, 8))
                self.assertFalse(built.context.params)
                self.assertIsNone(built.context.material)
                self.assertEqual(resolve(built.context, "face(feature='main', where='max_z')")[0].feature, "main")

    def test_worker_preserves_named_selector_failures_and_rejects_unknown_overrides(self) -> None:
        named = run_build(BuildRequest(source=MOUNT, project_dir=self.root, params={"width": 70}))
        self.assertTrue(named.ok, named.failure)
        self.assertIsNone(named.context.shape)
        self.assertEqual((named.context.units, named.context.material, named.context.process), ("mm", "steel_1018", "cnc_3axis"))
        self.assertEqual(len(resolve(named.context, "face(feature='mount_holes', kind='cylinder')", expect=4)), 4)
        source = BOX + 'with part("second"):\n    Box(1,1,1)\n'
        self.assertIn("one named part", self.live_build(source).failure.message)
        duplicate = self.live_build(BOX + 'with bp:\n    with feature("stock"):\n        Box(1,1,1)\n')
        self.assertEqual(duplicate.failure.code, "BAD_INPUT")
        self.assertIn("already declared", duplicate.failure.message)
        source = """from aiad import *
with part("bad"):
    with feature("stock"):
        Box(10, 8, 4)
    with feature("choose"):
        face(feature="stock", expect=1)
"""
        built = run_build(BuildRequest(source=source, project_dir=self.root))
        self.assertEqual(built.failure.code, "SELECTOR_AMBIGUOUS")
        self.assertEqual((built.failure.feature, built.failure.file, built.failure.line), ("choose", "model.py", 6))
        self.assertEqual(len(built.failure.candidates), 6)
        self.assertTrue(all(c.geometry["area"] > 0 for c in built.failure.candidates))
        unknown = self.live_build(MOUNT, params={"typo": 4})
        self.assertEqual(unknown.failure.code, "BAD_INPUT")
        self.assertIn("typo", unknown.failure.message)
        undeclared = self.live_build("result = Box(2,3,4)", params={"width": 3})
        self.assertEqual(undeclared.failure.code, "BAD_INPUT")
        invalid = self.live_build('params(width=Param(float("nan")))\nresult = Box(2,3,4)')
        self.assertEqual(invalid.failure.code, "BAD_INPUT")


if __name__ == "__main__":
    unittest.main()
