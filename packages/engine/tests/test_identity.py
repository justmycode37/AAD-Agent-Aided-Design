from __future__ import annotations

import copy
import hashlib
import json
import subprocess
import sys
import unittest
from decimal import Decimal
from pathlib import Path

from aad.engine.identity import cache_identity


def inputs() -> dict:
    return {
        "source": "from aad import *\r\nresult = Box(60, 40, 6)\n",
        "applied_params": {
            "width": 60.0,
            "spacing": [46.0, 26.0],
            "profile": {"closed": True, "label": "π"},
        },
        "spec": {
            "units": "mm",
            "requirements": [
                {"id": "size", "bbox": {"x": 60, "tolerance": 0.1}},
                {"id": "holes", "holes": {"count": 4}},
            ],
            "policy": {"warnings": True},
        },
        "kind": "part",
        "units": "mm",
        "material": "steel_1018",
        "process": "cnc_3axis",
        "kernel_versions": {"build123d": "0.13.0", "occt": "8.0.1.1.0"},
        "tolerances": {"linear": 0.001, "angular": 0.1},
        "asset_sha256": {
            "motor.step": hashlib.sha256(b"staged motor v1").hexdigest(),
            "bolt.step": hashlib.sha256(b"staged bolt").hexdigest(),
        },
        "pipeline_identity": "sha256:build-manifest-1",
    }


def reverse_objects(value):
    if type(value) is dict:
        return {key: reverse_objects(item) for key, item in reversed(list(value.items()))}
    if type(value) is list:
        return [reverse_objects(item) for item in value]
    return value


