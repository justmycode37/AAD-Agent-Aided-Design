from __future__ import annotations

import importlib.util
import json
import math
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from aad.engine.measure import MeasurementError, measure
from aad.engine.types import Entity, FeatureInfo, ModelContext

_HAS_SELECTORS = importlib.util.find_spec('aad.engine.select') is not None


def context(shape, units='mm', owners=None):
    owners = [('stock', shape)] if owners is None else owners
    features = []
    for name, owned in owners:
        faces = [Entity(kind='face', index=i, feature=name) for i, face in enumerate(shape.faces())
                 if any(face.wrapped.IsSame(item.wrapped) for item in owned.faces())]
        edges = [Entity(kind='edge', index=i, feature=name) for i, edge in enumerate(shape.edges())
                 if any(edge.wrapped.IsSame(item.wrapped) for item in owned.edges())]
        features.append(FeatureInfo(name=name, faces=faces, edges=edges))
    return ModelContext(shape=shape, units=units, features=features, metrics={'volume': 999999, 'area': 999999})


class WholeMeasurementTests(unittest.TestCase):
    def test_analytic_box_and_cylinder_ignore_cached_metrics(self):
        import build123d as bd
        for shape, area, volume in ((bd.Box(2, 4, 6), 88, 48), (bd.Cylinder(3, 10), 78 * math.pi, 90 * math.pi)):
            with self.subTest(volume=volume):
                ctx = context(shape)
                surface = measure(ctx, 'area')
                measured = measure(ctx, 'volume')
                self.assertAlmostEqual(surface['value'], area)
                self.assertAlmostEqual(measured['value'], volume)
                self.assertEqual(surface['unit'], 'mm^2')
                self.assertEqual(measured['unit'], 'mm^3')
                self.assertEqual(measured['scope'], 'whole_shape')
                self.assertEqual(measured['entities'], [])
                self.assertEqual(measured['aggregation'], 'sum_of_native_solids')
                json.dumps(measured, allow_nan=False)

    def test_inertia_is_central_world_tensor_after_placement(self):
        import build123d as bd
        shape = bd.Box(2, 4, 6).rotate(bd.Axis.Z, 30).moved(bd.Location((10, -7, 3)))
        ctx = context(shape)
        measured = measure(ctx, 'inertia')
        expected = [[196, 12 * math.sqrt(3), 0], [12 * math.sqrt(3), 172, 0], [0, 0, 80]]
        for row, target in zip(measured['value'], expected, strict=True):
            for value, wanted in zip(row, target, strict=True):
                self.assertAlmostEqual(value, wanted)
        for value, wanted in zip(measured['reference_point'], (10, -7, 3), strict=True):
            self.assertAlmostEqual(value, wanted)
        self.assertEqual(measured['unit'], 'mm^5')
        self.assertEqual(measured['axes'], 'world_xyz')
        self.assertEqual(measured['definition'], 'central_volume_inertia')
        ctx.material = 'unknown-material'
        self.assertEqual(measure(ctx, 'inertia'), measured)

    def test_nested_solid_aggregation_and_cavity(self):
        import build123d as bd
        nested = bd.Compound(children=[bd.Box(2, 4, 6), bd.Compound(children=[bd.Box(2, 3, 4).moved(bd.Location((10, 0, 0)))])])
        ctx = context(nested)
        self.assertAlmostEqual(measure(ctx, 'volume')['value'], 72)
        inertia = measure(ctx, 'inertia')
        self.assertEqual(inertia['solid_count'], 2)
        self.assertAlmostEqual(inertia['reference_point'][0], 10 / 3)
        for index, value in enumerate((258, 1800, 1706)):
            self.assertAlmostEqual(inertia['value'][index][index], value)
        cavity = context(bd.Box(10, 10, 10) - bd.Box(2, 2, 2))
        self.assertAlmostEqual(measure(cavity, 'volume')['value'], 992)
        self.assertAlmostEqual(measure(cavity, 'area')['value'], 624)

    def test_reversed_body_cannot_hide_in_positive_total(self):
        import build123d as bd
        negative = bd.Box(1, 2, 3).solid().moved(bd.Location((10, 0, 0)))
        negative.wrapped.Reverse()
        shape = bd.Compound(children=[bd.Box(2, 3, 4), negative])
        self.assertGreater(sum(solid.volume for solid in shape.solids()), 0)
        for kind in ('volume', 'inertia'):
            with self.subTest(kind=kind), self.assertRaises(MeasurementError) as raised:
                measure(context(shape), kind)
            self.assertEqual(raised.exception.code, 'INVALID_SOLID')
            self.assertEqual(raised.exception.phase, 'validate')
            self.assertIn('signed volume', str(raised.exception))

    def test_physical_mm_in_powers_and_unitless_brep(self):
        import build123d as bd
        box = bd.Box(2, 4, 6)
        mm, inch = context(box), context(box.scale(1 / 25.4), 'in')
        for kind, power in (('area', 2), ('volume', 3)):
            self.assertAlmostEqual(measure(inch, kind)['value'] * 25.4**power, measure(mm, kind)['value'])
        tensor_mm, tensor_in = measure(mm, 'inertia'), measure(inch, 'inertia')
        for i in range(3):
            self.assertAlmostEqual(tensor_in['value'][i][i] * 25.4**5, tensor_mm['value'][i][i])
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'part.brep'
            bd.export_brep(box, path)
            loaded = bd.import_brep(path)
            self.assertAlmostEqual(measure(context(loaded, 'in'), 'volume')['value'], 48)
            self.assertAlmostEqual(measure(context(loaded, 'mm'), 'volume')['value'], 48)

    def test_wrapper_properties_do_not_change_native_volume(self):
        import build123d as bd
        box = bd.Box(2, 4, 6)
        box.solids = lambda: []
        ctx = ModelContext(shape=box, metrics={'volume': 999})
        self.assertAlmostEqual(measure(ctx, 'volume')['value'], 48)

    def test_invalid_inputs_missing_material_and_thickness_fail(self):
        import build123d as bd
        ctx = context(bd.Box(2, 4, 6))
        cases = [('unknown', None, None), ('distance', 'face()', None), ('angle', None, None),
                 ('radius', None, None), ('area', None, 'face()'), ('volume', 'face()', None),
                 ('inertia', None, 'edge()'), ('area', '', None), ('mass', None, None), ('thickness', None, None)]
        for kind, a, b in cases:
            with self.subTest(kind=kind, a=a, b=b), self.assertRaises(MeasurementError) as raised:
                measure(ctx, kind, a, b)
            self.assertEqual(raised.exception.code, 'BAD_INPUT')
        ctx.material = 'steel_1018'
        with self.assertRaisesRegex(MeasurementError, 'density integration is not implemented'):
            measure(ctx, 'mass')
        for units in ('m', None, ['mm']):
            ctx.units = units
            with self.assertRaises(MeasurementError):
                measure(ctx, 'volume')
        with self.assertRaises(MeasurementError):
            measure(ModelContext(), 'volume')

    def test_absent_selector_dependency_fails_explicitly(self):
        import build123d as bd
        with patch.dict(sys.modules, {'aad.engine.select': None}), self.assertRaises(MeasurementError) as raised:
            measure(context(bd.Box(2, 4, 6)), 'area', "face(where='max_z')")
        self.assertEqual(raised.exception.code, 'BAD_INPUT')
        self.assertIn('selector package is not installed', str(raised.exception))

    def test_import_is_kernel_free(self):
        program = "import sys; import aad.engine.measure; assert all(m not in sys.modules for m in ('build123d','OCP','vtk'))"
        subprocess.run([sys.executable, '-c', program], check=True, timeout=10)


