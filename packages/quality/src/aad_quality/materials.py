"""Kernel-free catalog of sourced nominal material densities.

Densities describe the explicitly named bulk material references below. They
support estimates from a fully dense B-rep volume; they do not guarantee the
mass of a finished, printed, coated, or otherwise manufactured part.
"""
from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
import math
from numbers import Real
from typing import Any


_REQUIRED_ASSUMPTIONS = (
    "bulk_vs_printed",
    "porosity",
    "moisture",
    "composition",
    "temperature",
    "mass_guarantee",
)

_CATALOG: dict[str, dict[str, Any]] = {
    "pla": {
        "id": "pla",
        "name": "NatureWorks Ingeo 3D700 PLA base resin",
        "definition": (
            "NatureWorks Ingeo biopolymer grade 3D700 for large-format additive "
            "manufacturing, treated here as the unfilled base resin. This ID is "
            "specific to that grade and is not a density claim for every PLA."
        ),
        "density_g_cm3": 1.24,
        "density_unit": "g/cm^3",
        "density_basis": (
            "The 3D700 technical data sheet lists density as 1.24 g/cc by ASTM "
            "D1505. The cited density row does not state a test temperature."
        ),
        "source_title": "INGEO BIOPOLYMER 3D700 TECHNICAL DATA SHEET",
        "source_url": (
            "https://natureworksllc.com/getContentAsset/"
            "ad296463-f132-4a11-8763-e9297c353e24/"
            "53ffd608-340f-457b-b656-6b8cc0000bd4/"
            "TechnicalDataSheet_3D700_large-format.pdf?language=en"
        ),
        "additional_sources": [
            {
                "title": "NatureWorks | Composting",
                "url": (
                    "https://natureworksllc.com/sustainability/"
                    "end-of-life-opportunities/composting"
                ),
                "supports": "NatureWorks describes Ingeo biopolymer as primarily PLA.",
            }
        ],
        "provenance": (
            "Uses the manufacturer-listed 1.24 g/cm^3 density for Ingeo 3D700. "
            "It is a nominal resin density, not a measurement of a printed part."
        ),
        "assumptions": {
            "bulk_vs_printed": (
                "Use the bulk base-resin density. The TDS says 3D700 pellets can "
                "be printed directly or converted to FFF filament; this value is "
                "not the effective density of a printed component."
            ),
            "porosity": (
                "The B-rep is treated as a solid, void-free envelope. Infill, "
                "bead gaps, shells, and print porosity are not modeled."
            ),
            "moisture": (
                "The TDS says drying before melt extrusion is essential for "
                "process consistency. Moisture state and any moisture-related "
                "mass change are not modeled."
            ),
            "composition": (
                "Assumes the base 3D700 resin without optional process aids, "
                "reinforcements, impact modifiers, pigments, or other fillers. "
                "The TDS notes that additives may be used."
            ),
            "temperature": (
                "The density row does not state a test temperature; no "
                "temperature-dependent density correction is applied."
            ),
            "mass_guarantee": (
                "This is a nominal-density estimate only; finished printed-part "
                "mass is not guaranteed."
            ),
        },
    },
    "steel_1018": {
        "id": "steel_1018",
        "name": "AISI 1018 mild steel reference",
        "definition": (
            "AISI 1018 mild steel, scoped to the source table's associated "
            "nominal composition Fe-0.18 wt% C-0.8 wt% Mn-0.18 wt% Si. The source "
            "does not specify a rolled product form or heat-treatment condition."
        ),
        "density_g_cm3": 7.9,
        "density_unit": "g/cm^3",
        "density_basis": (
            "Argonne Table 4 lists 7.9 g/mL for an AISI 1018 sample with nominal "
            "composition Fe-0.18C-0.8Mn-0.18Si. The report says tabulated "
            "densities may be measured or literature-derived and does not "
            "identify which applies to this row; 7.9 is retained at the source's "
            "reported precision as a coarse nominal reference."
        ),
        "source_title": (
            "Electrochemical Corrosion Testing of Metal Waste Forms "
            "(ANL/CMT/CP-100737), Table 4"
        ),
        "source_url": "https://www.osti.gov/servlets/purl/750628",
        "provenance": (
            "Argonne National Laboratory's Table 4 associates 7.9 g/mL with "
            "AISI 1018 and the composition stated in the definition. The report "
            "does not establish this as a guaranteed value for all 1018 heats "
            "or product conditions."
        ),
        "assumptions": {
            "bulk_vs_printed": (
                "Use a bulk solid-metal density; this entry is not for metal "
                "powder or an additively manufactured steel part."
            ),
            "porosity": (
                "The B-rep is treated as a fully dense solid. Pores, inclusions, "
                "cracks, scale, coatings, plating, and corrosion are not modeled."
            ),
            "moisture": (
                "No moisture adjustment is applied; surface water, oil, and "
                "other contamination are excluded from the material mass."
            ),
            "composition": (
                "This ID is limited to the AISI 1018 sample composition named "
                "above. It does not alias A36, 1020, or other steels. The source "
                "does not identify a product form or condition; lot chemistry "
                "and processing can vary."
            ),
            "temperature": (
                "The report does not state a density measurement temperature; "
                "thermal expansion and density changes with temperature are not "
                "modeled."
            ),
            "mass_guarantee": (
                "This is a coarse nominal-density estimate, not a certified "
                "stock value or guaranteed finished-part mass."
            ),
        },
    },
    "aluminum_6061": {
        "id": "aluminum_6061",
        "name": "Wrought aluminum alloy 6061, temper unspecified",
        "definition": (
            "Wrought aluminum alloy designation 6061. The nominal-density "
            "reference does not specify a temper or particular product form."
        ),
        "density_g_cm3": 2.70,
        "density_unit": "g/cm^3",
        "density_basis": (
            "The Aluminum Association's Teal Sheets list calculated nominal "
            "density 2.70 x 10^3 kg/m^3 for designation 6061, equivalent to "
            "2.70 g/cm^3."
        ),
        "source_title": "International Alloy Designations (Teal Sheets, December 2025)",
        "source_url": (
            "https://www.aluminum.org/sites/default/files/2026-01/"
            "Teal%20Sheets%20December%202025.pdf"
        ),
        "provenance": (
            "Uses the Aluminum Association's calculated nominal density for "
            "wrought alloy designation 6061. The source does not identify a "
            "temper-specific or part-specific density."
        ),
        "assumptions": {
            "bulk_vs_printed": (
                "Use the nominal density for wrought 6061 alloy; this is not a "
                "powder-bed or additively manufactured part density."
            ),
            "porosity": (
                "The B-rep is treated as a fully dense solid. Porosity, casting "
                "or additive defects, voids, cladding, and coatings are not modeled."
            ),
            "moisture": (
                "No moisture correction is applied; surface water, oil, and "
                "contamination are excluded."
            ),
            "composition": (
                "The definition is alloy 6061 with temper unspecified. It does "
                "not alias 6063, 7075, or another alloy; heat chemistry, temper, "
                "and product-form differences are not modeled."
            ),
            "temperature": (
                "The source labels this a calculated nominal density without a "
                "density reference temperature; thermal expansion is not modeled."
            ),
            "mass_guarantee": (
                "This is a nominal-density estimate, not a guaranteed "
                "finished-part mass."
            ),
        },
    },
    "stainless_304_4301": {
        "id": "stainless_304_4301",
        "name": "Outokumpu Core 304/4301 stainless steel",
        "definition": (
            "Outokumpu Core 304/4301, EN 1.4301, ASTM Type 304, UNS S30400. "
            "This is the 304 grade; it is distinct from Core 304L/4307 and "
            "other stainless grades."
        ),
        "density_g_cm3": 7.9,
        "density_unit": "g/cm^3",
        "density_basis": (
            "Outokumpu's physical-properties table lists Core 304/4301 at "
            "7.9 kg/dm^3 according to EN 10088-1 Table 7; this is numerically "
            "7.9 g/cm^3."
        ),
        "source_title": "Outokumpu Core range datasheet — Stainless steels for corrosive environments",
        "source_url": (
            "https://www.outokumpu.com/sv-se/products/product-ranges/-/media/"
            "files/products/core/outokumpu-core-range-datasheet.pdf"
            "?modified=20251117111909&revision=025e9931-a1d5-4c8f-8ff5-f881d38916da"
        ),
        "provenance": (
            "Uses Outokumpu's listed density for its exact Core 304/4301 "
            "product designation, equivalent to ASTM 304 / UNS S30400."
        ),
        "assumptions": {
            "bulk_vs_printed": (
                "Use a bulk wrought stainless-steel density; this entry is not "
                "for metal powder or an additively manufactured part."
            ),
            "porosity": (
                "The B-rep is treated as a fully dense solid. Voids, porosity, "
                "scale, coatings, plating, and corrosion are not modeled."
            ),
            "moisture": (
                "No moisture correction is applied; surface water, oil, and "
                "contamination are excluded."
            ),
            "composition": (
                "This ID means Outokumpu Core 304/4301 (EN 1.4301, UNS S30400). "
                "It does not alias 304L/4307, 304LN/4311, 316, or other grades."
            ),
            "temperature": (
                "The datasheet gives a grade-level density and separate "
                "temperature-dependent physical properties; no density correction "
                "for the modeled temperature is applied."
            ),
            "mass_guarantee": (
                "This is a nominal-density estimate, not a guaranteed "
                "finished-part mass."
            ),
        },
    },
}


