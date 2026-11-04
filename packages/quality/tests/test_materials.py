from __future__ import annotations

import math
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

from aiad_quality.materials import (
    _validated_density,
    get_material,
    list_materials,
)
from aiad_quality.spec import spec_checks


_REQUIRED_ASSUMPTIONS = {
    "bulk_vs_printed",
    "porosity",
    "moisture",
    "composition",
    "temperature",
    "mass_guarantee",
}


def _context(spec, shape, *, units="mm", material=None):
    return SimpleNamespace(
        spec=spec,
        shape=shape,
        units=units,
        material=material,
        metrics={},
        project_dir=None,
    )


def _actual_mass_g(check) -> float:
    assert check.actual is not None
    return float(check.actual.split(" g from B-rep", 1)[0])


def test_catalog_records_have_sources_definitions_and_finite_positive_density():
    records = list_materials()
    assert {record["id"] for record in records} == {
        "pla",
        "steel_1018",
        "aluminum_6061",
        "stainless_304_4301",
    }

    for record in records:
        assert record["name"].strip()
        assert record["definition"].strip()
        assert record["density_unit"] == "g/cm^3"
        assert math.isfinite(record["density_g_cm3"])
        assert record["density_g_cm3"] > 0
        assert record["source_title"].strip()
        assert record["source_url"].startswith("https://")
        assert record["density_basis"].strip()
        assert record["provenance"].strip()
        assert set(record["assumptions"]) == _REQUIRED_ASSUMPTIONS
        assert all(value.strip() for value in record["assumptions"].values())
        assert "mass" in record["assumptions"]["mass_guarantee"].lower()
        if "additional_sources" in record:
            for source in record["additional_sources"]:
                assert source["title"].strip()
                assert source["url"].startswith("https://")
                assert source["supports"].strip()


def test_contract_ids_are_scoped_to_exact_source_definitions():
    pla = get_material("pla")
    steel = get_material("steel_1018")

    assert pla is not None
    assert "3D700" in pla["definition"]
    assert "not a density claim for every PLA" in pla["definition"]
    assert steel is not None
    assert "AISI 1018" in steel["definition"]
    assert "Fe-0.18 wt% C-0.8 wt% Mn-0.18 wt% Si" in steel["definition"]
    assert "not identify which applies to this row" in steel["density_basis"]


def test_unknown_and_lookalike_ids_are_not_aliases():
    assert get_material("unknown") is None
    assert get_material("PLA") is None
    assert get_material("pla_filled") is None
    assert get_material("steel_1045") is None
    assert get_material(None) is None


def test_get_and_list_materials_return_deep_defensive_copies():
    first = get_material("pla")
    assert first is not None
    first["name"] = "mutated"
    first["assumptions"]["porosity"] = "mutated"
    first["additional_sources"][0]["url"] = "https://invalid.example/"

    second = get_material("pla")
    assert second is not None
    assert second["name"] == "NatureWorks Ingeo 3D700 PLA base resin"
    assert "void-free" in second["assumptions"]["porosity"]
    assert second["additional_sources"][0]["url"].startswith("https://natureworksllc.com/")

    listed = list_materials()
    listed_pla = next(record for record in listed if record["id"] == "pla")
    listed_pla["assumptions"]["composition"] = "mutated"
    assert "base 3D700 resin" in get_material("pla")["assumptions"]["composition"]


@pytest.mark.parametrize(
    "density",
    [0, -1, True, None, "1.24", math.nan, math.inf, -math.inf],
)
def test_density_validation_rejects_nonpositive_nonfinite_or_non_numeric_values(density):
    with pytest.raises(ValueError, match="positive finite density_g_cm3"):
        _validated_density("bad", density)


def test_material_catalog_import_does_not_load_a_cad_kernel():
    quality_src = Path(__file__).resolve().parents[1] / "src"
    environment = os.environ.copy()
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    environment["PYTHONPATH"] = os.pathsep.join(
        filter(None, [str(quality_src), environment.get("PYTHONPATH", "")])
    )
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import sys; import aiad_quality.materials; "
                "assert not any(name == 'build123d' or name.startswith('build123d.') "
                "for name in sys.modules)"
            ),
        ],
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_mass_evaluation_uses_real_brep_volume_and_nominal_pla_density():
    from build123d import Box

    spec = {
        "units": "mm",
        "material": "pla",
        "requirements": [
            {"id": "mass", "mass_g": {"min": 1.239, "max": 1.241}},
        ],
    }
    check = spec_checks(_context(spec, Box(10, 10, 10)))[0]

    assert check.status == "pass"
    assert _actual_mass_g(check) == pytest.approx(1.24)
    assert "B-rep volume" in check.actual
    assert "1.24 g/cm³ density" in check.actual
    assert "Nominal mass estimate" in check.message
    assert "finished-part mass is unproven" in check.message
    assert "fully dense" in check.actual
    assert "Source: https://" in check.assumptions


def test_mass_evaluation_converts_real_brep_volume_from_mm_and_inches():
    from build123d import Box

    def evaluate_cube(edge, units):
        spec = {
            "units": "mm",
            "material": "pla",
            "requirements": [
                {"id": "mass", "mass_g": {"min": 20.31, "max": 20.33}},
            ],
        }
        return spec_checks(_context(spec, Box(edge, edge, edge), units=units))[0]

    millimeter_cube = evaluate_cube(25.4, "mm")
    inch_cube = evaluate_cube(1, "in")

    assert millimeter_cube.status == "pass"
    assert inch_cube.status == "pass"
    expected = 25.4**3 / 1000 * 1.24
    assert _actual_mass_g(millimeter_cube) == pytest.approx(expected)
    assert _actual_mass_g(inch_cube) == pytest.approx(expected)
    assert _actual_mass_g(millimeter_cube) == pytest.approx(_actual_mass_g(inch_cube))


def test_mass_evaluation_uses_context_steel_1018_material_on_real_brep():
    from build123d import Box

    spec = {
        "requirements": [
            {"id": "mass", "mass_g": {"min": 7.89, "max": 7.91}},
        ],
    }
    check = spec_checks(_context(spec, Box(10, 10, 10), material="steel_1018"))[0]

    assert check.status == "pass"
    assert _actual_mass_g(check) == pytest.approx(7.9)


def test_mass_evaluation_skips_when_material_is_missing_or_unknown():
    from build123d import Box

    requirement = {"requirements": [{"id": "mass", "mass_g": {"max": 100}}]}
    missing = spec_checks(_context(requirement, Box(10, 10, 10)))[0]
    unknown = spec_checks(
        _context(
            {**requirement, "material": "pla_filled"},
            Box(10, 10, 10),
        )
    )[0]

    assert missing.status == "skip"
    assert "material is not identified" in missing.message
    assert unknown.status == "skip"
    assert "material data" in unknown.message
    assert "unavailable" in unknown.message


def test_mass_evaluation_skips_when_context_and_spec_materials_conflict():
    from build123d import Box

    spec = {
        "material": "pla",
        "requirements": [{"id": "mass", "mass_g": {"max": 100}}],
    }
    check = spec_checks(
        _context(spec, Box(10, 10, 10), material="steel_1018")
    )[0]

    assert check.status == "skip"
    assert "context material conflicts" in check.message
    assert check.actual.startswith("unknown")
