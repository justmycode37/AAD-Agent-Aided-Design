from __future__ import annotations

import json
import math
import os
import struct
import subprocess
import sys
import tempfile
import threading
import unittest
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest import mock
from xml.etree import ElementTree

import build123d as bd
from pygltflib import GLTF2

from aiad.engine import fileio
from aiad.engine.fileio import FileIOError, export, import_file
from aiad.engine.types import FeatureInfo, ModelContext


class FileIOTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.job = Path(self.temp.name)
        self.shape = bd.Box(2, 3, 4)

    def context(self, units: str = "mm", shape: object | None = None) -> ModelContext:
        return ModelContext(shape=shape if shape is not None else self.shape, units=units, job_dir=self.job)

    def assert_size(self, shape: object, expected: tuple[float, float, float], places: int = 5) -> None:
        for actual, want in zip(shape.bounding_box().size, expected):
            self.assertAlmostEqual(actual, want, places=places)

    def test_module_import_is_kernel_free(self) -> None:
        code = (
            "import sys; import aiad.engine.fileio; "
            "assert 'build123d' not in sys.modules; assert 'OCP' not in sys.modules"
        )
        subprocess.run([sys.executable, "-c", code], check=True, env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})

    def test_step_both_schemas_and_physical_volume_in_both_units(self) -> None:
        # A cylindrical cut exercises curved B-rep geometry, not just a box.
        shape = bd.Box(2, 3, 4) - bd.Cylinder(0.3, 6)
        for units in ("mm", "in"):
            for schema, header in (("AP214", "AUTOMOTIVE_DESIGN"), ("AP242", "AP242_MANAGED_MODEL")):
                with self.subTest(units=units, schema=schema):
                    path = self.job / f"{schema}-{units}.step"
                    self.assertEqual(export(self.context(units, shape), "step", path, schema=schema), path.resolve())
                    self.assertIn(header, path.read_text(encoding="ascii"))
                    actual = bd.import_step(path)
                    self.assertTrue(actual.is_valid)
                    self.assertEqual(len(actual.solids()), 1)
                    multiplier = 25.4 if units == "in" else 1.0
                    self.assert_size(actual, tuple(v * multiplier for v in (2, 3, 4)))
                    self.assertAlmostEqual(actual.volume / (shape.volume * multiplier**3), 1.0, places=8)

    def test_step_volume_tolerance_measures_written_file_and_preserves_existing_file(self) -> None:
        path = self.job / "delivery.step"
        previous = b"previous deliverable"
        path.write_bytes(previous)
        original_write = fileio._write_step

        def write_larger(shape: object, destination: Path, units: str, schema: str) -> None:
            original_write(shape.scale(1.1), destination, units, schema)

        # This writes a real, readable STEP with 33.1% more physical volume.
        with mock.patch.object(fileio, "_write_step", side_effect=write_larger):
            with self.assertRaises(FileIOError) as failed:
                export(self.context("in"), "step", path, step_volume_tolerance=0.01)
            self.assertEqual(failed.exception.phase, "validate")
            self.assertIn("physical volume drift", str(failed.exception))
            self.assertEqual(path.read_bytes(), previous)
            self.assertEqual(list(self.job.iterdir()), [path])
            export(self.context("in"), "step", path, step_volume_tolerance=0.34)
        read_back = bd.import_step(path)
        self.assertAlmostEqual(read_back.volume / (self.shape.volume * 25.4**3), 1.1**3, places=8)

    def test_step_settings_are_restored_and_import_is_millimetres(self) -> None:
        from OCP.Interface import Interface_Static
        from OCP.STEPControl import STEPControl_Controller

        STEPControl_Controller.Init_s()
        keys = ("write.step.schema", "xstep.cascade.unit", "write.step.unit")
        previous = {key: Interface_Static.CVal_s(key) for key in keys}
        try:
            self.assertTrue(Interface_Static.SetCVal_s("write.step.schema", "AP242DIS"))
            self.assertTrue(Interface_Static.SetCVal_s("xstep.cascade.unit", "INCH"))
            path = export(self.context(), "step", self.job / "test.step", schema="AP214")
            imported = import_file(path, self.job, units="mm")
            self.assert_size(imported.context.shape, (2, 3, 4))
            self.assertEqual(Interface_Static.CVal_s("write.step.schema"), "AP242DIS")
            self.assertEqual(Interface_Static.CVal_s("xstep.cascade.unit"), "INCH")
            self.assertEqual(Interface_Static.CVal_s("write.step.unit"), previous["write.step.unit"])
        finally:
            for key, value in previous.items():
                Interface_Static.SetCVal_s(key, value)

    def test_import_step_with_an_embedded_inch_unit(self) -> None:
        from OCP.Interface import Interface_Static
        from OCP.STEPControl import STEPControl_Controller

        STEPControl_Controller.Init_s()
        previous = Interface_Static.CVal_s("write.step.unit")
        path = self.job / "inch-source.step"
        try:
            self.assertTrue(Interface_Static.SetCVal_s("write.step.unit", "INCH"))
            self.assertTrue(bd.export_step(self.shape, path, unit=bd.Unit.IN))
        finally:
            Interface_Static.SetCVal_s("write.step.unit", previous)
        self.assertIn("CONVERSION_BASED_UNIT('INCH'", path.read_text(encoding="ascii"))
        millimetres = import_file(path, self.job, units="mm")
        inches = import_file(path, self.job, units="in")
        self.assert_size(millimetres.context.shape, tuple(value * 25.4 for value in (2, 3, 4)))
        self.assert_size(inches.context.shape, (2, 3, 4))
        self.assertAlmostEqual(millimetres.context.shape.volume / inches.context.shape.volume, 25.4**3, places=6)

    def test_step_assembly_keeps_both_solids(self) -> None:
        first = bd.Box(2, 3, 4)
        second = bd.Box(1, 1, 1).moved(bd.Location((8, 0, 0)))
        assembly = bd.Compound(children=[first, second])
        path = export(self.context(shape=assembly), "step", self.job / "assembly.step", schema="AP242")
        imported = bd.import_step(path)
        self.assertEqual(len(imported.solids()), 2)
        self.assertAlmostEqual(imported.volume, 25.0, places=7)

    def test_step_nested_assemblies_use_all_native_solid_volumes(self) -> None:
        nested = bd.Compound(children=[bd.Compound(children=[bd.Box(2, 3, 4)])])
        self.assertEqual(len(nested.solids()), 1)
        for units, multiplier in (("mm", 1), ("in", 25.4)):
            for schema in ("AP214", "AP242"):
                with self.subTest(units=units, schema=schema):
                    path = export(self.context(units, nested), "step", self.job / f"nested-{units}-{schema}.step", schema=schema)
                    restored = bd.import_step(path)
                    self.assertEqual(len(restored.solids()), 1)
                    volume = math.fsum(solid.volume for solid in restored.solids())
                    self.assertAlmostEqual(volume / multiplier**3, 24, places=7)

    def test_3mf_retains_root_and_child_placements_as_separate_resources(self) -> None:
        parts = [bd.Box(2, 3, 4), bd.Box(1, 2, 1).moved(bd.Location((6, 0, 1)))]
        root = bd.Compound(children=parts).moved(bd.Location((10, -5, 2.5), (0, 0, 90)))
        before = root.location
        for units in ("mm", "in"):
            with self.subTest(units=units):
                path = export(self.context(units, root), "3mf", self.job / f"located-{units}.3mf")
                reader = bd.Mesher()
                restored = bd.Compound(reader.read(path))
                self.assertEqual(len(reader.meshes), 2)
                for actual, expected in zip(restored.bounding_box().min, root.bounding_box().min):
                    self.assertAlmostEqual(actual, expected, places=5)
                for actual, expected in zip(restored.bounding_box().max, root.bounding_box().max):
                    self.assertAlmostEqual(actual, expected, places=5)
                self.assertEqual(root.location, before)

    def test_converted_assembly_reexports_at_the_same_physical_size(self) -> None:
        assembly = bd.Compound(children=[self.shape, bd.Box(1, 1, 1).moved(bd.Location((8, 0, 0)))])
        original = export(self.context(shape=assembly), "step", self.job / "assembly.step", schema="AP242")
        imported = import_file(original, self.job, units="in")
        self.assertIn("flattened the assembly", imported.history_notice)
        self.assertEqual(len(imported.context.shape.solids()), 2)
        expected_mm = tuple(assembly.bounding_box().size)
        self.assert_size(imported.context.shape, tuple(value / 25.4 for value in expected_mm))

        step = export(imported.context, "step", self.job / "converted.step", schema="AP242")
        self.assert_size(bd.import_step(step), expected_mm)

        three_mf = export(imported.context, "3mf", self.job / "converted.3mf")
        reader = bd.Mesher()
        parts = reader.read(three_mf)
        self.assertEqual(reader.model_unit, bd.Unit.IN)
        self.assertEqual(len(parts), 2)
        self.assert_size(bd.Compound(parts), tuple(value / 25.4 for value in expected_mm))

        glb = export(imported.context, "glb", self.job / "converted.glb")
        gltf = GLTF2().load_binary(str(glb))
        positions = [gltf.accessors[p.attributes.POSITION] for mesh in gltf.meshes for p in mesh.primitives]
        for axis, extent in enumerate(expected_mm):
            low = min(accessor.min[axis] for accessor in positions)
            high = max(accessor.max[axis] for accessor in positions)
            self.assertAlmostEqual(high - low, extent / 1000, places=7)

    def test_brep_and_stl_roundtrip_import_convert_actual_coordinates(self) -> None:
        for source_units in ("mm", "in"):
            for fmt in ("step", "brep", "stl"):
                path = export(self.context(source_units), fmt, self.job / f"{source_units}.{fmt}")
                for target_units in ("mm", "in"):
                    with self.subTest(source=source_units, target=target_units, fmt=fmt):
                        opts = {} if fmt == "step" else {"source_units": source_units}
                        result = import_file(path, self.job, units=target_units, name="imported", **opts)
                        factor = (25.4 if source_units == "in" else 1) / (25.4 if target_units == "in" else 1)
                        self.assertEqual(result.context.units, target_units)
                        self.assert_size(result.context.shape, tuple(v * factor for v in (2, 3, 4)))
                        self.assertEqual(len(result.context.features), 1)
                        self.assertIsInstance(result.context.features[0], FeatureInfo)
                        self.assertEqual(result.context.features[0].name, "imported")
                        self.assertTrue(result.context.features[0].faces)
                        self.assertIn("history is unavailable", result.history_notice)
                        if fmt != "stl":
                            self.assertAlmostEqual(result.context.shape.volume, self.shape.volume * factor**3, places=5)
                        else:
                            self.assertEqual(len(result.context.shape.solids()), 0)
                            self.assertIn("triangulated reference", result.history_notice)

    def test_unitless_assumption_and_ascii_stl_import_are_explicit(self) -> None:
        for fmt in ("brep", "stl"):
            with self.subTest(fmt=fmt):
                path = self.job / f"assumed.{fmt}"
                if fmt == "brep":
                    bd.export_brep(self.shape, path)
                else:
                    bd.export_stl(self.shape, path, ascii_format=True)
                result = import_file(path, self.job, units="in", role="reference")
                self.assertTrue(result.source_units_assumed)
                self.assertEqual(result.source_units, "mm")
                self.assertEqual(result.role, "reference")
                self.assertIn("assumed to use mm", result.history_notice)
                self.assert_size(result.context.shape, tuple(v / 25.4 for v in (2, 3, 4)))
        step = export(self.context(), "step", self.job / "units.step")
        with self.assertRaisesRegex(FileIOError, "STEP units are read from the file"):
            import_file(step, self.job, source_units="in")

    def test_unit_conversion_scales_position_as_well_as_size(self) -> None:
        translated = self.shape.moved(bd.Location((10, -5, 2.5)))
        for source_units, target_units, factor in (("mm", "in", 1 / 25.4), ("in", "mm", 25.4)):
            for fmt in ("step", "brep", "stl"):
                with self.subTest(source=source_units, target=target_units, fmt=fmt):
                    path = export(self.context(source_units, translated), fmt, self.job / f"translated-{source_units}.{fmt}")
                    opts = {} if fmt == "step" else {"source_units": source_units}
                    imported = import_file(path, self.job, units=target_units, **opts).context.shape
                    bounds = imported.bounding_box()
                    for low, high, center in zip(bounds.min, bounds.max, (10, -5, 2.5)):
                        self.assertAlmostEqual((low + high) / 2, center * factor, places=5)
                    self.assert_size(imported, tuple(value * factor for value in (2, 3, 4)))

    def test_binary_stl_is_readable_by_occt(self) -> None:
        path = export(self.context("in"), "stl", self.job / "part.stl")
        data = path.read_bytes()
        triangles = struct.unpack_from("<I", data, 80)[0]
        self.assertGreater(triangles, 0)
        self.assertEqual(len(data), 84 + triangles * 50)
        self.assert_size(bd.import_stl(path), (2, 3, 4))

    def mesh_triangle_count(self, path: Path, fmt: str) -> int:
        if fmt == "stl":
            return struct.unpack_from("<I", path.read_bytes(), 80)[0]
        if fmt == "3mf":
            reader = bd.Mesher()
            reader.read(path)
            return sum(reader.triangle_counts)
        gltf = GLTF2().load_binary(str(path))
        return sum(gltf.accessors[p.indices].count // 3 for mesh in gltf.meshes for p in mesh.primitives)

    def test_mesh_deflection_uses_physical_coordinate_units_for_every_format(self) -> None:
        for fmt in ("stl", "3mf", "glb"):
            counts = []
            for units, scale in (("mm", 1), ("in", 25.4)):
                with self.subTest(fmt=fmt, units=units):
                    shape = bd.Cylinder(25.4 / scale, 50.8 / scale)
                    path = export(self.context(units, shape), fmt, self.job / f"physical-{units}.{fmt}",
                                  tolerance=0.1 / scale, angular_tolerance=2.0)
                    counts.append(self.mesh_triangle_count(path, fmt))
            self.assertGreater(counts[0], 0)
            self.assertEqual(counts[0], counts[1])

    def test_stl_circle_chords_meet_configured_absolute_deflection(self) -> None:
        radius = 25.4
        path = export(self.context(shape=bd.Cylinder(radius, 50.8)), "stl", self.job / "chords.stl",
                      tolerance=0.1, angular_tolerance=2.0)
        data = path.read_bytes()
        count = struct.unpack_from("<I", data, 80)[0]
        sagittas = []
        for index in range(count):
            values = struct.unpack_from("<12fH", data, 84 + 50 * index)
            points = [values[start:start + 3] for start in (3, 6, 9)]
            if max(p[2] for p in points) - min(p[2] for p in points) < 1e-5:
                continue
            for a, b in zip(points, points[1:] + points[:1]):
                if abs(a[2] - b[2]) < 1e-5:
                    chord = math.dist(a, b)
                    sagittas.append(radius - math.sqrt(max(0, radius**2 - chord**2 / 4)))
        self.assertTrue(sagittas)
        self.assertLessEqual(max(sagittas), 0.1)

    def test_mesh_export_honors_tighter_angle_and_preserves_source_cache(self) -> None:
        from OCP.BRep import BRep_Tool
        from OCP.TopLoc import TopLoc_Location

        shape = bd.Cylinder(10, 20)
        _, coarse = shape.tessellate(10, 2.0)
        def source_counts():
            return [BRep_Tool.Triangulation_s(f.wrapped, TopLoc_Location()).NbTriangles() for f in shape.faces()]
        original_counts = source_counts()
        for fmt in ("stl", "3mf", "glb"):
            with self.subTest(fmt=fmt):
                path = export(self.context(shape=shape), fmt, self.job / f"angular.{fmt}",
                              tolerance=10, angular_tolerance=0.05)
                self.assertGreater(self.mesh_triangle_count(path, fmt), len(coarse))
                self.assertEqual(source_counts(), original_counts)

    def test_3mf_is_readable_and_carries_actual_model_units(self) -> None:
        for units, native in (("mm", bd.Unit.MM), ("in", bd.Unit.IN)):
            with self.subTest(units=units):
                path = export(self.context(units), "3mf", self.job / f"{units}.3mf")
                with zipfile.ZipFile(path) as archive:
                    self.assertIsNone(archive.testzip())
                    root = ElementTree.fromstring(archive.read("3D/3dmodel.model"))
                    self.assertEqual(root.attrib["unit"], "millimeter" if units == "mm" else "inch")
                reader = bd.Mesher()
                shapes = reader.read(path)
                self.assertEqual(reader.model_unit, native)
                self.assertEqual(len(shapes), 1)
                self.assertGreater(reader.triangle_counts[0], 0)
                self.assert_size(shapes[0], (2, 3, 4))
                self.assertAlmostEqual(shapes[0].volume, self.shape.volume, places=5)

    def test_binary_glb_is_parseable_and_coordinates_are_metres(self) -> None:
        for units, metres in (("mm", 0.001), ("in", 0.0254)):
            with self.subTest(units=units):
                path = export(self.context(units), "glb", self.job / f"{units}.glb")
                magic, version, length = struct.unpack("<4sII", path.read_bytes()[:12])
                self.assertEqual((magic, version, length), (b"glTF", 2, path.stat().st_size))
                gltf = GLTF2().load_binary(str(path))
                self.assertEqual(gltf.asset.version, "2.0")
                self.assertTrue(gltf.binary_blob())
                positions = [
                    gltf.accessors[primitive.attributes.POSITION]
                    for mesh in gltf.meshes
                    for primitive in mesh.primitives
                ]
                minimum = [min(accessor.min[axis] for accessor in positions) for axis in range(3)]
                maximum = [max(accessor.max[axis] for accessor in positions) for axis in range(3)]
                for axis, want in enumerate((2, 3, 4)):
                    self.assertAlmostEqual(maximum[axis] - minimum[axis], want * metres, places=7)

    def test_glb_rotates_root_translation_into_the_gltf_coordinate_system(self) -> None:
        shape = self.shape.moved(bd.Location((10, -5, 2.5)))
        before = tuple(shape.location.position)
        for units, metres in (("mm", 0.001), ("in", 0.0254)):
            with self.subTest(units=units):
                path = export(self.context(units, shape), "glb", self.job / f"translated-{units}.glb")
                gltf = GLTF2().load_binary(str(path))
                translation = gltf.nodes[gltf.scenes[gltf.scene].nodes[0]].translation
                # A -90 degree rotation about X maps (x, y, z) to (x, z, -y).
                for actual, expected in zip(translation, (10 * metres, 2.5 * metres, 5 * metres)):
                    self.assertAlmostEqual(actual, expected, places=7)
                self.assertEqual(tuple(shape.location.position), before)

    def test_glb_preserves_a_rotated_and_translated_location(self) -> None:
        from OCP.gp import gp_Pnt

        shape = self.shape.moved(bd.Location((10, -5, 2.5), (25, 35, 45)))
        path = export(self.context("in", shape), "glb", self.job / "rotated.glb")
        gltf = GLTF2().load_binary(str(path))
        node = gltf.nodes[gltf.scenes[gltf.scene].nodes[0]]
        primitive = gltf.meshes[node.mesh].primitives[0]
        accessor = gltf.accessors[primitive.attributes.POSITION]
        view = gltf.bufferViews[accessor.bufferView]
        point = struct.unpack_from("<3f", gltf.binary_blob(), (view.byteOffset or 0) + (accessor.byteOffset or 0))

        def cross(a: tuple[float, ...], b: tuple[float, ...]) -> tuple[float, float, float]:
            return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])

        q = tuple(node.rotation[:3])
        first, second = cross(q, point), cross(q, cross(q, point))
        actual = tuple(point[i] + 2 * (node.rotation[3] * first[i] + second[i]) + node.translation[i] for i in range(3))
        native_point = gp_Pnt(*(value / 0.0254 for value in point))
        axis_rotation = bd.Location((0, 0, 0), (1, 0, 0), -90)
        native_point.Transform((axis_rotation * shape.location).wrapped.Transformation())
        expected = (native_point.X() * 0.0254, native_point.Y() * 0.0254, native_point.Z() * 0.0254)
        for got, want in zip(actual, expected):
            self.assertAlmostEqual(got, want, places=7)

    def test_each_invalid_artifact_and_writer_failure_preserves_previous_file(self) -> None:
        def bad_output(shape: object, fmt: str, path: Path, units: str, options: dict) -> None:
            path.write_bytes(b"incomplete artifact")

        for fmt in ("step", "brep", "stl", "3mf", "glb"):
            with self.subTest(fmt=fmt):
                destination = self.job / f"keep.{fmt}"
                destination.write_bytes(b"old bytes")
                before = set(self.job.iterdir())
                with mock.patch.object(fileio, "_write_export", side_effect=bad_output):
                    with self.assertRaises(FileIOError) as failed:
                        export(self.context(), fmt, destination)
                self.assertEqual(failed.exception.phase, "validate")
                self.assertEqual(destination.read_bytes(), b"old bytes")
                self.assertEqual(set(self.job.iterdir()), before)

        def failed_write(shape: object, fmt: str, path: Path, units: str, options: dict) -> None:
            path.write_bytes(b"partial")
            raise OSError("simulated write failure")

        destination = self.job / "keep.stl"
        with mock.patch.object(fileio, "_write_export", side_effect=failed_write):
            with self.assertRaises(FileIOError) as failed:
                export(self.context(), "stl", destination)
        self.assertEqual(failed.exception.phase, "kernel")
        self.assertEqual(destination.read_bytes(), b"old bytes")

    def test_glb_rejects_nonfinite_vertex_and_out_of_range_index(self) -> None:
        path = export(self.context(), "glb", self.job / "part.glb")
        original = path.read_bytes()
        json_size = struct.unpack_from("<I", original, 12)[0]
        doc = json.loads(original[20 : 20 + json_size])
        bin_start = 20 + json_size + 8
        primitive = doc["meshes"][0]["primitives"][0]

        def location(accessor_index: int) -> tuple[int, dict]:
            accessor = doc["accessors"][accessor_index]
            view = doc["bufferViews"][accessor["bufferView"]]
            return bin_start + view.get("byteOffset", 0) + accessor.get("byteOffset", 0), accessor

        bad = bytearray(original)
        position, _ = location(primitive["attributes"]["POSITION"])
        struct.pack_into("<f", bad, position, math.nan)
        path.write_bytes(bad)
        with self.assertRaisesRegex(ValueError, "non-finite"):
            fileio._validate_glb(path)

        bad = bytearray(original)
        index, accessor = location(primitive["indices"])
        index_format = {5121: "<B", 5123: "<H", 5125: "<I"}[accessor["componentType"]]
        struct.pack_into(index_format, bad, index, 255)
        path.write_bytes(bad)
        with self.assertRaisesRegex(ValueError, "outside its position"):
            fileio._validate_glb(path)

    def test_destinations_are_confined_even_through_symlinks(self) -> None:
        with self.assertRaises(FileIOError):
            export(ModelContext(shape=self.shape, job_dir=Path(self.job.anchor)), "brep", self.job / "root.brep")
        with tempfile.TemporaryDirectory() as outside_name:
            outside = Path(outside_name)
            target = outside / "untouched.brep"
            target.write_bytes(b"outside")
            directory_link = self.job / "escape"
            directory_link.symlink_to(outside, target_is_directory=True)
            file_link = self.job / "linked.brep"
            file_link.symlink_to(target)
            for path in (target, self.job / ".." / target.name, directory_link / "new.brep", file_link):
                with self.subTest(path=path), self.assertRaises(FileIOError):
                    export(self.context(), "brep", path)
            self.assertEqual(target.read_bytes(), b"outside")
            self.assertFalse((outside / "new.brep").exists())
            relative = export(self.context(), "brep", Path("relative.brep"))
            self.assertEqual(relative, (self.job / "relative.brep").resolve())

    def test_concurrent_exports_use_distinct_temporary_siblings(self) -> None:
        destination = self.job / "shared.brep"
        destination.write_bytes(b"old")
        real_write = fileio._write_export
        barrier = threading.Barrier(2)
        temporary_paths: list[Path] = []

        def synchronized_write(shape: object, fmt: str, path: Path, units: str, options: dict) -> None:
            temporary_paths.append(path)
            self.assertEqual(path.parent, destination.resolve().parent)
            self.assertEqual(destination.read_bytes(), b"old")
            barrier.wait(timeout=10)
            real_write(shape, fmt, path, units, options)

        with mock.patch.object(fileio, "_write_export", side_effect=synchronized_write):
            with ThreadPoolExecutor(max_workers=2) as pool:
                calls = [pool.submit(export, self.context(), "brep", destination) for _ in range(2)]
                for call in calls:
                    self.assertEqual(call.result(), destination.resolve())
        self.assertEqual(len(set(temporary_paths)), 2)
        self.assertTrue(all(not path.exists() for path in temporary_paths))
        self.assertAlmostEqual(bd.import_brep(destination).volume, self.shape.volume, places=6)

    def test_invalid_options_and_missing_shape_are_reported(self) -> None:
        path = self.job / "part.step"
        for options in ({"schema": "AP203"}, {"step_volume_tolerance": float("nan")}, {"tolerance": 0}, {"typo": 1}):
            with self.subTest(options=options), self.assertRaises(FileIOError) as failed:
                export(self.context(), "step", path, **options)
            self.assertEqual(failed.exception.code, "EXPORT_FAILED")
            self.assertEqual(failed.exception.phase, "input")
        with self.assertRaisesRegex(FileIOError, "needs a 3D shape"):
            export(ModelContext(job_dir=self.job), "step", path)
        self.assertFalse(path.exists())


if __name__ == "__main__":
    unittest.main()