def _validated_density(material_id: str, value: object) -> float:
    """Return a density as float, rejecting non-numeric, non-finite, or <= 0 values."""
    if isinstance(value, bool) or not isinstance(value, Real):
        raise ValueError(
            f"Material {material_id!r} must have a positive finite density_g_cm3."
        )
    try:
        density = float(value)
    except (OverflowError, TypeError, ValueError) as exc:
        raise ValueError(
            f"Material {material_id!r} must have a positive finite density_g_cm3."
        ) from exc
    if not math.isfinite(density) or density <= 0:
        raise ValueError(
            f"Material {material_id!r} must have a positive finite density_g_cm3."
        )
    return density


def _validate_record(material_id: str, record: Mapping[str, Any]) -> None:
    if not isinstance(record, Mapping):
        raise ValueError(f"Material {material_id!r} must be a mapping.")
    if record.get("id") != material_id:
        raise ValueError(f"Material key {material_id!r} must match its id field.")

    for field in (
        "name",
        "definition",
        "density_unit",
        "density_basis",
        "source_title",
        "source_url",
        "provenance",
    ):
        value = record.get(field)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"Material {material_id!r} is missing {field}.")
    if record.get("density_unit") != "g/cm^3":
        raise ValueError(f"Material {material_id!r} density_unit must be 'g/cm^3'.")
    if not record["source_url"].startswith("https://"):
        raise ValueError(f"Material {material_id!r} source_url must use HTTPS.")
    _validated_density(material_id, record.get("density_g_cm3"))

    assumptions = record.get("assumptions")
    if not isinstance(assumptions, Mapping):
        raise ValueError(f"Material {material_id!r} must document assumptions.")
    for field in _REQUIRED_ASSUMPTIONS:
        value = assumptions.get(field)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(
                f"Material {material_id!r} is missing assumption {field!r}."
            )


def _validate_catalog(catalog: Mapping[str, Mapping[str, Any]]) -> None:
    for material_id, record in catalog.items():
        if not isinstance(material_id, str) or not material_id.strip():
            raise ValueError("Material IDs must be non-empty strings.")
        _validate_record(material_id, record)


_validate_catalog(_CATALOG)


def get_material(id: str) -> dict[str, Any] | None:
    """Return a defensive copy of an exact catalog ID, or None if it is unknown."""
    if not isinstance(id, str):
        return None
    record = _CATALOG.get(id)
    if record is None:
        return None
    _validate_record(id, record)
    return deepcopy(record)


def list_materials() -> list[dict[str, Any]]:
    """Return defensive copies of all catalog records in stable insertion order."""
    _validate_catalog(_CATALOG)
    return [deepcopy(record) for record in _CATALOG.values()]
