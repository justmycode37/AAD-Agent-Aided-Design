from __future__ import annotations

import copy
import hashlib
import json
import os
import subprocess
import sys
import unittest
from collections import Counter
from pathlib import Path

from evals.benchmark.corpus.loader import (
    ASSETS_DIR,
    CORPUS_DIR,
    CorpusValidationError,
    load_corpus,
    load_public_projection,
    public_projection,
    validate_corpus,
)
from evals.benchmark.corpus.references import build_all_references


REPOSITORY = CORPUS_DIR.parents[2]


class CorpusContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.corpus = load_corpus()

    def test_loader_import_is_kernel_free(self) -> None:
        snippet = (
            "import sys; import evals.benchmark.corpus.loader; "
            "assert 'build123d' not in sys.modules; assert 'ezdxf' not in sys.modules; "
            "assert 'aiad_quality' not in sys.modules; assert 'aiad.engine' not in sys.modules"
        )
        result = subprocess.run(
            [sys.executable, "-c", snippet],
            cwd=REPOSITORY,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_target_distribution_and_unique_public_requests(self) -> None:
        counts = Counter(task["category"] for task in self.corpus["tasks"])
        self.assertEqual(
            counts,
            Counter(
                {
                    "part": 40,
                    "edit_chain": 20,
                    "assembly": 10,
                    "2d_profile": 15,
                    "drawing": 5,
                    "clarification": 5,
                    "infeasible": 5,
                }
            ),
        )
        family_counts = Counter(task["family"] for task in self.corpus["tasks"])
        self.assertGreaterEqual(len(family_counts), 20)
        self.assertLessEqual(max(family_counts.values()), 5)
        artifact_categories = {"part", "edit_chain", "assembly", "2d_profile", "drawing"}
        artifact_families = Counter(
            task["family"] for task in self.corpus["tasks"] if task["category"] in artifact_categories
        )
        self.assertGreaterEqual(len(artifact_families), 20)
        self.assertLessEqual(max(artifact_families.values()), 5)
        requests = [stage["request"] for task in self.corpus["tasks"] for stage in task["stages"]]
        self.assertEqual(len(requests), len(set(requests)))
        for task in self.corpus["tasks"]:
            for stage in task["stages"]:
                if stage["expected_outcome"] != "artifact":
                    continue
                request = stage["request"].casefold()
                self.assertTrue(
                    any(marker in request for marker in ("coordinates", "origin", "frame", "xy sketch", "projection")),
                    f"missing explicit frame in {task['id']}/{stage['id']}",
                )
                self.assertTrue(
                    "±" in request or "tolerance" in request or "within" in request,
                    f"missing tolerance in {task['id']}/{stage['id']}",
                )

    def test_public_projection_matches_and_contains_no_hidden_fields(self) -> None:
        self.assertEqual(public_projection(self.corpus), load_public_projection())
        self.assertEqual(len(self.corpus["tasks"]), len(load_public_projection()["tasks"]))
        for task in load_public_projection()["tasks"]:
            self.assertEqual(set(task), {"id", "title", "kind", "stages"})
            for stage in task["stages"]:
                self.assertEqual(set(stage), {"id", "request", "input_assets"})
        encoded = json.dumps(load_public_projection())
        for hidden in ("expected_outcome", "oracle", "preserve_check_ids", "reference", "family", "difficulty"):
            self.assertNotIn(f'"{hidden}"', encoded)

    def test_vague_and_infeasible_tasks_have_no_positive_references(self) -> None:
        for task in self.corpus["tasks"]:
            if task["category"] in {"clarification", "infeasible"}:
                self.assertNotIn("reference", task)
                self.assertEqual(task["stages"][0]["expected_outcome"], task["category"])
                self.assertEqual(len(task["stages"][0]["oracle"]["checks"]), 1)

    def test_inch_units_and_import_edit_are_explicit(self) -> None:
        inch_task = next(task for task in self.corpus["tasks"] if task["id"] == "part_lid_002")
        self.assertIn("units:in", inch_task["tags"])
        self.assertIn("±0.004 in", inch_task["stages"][0]["request"])
        bbox = next(check for check in inch_task["stages"][0]["oracle"]["checks"] if check["type"] == "bbox_mm")
        self.assertEqual(bbox["ranges"], [[101.4984, 101.7016], [76.0984, 76.3016], [4.9784, 5.1816]])
        bores = next(check for check in inch_task["stages"][0]["oracle"]["checks"] if check["type"] == "cylindrical_bores")["bores"]
        self.assertEqual(bores[0]["diameter_mm"], [5.0292, 5.1308])
        self.assertAlmostEqual(bores[0]["center_tolerance_mm"], 0.0508)

        imported = next(task for task in self.corpus["tasks"] if task["id"] == "edit_import_mesh_019")
        self.assertEqual(len(imported["stages"]), 2)
        for stage in imported["stages"]:
            self.assertIn("fan_envelope.stl", stage["request"])
            self.assertEqual(stage["input_assets"][0]["id"], "fan_envelope")
        first_types = {check["type"] for check in imported["stages"][0]["oracle"]["checks"]}
        second_types = {check["type"] for check in imported["stages"][1]["oracle"]["checks"]}
        self.assertIn("asset_match", first_types)
        self.assertIn("cylindrical_bores", second_types)
        self.assertEqual(imported["stages"][0]["oracle"]["coverage"], "bounded")

        carrier = next(task for task in self.corpus["tasks"] if task["id"] == "part_fan_carrier_001")
        clearance = next(
            check for check in carrier["stages"][0]["oracle"]["checks"] if check["type"] == "clearance_mm"
        )
        self.assertEqual(clearance["asset_id"], "fan_envelope")
        self.assertEqual(clearance["range"], [1.9, 2.1])

    def test_edit_chains_have_matching_retention_checks(self) -> None:
        edits = [task for task in self.corpus["tasks"] if task["category"] == "edit_chain"]
        self.assertEqual(len(edits), 20)
        for task in edits:
            self.assertIn(len(task["stages"]), {2, 3})
            for previous, current in zip(task["stages"], task["stages"][1:]):
                previous_checks = {check["id"]: check for check in previous["oracle"]["checks"]}
                current_checks = {check["id"]: check for check in current["oracle"]["checks"]}
                preserve = current["oracle"]["preserve_check_ids"]
                self.assertTrue(preserve)
                self.assertTrue(set(preserve) <= set(previous_checks) & set(current_checks))
                for check_id in preserve:
                    self.assertEqual(previous_checks[check_id], current_checks[check_id])
                self.assertRegex(current["request"].casefold(), r"retain|keep|preserve")

    def test_rejects_empty_and_malformed_corpora(self) -> None:
        empty = {"schema_version": 1, "tasks": []}
        with self.assertRaisesRegex(CorpusValidationError, "nonempty"):
            validate_corpus(empty)

        malformed = copy.deepcopy(self.corpus)
        del malformed["tasks"][0]["title"]
        with self.assertRaisesRegex(CorpusValidationError, "missing keys.*title"):
            validate_corpus(malformed)

    def test_rejects_duplicate_ids_and_requests(self) -> None:
        duplicate_task = copy.deepcopy(self.corpus)
        duplicate_task["tasks"][-1]["id"] = duplicate_task["tasks"][0]["id"]
        with self.assertRaisesRegex(CorpusValidationError, "duplicate task ID"):
            validate_corpus(duplicate_task)

        duplicate_stage = copy.deepcopy(self.corpus)
        edit = next(task for task in duplicate_stage["tasks"] if task["category"] == "edit_chain")
        edit["stages"][1]["id"] = edit["stages"][0]["id"]
        with self.assertRaisesRegex(CorpusValidationError, "duplicate stage ID"):
            validate_corpus(duplicate_stage)

        duplicate_check = copy.deepcopy(self.corpus)
        task = duplicate_check["tasks"][0]
        checks = task["stages"][0]["oracle"]["checks"]
        checks.append(copy.deepcopy(checks[0]))
        with self.assertRaisesRegex(CorpusValidationError, "duplicate check ID"):
            validate_corpus(duplicate_check)

        duplicate_asset = copy.deepcopy(self.corpus)
        carrier = next(task for task in duplicate_asset["tasks"] if task["id"] == "part_fan_carrier_001")
        assets = carrier["stages"][0]["input_assets"]
        assets.append(copy.deepcopy(assets[0]))
        with self.assertRaisesRegex(CorpusValidationError, "duplicate asset ID"):
            validate_corpus(duplicate_asset)

        duplicate_request = copy.deepcopy(self.corpus)
        duplicate_request["tasks"][1]["stages"][0]["request"] = duplicate_request["tasks"][0]["stages"][0]["request"]
        with self.assertRaisesRegex(CorpusValidationError, "exact request text is duplicated"):
            validate_corpus(duplicate_request)

    def test_rejects_nonfinite_values_and_json_constants(self) -> None:
        bad_value = copy.deepcopy(self.corpus)
        bad_value["tasks"][0]["stages"][0]["oracle"]["checks"][0]["ranges"][0][0] = float("nan")
        with self.assertRaisesRegex(CorpusValidationError, "finite"):
            validate_corpus(bad_value)

        path = ASSETS_DIR / f".invalid-json-{os.getpid()}.json"
        try:
            path.write_text('{"schema_version": NaN, "tasks": []}', encoding="utf-8")
            with self.assertRaisesRegex(CorpusValidationError, "non-finite JSON number"):
                load_corpus(path, corpus_dir=CORPUS_DIR)
        finally:
            path.unlink(missing_ok=True)

    def test_rejects_malformed_reference_parameters(self) -> None:
        malformed = copy.deepcopy(self.corpus)
        flange = next(
            task for task in malformed["tasks"]
            if task.get("reference", {}).get("factory") == "counterbore_flange"
        )
        params = next(iter(flange["reference"]["stages"].values()))
        params["bolt_centers_mm"][0] = [1.0, 2.0, 3.0]
        with self.assertRaisesRegex(CorpusValidationError, "two-number X/Y point"):
            validate_corpus(malformed)

    def test_rejects_traversal_absolute_and_escaping_symlink_assets(self) -> None:
        for unsafe in ("assets/../PLAN.md", "/etc/passwd", "C:\\outside.stl"):
            bad_path = copy.deepcopy(self.corpus)
            stage = next(task for task in bad_path["tasks"] if task["id"] == "part_fan_carrier_001")["stages"][0]
            stage["input_assets"][0]["path"] = unsafe
            with self.subTest(path=unsafe), self.assertRaises(CorpusValidationError):
                validate_corpus(bad_path)

        link_path = ASSETS_DIR / f".escaping-link-{os.getpid()}.stl"
        target = REPOSITORY / "PLAN.md"
        try:
            link_path.symlink_to(target)
            bad_link = copy.deepcopy(self.corpus)
            stage = next(task for task in bad_link["tasks"] if task["id"] == "part_fan_carrier_001")["stages"][0]
            stage["input_assets"][0] = {
                "id": "fan_envelope",
                "path": f"assets/{link_path.name}",
                "format": "stl",
                "sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
            }
            with self.assertRaisesRegex(CorpusValidationError, "escapes"):
                validate_corpus(bad_link)
        finally:
            link_path.unlink(missing_ok=True)

    def test_rejects_incorrect_preservation_ids_and_changed_retained_checks(self) -> None:
        bad_id = copy.deepcopy(self.corpus)
        edit = next(task for task in bad_id["tasks"] if task["category"] == "edit_chain")
        edit["stages"][1]["oracle"]["preserve_check_ids"] = ["not_a_current_check"]
        with self.assertRaisesRegex(CorpusValidationError, "not current-stage checks"):
            validate_corpus(bad_id)

        changed = copy.deepcopy(self.corpus)
        edit = next(task for task in changed["tasks"] if task["category"] == "edit_chain")
        retained_id = next(check["id"] for check in edit["stages"][1]["oracle"]["checks"] if check["type"] == "solids")
        retained = next(check for check in edit["stages"][1]["oracle"]["checks"] if check["id"] == retained_id)
        retained["equals"] += 1
        with self.assertRaisesRegex(CorpusValidationError, "changed definition"):
            validate_corpus(changed)

    def test_builds_every_declared_reference_control(self) -> None:
        from OCP.BRepBndLib import BRepBndLib
        from OCP.Bnd import Bnd_Box
        from OCP.BRepClass3d import BRepClass3d_SolidClassifier
        from OCP.TopExp import TopExp_Explorer
        from OCP.TopAbs import TopAbs_SOLID, TopAbs_IN, TopAbs_ON
        from OCP.gp import gp_Pnt
        from ezdxf.bbox import extents

        built, unsupported = build_all_references(self.corpus)
        expected_unsupported = {task["id"] for task in self.corpus["tasks"] if task["category"] == "drawing"}
        self.assertEqual({task_id for task_id, _, _ in unsupported}, expected_unsupported)
        self.assertTrue(all("NotImplementedError" in reason for _, _, reason in unsupported))
        self.assertEqual(len(built), 105)
        task_map = {task["id"]: task for task in self.corpus["tasks"]}
        for task_id, stage_id, artifact in built:
            with self.subTest(task=task_id, stage=stage_id):
                stage = next(stage for stage in task_map[task_id]["stages"] if stage["id"] == stage_id)
                if hasattr(artifact, "is_valid"):
                    result = artifact.is_valid
                    if callable(result):
                        result = result()
                    self.assertTrue(result, f"invalid control: {task_id}/{stage_id}")
                    box = Bnd_Box()
                    BRepBndLib.AddOptimal_s(artifact.wrapped, box, False, False)
                    lo, hi = box.CornerMin(), box.CornerMax()
                    measured = [hi.X() - lo.X(), hi.Y() - lo.Y(), hi.Z() - lo.Z()]
                    solids = []
                    explorer = TopExp_Explorer(artifact.wrapped, TopAbs_SOLID)
                    while explorer.More():
                        solids.append(explorer.Current())
                        explorer.Next()
                    for check in stage["oracle"]["checks"]:
                        if check["type"] == "bbox_mm":
                            self.assertTrue(all(a <= value <= b for value, (a, b) in zip(measured, check["ranges"])), f"{check['id']}: {measured}")
                        elif check["type"] == "solids":
                            self.assertEqual(len(solids), check["equals"])
                        elif check["type"] == "point_material":
                            states = [BRepClass3d_SolidClassifier(solid, gp_Pnt(*check["point_mm"]), 1e-7).State() for solid in solids]
                            actual = "solid" if TopAbs_IN in states else "boundary" if TopAbs_ON in states else "void"
                            self.assertEqual(actual, check["equals"], f"{check['id']}: {check['point_mm']}")
                elif hasattr(artifact, "audit"):
                    audit = artifact.audit()
                    self.assertEqual(audit.errors, [], f"DXF audit errors: {task_id}/{stage_id}")
                    measured = extents(artifact.modelspace()).size
                    ranges = stage["oracle"]["checks"][0]["extents_mm"]
                    self.assertTrue(all(a <= value <= b for value, (a, b) in zip((measured.x, measured.y), ranges)), f"DXF size: {measured}")

    def test_artifact_coverage_and_drawing_placeholders_cannot_self_certify(self) -> None:
        for task in self.corpus["tasks"]:
            for stage in task["stages"]:
                if stage["expected_outcome"] == "artifact":
                    self.assertEqual(stage["oracle"]["coverage"], "bounded")

    def test_profile_extent_ranges_are_sizes_from_public_dimensions(self) -> None:
        profiles = {task["id"]: task for task in self.corpus["tasks"] if task["category"] == "2d_profile"}
        for task_id, expected in (("profile_round_plate_001", [80, 80]), ("profile_t_plate_005", [100, 70]), ("profile_tab_010", [90, 60])):
            check = profiles[task_id]["stages"][0]["oracle"]["checks"][0]
            for pair, dimension in zip(check["extents_mm"], expected):
                self.assertAlmostEqual(sum(pair) / 2, dimension)

    def test_width_edit_retains_bores_without_retaining_old_width(self):
        task = next(task for task in self.corpus["tasks"] if task["id"] == "edit_plate_widen_001")
        initial, changed = task["stages"]
        before = next(check for check in initial["oracle"]["checks"] if check["type"] == "bbox_mm")
        after = next(check for check in changed["oracle"]["checks"] if check["type"] == "bbox_mm")
        self.assertEqual(before["ranges"][0], [79.9, 80.1])
        self.assertEqual(after["ranges"][0], [99.9, 100.1])
        self.assertNotIn(before["id"], changed["oracle"]["preserve_check_ids"])
        self.assertIn("mounting_bores", changed["oracle"]["preserve_check_ids"])


if __name__ == "__main__":
    unittest.main()