class CacheIdentityTests(unittest.TestCase):
    def test_golden_record_and_encoding(self) -> None:
        key, record = cache_identity(
            source="r = 'π'\r\n",
            applied_params={"z": [1, True, None], "a": -0.0},
            spec={"requirements": [{"id": "size", "equals": 2.5}]},
            kind="part",
            units="mm",
            material=None,
            process="cnc",
            kernel_versions={"occt": "8", "build123d": "0.13"},
            tolerances={"linear": 0.001},
            asset_sha256={"x.step": "a" * 64},
            pipeline_identity="build:1",
        )
        expected = (
            '{"applied_params":{"a":-0.0,"z":[1,true,null]},'
            '"asset_sha256":{"x.step":"' + "a" * 64 + '"},'
            '"kernel_versions":{"build123d":"0.13","occt":"8"},'
            '"kind":"part","material":null,"pipeline_identity":"build:1",'
            '"process":"cnc","schema":"aad.cache-identity.v1",'
            '"source":"r = \'π\'\\r\\n",'
            '"spec":{"requirements":[{"equals":2.5,"id":"size"}]},'
            '"tolerances":{"linear":0.001},"units":"mm"}'
        ).encode("utf-8")
        encoded = json.dumps(
            record, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        self.assertEqual(encoded, expected)
        self.assertEqual(key, hashlib.sha256(expected).hexdigest())
        self.assertEqual(key, "1920f66c078c89fa7167c4f1db17ca4898e4c44314c63aeaed7ad26a3bf7413e")

    def test_every_bound_input_changes_the_key(self) -> None:
        original = inputs()
        original_key, _ = cache_identity(**original)
        variations = {
            "source": original["source"] + " ",
            "applied_params": {**original["applied_params"], "width": 61.0},
            "spec": {**original["spec"], "custom_extension": {"limit": 7}},
            "kind": "2d",
            "units": "in",
            "material": None,
            "process": "fdm_pla",
            "kernel_versions": {**original["kernel_versions"], "occt": "8.0.2"},
            "tolerances": {**original["tolerances"], "linear": 0.002},
            "asset_sha256": {
                **original["asset_sha256"],
                "motor.step": hashlib.sha256(b"staged motor v2").hexdigest(),
            },
            "pipeline_identity": "sha256:build-manifest-2",
        }
        for field, value in variations.items():
            with self.subTest(field=field):
                modified = copy.deepcopy(original)
                modified[field] = copy.deepcopy(value)
                self.assertNotEqual(original_key, cache_identity(**modified)[0])

    def test_complete_spec_and_asset_names_are_bound(self) -> None:
        original = inputs()
        key, _ = cache_identity(**original)
        changed_spec = copy.deepcopy(original)
        changed_spec["spec"]["requirements"][0]["bbox"]["tolerance"] = 0.2
        self.assertNotEqual(key, cache_identity(**changed_spec)[0])
        renamed_asset = copy.deepcopy(original)
        renamed_asset["asset_sha256"]["new-name.step"] = renamed_asset["asset_sha256"].pop("motor.step")
        self.assertNotEqual(key, cache_identity(**renamed_asset)[0])
        no_assets = copy.deepcopy(original)
        no_assets["asset_sha256"] = {}
        self.assertNotEqual(key, cache_identity(**no_assets)[0])

    def test_reordered_objects_have_the_same_key_and_record(self) -> None:
        original = inputs()
        reordered = reverse_objects(original)
        self.assertNotEqual(list(original["spec"]), list(reordered["spec"]))
        key, record = cache_identity(**original)
        self.assertEqual((key, record), cache_identity(**reordered))

        def assert_sorted(value):
            if type(value) is dict:
                self.assertEqual(list(value), sorted(value))
                for item in value.values():
                    assert_sorted(item)
            elif type(value) is list:
                for item in value:
                    assert_sorted(item)

        assert_sorted(record)

    def test_source_and_array_order_are_preserved(self) -> None:
        original = inputs()
        key, record = cache_identity(**original)
        self.assertEqual(record["source"], original["source"])
        for field, path in (
            ("applied_params", "spacing"),
            ("spec", "requirements"),
        ):
            with self.subTest(field=field):
                modified = copy.deepcopy(original)
                modified[field][path].reverse()
                self.assertNotEqual(key, cache_identity(**modified)[0])
        for source in (original["source"].replace("\r\n", "\n"), original["source"].rstrip("\n")):
            with self.subTest(source=source):
                modified = copy.deepcopy(original)
                modified["source"] = source
                self.assertNotEqual(key, cache_identity(**modified)[0])

        composed = copy.deepcopy(original)
        decomposed = copy.deepcopy(original)
        composed["source"] = "# é\n"
        decomposed["source"] = "# e\N{COMBINING ACUTE ACCENT}\n"
        self.assertNotEqual(cache_identity(**composed)[0], cache_identity(**decomposed)[0])

    def test_json_number_types_and_signed_zero_remain_distinct(self) -> None:
        keys = []
        for value in (True, 1, 1.0, 0.0, -0.0):
            modified = inputs()
            modified["applied_params"] = {"value": value}
            keys.append(cache_identity(**modified)[0])
        self.assertEqual(len(set(keys)), len(keys))

    def test_record_is_a_detached_copy(self) -> None:
        original = inputs()
        saved = copy.deepcopy(original)
        key, record = cache_identity(**original)
        original["spec"]["requirements"][0]["id"] = "changed"
        self.assertEqual(record["spec"]["requirements"][0]["id"], "size")
        record["applied_params"]["spacing"][0] = -1
        self.assertEqual(original["applied_params"]["spacing"][0], 46.0)
        self.assertEqual(key, cache_identity(**saved)[0])

    def test_all_fields_must_be_supplied_and_pipeline_is_nonblank(self) -> None:
        original = inputs()
        for field in original:
            with self.subTest(missing=field):
                incomplete = copy.deepcopy(original)
                del incomplete[field]
                with self.assertRaises(TypeError):
                    cache_identity(**incomplete)
        for bad in (None, "", " \t\n", 0, b"build"):
            with self.subTest(pipeline_identity=bad):
                modified = inputs()
                modified["pipeline_identity"] = bad
                with self.assertRaises((TypeError, ValueError)):
                    cache_identity(**modified)

    def test_invalid_scalar_and_mapping_fields_fail(self) -> None:
        bad_fields = {
            "source": None,
            "kind": " ",
            "units": "",
            "material": "",
            "process": 3,
            "applied_params": [],
            "spec": None,
            "kernel_versions": {},
            "tolerances": [],
            "asset_sha256": [],
        }
        for field, value in bad_fields.items():
            with self.subTest(field=field):
                modified = inputs()
                modified[field] = value
                with self.assertRaises((TypeError, ValueError)):
                    cache_identity(**modified)

        for versions in ({"": "1"}, {"kernel": " "}, {"kernel": 1}, {1: "1"}):
            with self.subTest(kernel_versions=versions):
                modified = inputs()
                modified["kernel_versions"] = versions
                with self.assertRaises((TypeError, ValueError)):
                    cache_identity(**modified)

    def test_asset_digests_are_complete_lowercase_sha256_strings(self) -> None:
        for assets in (
            {"": "a" * 64},
            {" ": "a" * 64},
            {1: "a" * 64},
            {"x": "a" * 63},
            {"x": "a" * 65},
            {"x": "A" * 64},
            {"x": "g" * 64},
            {"x": None},
        ):
            with self.subTest(assets=assets):
                modified = inputs()
                modified["asset_sha256"] = assets
                with self.assertRaises((TypeError, ValueError)):
                    cache_identity(**modified)

    def test_nonfinite_numbers_fail_at_any_depth(self) -> None:
        for field in ("applied_params", "spec", "tolerances"):
            for number in (float("nan"), float("inf"), float("-inf")):
                with self.subTest(field=field, number=number):
                    modified = inputs()
                    modified[field] = {"nested": [0, {"number": number}]}
                    with self.assertRaisesRegex(ValueError, "finite"):
                        cache_identity(**modified)

    def test_non_json_values_and_nonstring_object_keys_fail(self) -> None:
        class IntSubclass(int):
            pass

        class DictSubclass(dict):
            pass

        for bad in (b"bytes", (1, 2), {1, 2}, Decimal("1.2"), object(), IntSubclass(1), DictSubclass()):
            with self.subTest(value=type(bad).__name__):
                modified = inputs()
                modified["applied_params"] = {"nested": [bad]}
                with self.assertRaises(TypeError):
                    cache_identity(**modified)
        for bad_key in (1, True, None, 1.5):
            with self.subTest(key=bad_key):
                modified = inputs()
                modified["spec"] = {"nested": {bad_key: "value"}}
                with self.assertRaises(TypeError):
                    cache_identity(**modified)

    def test_invalid_unicode_cycles_and_shared_containers_fail(self) -> None:
        for field, value in (
            ("source", "\ud800"),
            ("spec", {"bad": "\udfff"}),
            ("applied_params", {"\ud800": 1}),
        ):
            with self.subTest(field=field):
                modified = inputs()
                modified[field] = value
                with self.assertRaises(ValueError):
                    cache_identity(**modified)

        cyclic_list = []
        cyclic_list.append(cyclic_list)
        cyclic_dict = {}
        cyclic_dict["self"] = cyclic_dict
        shared = []
        for value in (cyclic_list, cyclic_dict, {"a": shared, "b": shared}):
            with self.subTest(container=type(value).__name__):
                modified = inputs()
                modified["applied_params"] = {"value": value}
                with self.assertRaisesRegex(ValueError, "shared or cyclic"):
                    cache_identity(**modified)

        modified = inputs()
        modified["spec"]["alias"] = modified["applied_params"]
        with self.assertRaisesRegex(ValueError, "shared or cyclic"):
            cache_identity(**modified)

    def test_import_and_call_never_load_a_kernel(self) -> None:
        source_dir = Path(__file__).resolve().parents[1] / "src"
        script = """
import sys
sys.path.insert(0, sys.argv[1])

class BlockKernel:
    def find_spec(self, fullname, path=None, target=None):
        if fullname.partition('.')[0] in {'build123d', 'OCP'}:
            raise AssertionError(f'kernel import attempted: {fullname}')
        return None

sys.meta_path.insert(0, BlockKernel())
from aad.engine.identity import cache_identity
key, record = cache_identity(
    source='', applied_params={}, spec={}, kind='part', units='mm',
    material=None, process=None, kernel_versions={'kernel': '1'},
    tolerances={}, asset_sha256={}, pipeline_identity='build:1',
)
assert len(key) == 64 and record['source'] == ''
assert not any(name.partition('.')[0] in {'build123d', 'OCP'} for name in sys.modules)
assert 'aad.engine.revisions' not in sys.modules
"""
        result = subprocess.run(
            [sys.executable, "-I", "-B", "-c", script, str(source_dir)],
            capture_output=True,
            text=True,
            timeout=10,
        )
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