@unittest.skipUnless(_HAS_SELECTORS, 'Selected measurements require the engine selector dependency in PR41')
class SelectedMeasurementTests(unittest.TestCase):
    def test_box_distance_area_and_unoriented_face_angle(self):
        import build123d as bd
        ctx = context(bd.Box(2, 4, 6))
        top, bottom = "face(feature='stock', where='max_z')", "face(feature='stock', where='min_z')"
        measured = measure(ctx, 'distance', top, bottom)
        self.assertAlmostEqual(measured['value'], 6)
        self.assertAlmostEqual(math.dist(*measured['closest_points'].values()), 6)
        self.assertEqual(measured['scope'], 'selected_entities')
        self.assertEqual(len(measured['entities']), 2)
        self.assertEqual(measured['entities'][0]['feature'], 'stock')
        self.assertNotIn('_aad_binding', json.dumps(measured, allow_nan=False))
        self.assertAlmostEqual(measure(ctx, 'area', top)['value'], 8)
        self.assertAlmostEqual(measure(ctx, 'angle', top, bottom)['value'], 0)
        self.assertAlmostEqual(measure(ctx, 'angle', top, "face(where='max_x')")['value'], 90)

    def test_rotated_planes_and_lines_use_world_coordinates(self):
        import build123d as bd
        for kind in ('face', 'edge'):
            with self.subTest(kind=kind):
                first = bd.Face.make_rect(4, 4) if kind == 'face' else bd.Edge.make_line((0, 0, 0), (1, 0, 0))
                second = first.rotate(bd.Axis.Y if kind == 'face' else bd.Axis.Z, 30).moved(bd.Location((10, -7, 3)))
                # Create a fresh reference without relying on mutation semantics.
                first = bd.Face.make_rect(4, 4) if kind == 'face' else bd.Edge.make_line((0, 0, 0), (1, 0, 0))
                ctx = context(bd.Compound(children=[first, second]), owners=[('a', first), ('b', second)])
                if kind == 'edge':
                    # Distinguish the line from the context's feature faces.
                    a, b = "edge(feature='a')", "edge(feature='b')"
                else:
                    a, b = "face(feature='a')", "face(feature='b')"
                measured = measure(ctx, 'angle', a, b)
                self.assertAlmostEqual(measured['value'], 30)
                self.assertEqual(measured['unit'], 'deg')

    def test_analytic_radius_and_cap_area_after_placement(self):
        import build123d as bd
        ctx = context(bd.Cylinder(3, 10))
        side = "face(feature='stock', kind='cylinder')"
        top = "face(feature='stock', where='max_z')"
        circle = "edge(feature='stock', kind='circle', where='max_z')"
        self.assertAlmostEqual(measure(ctx, 'radius', side)['value'], 3)
        self.assertAlmostEqual(measure(ctx, 'radius', circle)['value'], 3)
        self.assertAlmostEqual(measure(ctx, 'area', top)['value'], 9 * math.pi)
        ctx = context(bd.Cylinder(3, 10).rotate(bd.Axis.Y, 35).moved(bd.Location((8, -4, 12))))
        self.assertAlmostEqual(measure(ctx, 'radius', side)['value'], 3)
        sphere = context(bd.Sphere(3))
        self.assertAlmostEqual(measure(sphere, 'radius', "face(kind='sphere')")['value'], 3)

    def test_selected_clearance_does_not_assert_body_separation(self):
        import build123d as bd
        outer, inner = bd.Box(10, 10, 10), bd.Box(2, 2, 2)
        ctx = context(bd.Compound(children=[outer, inner]), owners=[('outer', outer), ('inner', inner)])
        measured = measure(ctx, 'clearance', "face(feature='outer', where='max_z')", "face(feature='inner', where='max_z')")
        self.assertAlmostEqual(measured['value'], 4)
        self.assertEqual(measured['definition'], 'minimum_gap_between_selected_entities')
        self.assertEqual(measured['scope'], 'selected_entities')
        self.assertNotIn('clear', measured)
        self.assertAlmostEqual((outer & inner).volume, 8)

    def test_selected_units_and_zero_self_distance(self):
        import build123d as bd
        top, bottom = "face(where='max_z')", "face(where='min_z')"
        inch = context(bd.Box(2 / 25.4, 4 / 25.4, 6 / 25.4), 'in')
        distance = measure(inch, 'distance', top, bottom)
        self.assertEqual(distance['unit'], 'in')
        self.assertAlmostEqual(distance['value'] * 25.4, 6)
        self.assertAlmostEqual(measure(inch, 'area', top)['value'] * 25.4**2, 8)
        self.assertEqual(measure(inch, 'distance', top, top)['value'], 0)

    def test_selector_failures_retain_codes_and_candidates(self):
        import build123d as bd
        ctx = context(bd.Box(2, 4, 6))
        for selector, code in (("face(feature='missing')", 'SELECTOR_EMPTY'), ('face()', 'SELECTOR_AMBIGUOUS')):
            with self.subTest(selector=selector), self.assertRaises(MeasurementError) as raised:
                measure(ctx, 'area', selector)
            error = raised.exception
            self.assertEqual(error.code, code)
            self.assertEqual(error.phase, 'selector')
            self.assertEqual(error.selector, selector)
            self.assertTrue(error.candidates)
            self.assertIs(error.failure.candidates, error.candidates)

    def test_wrong_geometry_is_not_a_successful_zero(self):
        import build123d as bd
        ctx = context(bd.Cylinder(3, 10))
        top = "face(where='max_z')"
        side = "face(kind='cylinder')"
        edge = "edge(kind='circle', where='max_z')"
        for kind, a, b in (('radius', top, None), ('area', edge, None), ('angle', side, top), ('angle', edge, top)):
            with self.subTest(kind=kind, a=a), self.assertRaises(MeasurementError) as raised:
                measure(ctx, kind, a, b)
            self.assertEqual(raised.exception.code, 'BAD_INPUT')
