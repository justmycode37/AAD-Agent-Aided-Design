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
        "aliases": ["pla", "polylactic acid"],
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
        "aliases": ["steel", "mild steel", "1018"],
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
        "aliases": ["aluminium", "aluminum", "6061", "alu 6061"],
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
        "aliases": ["stainless", "stainless steel", "304", "4301", "1.4301"],
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
    'petg': {
        "id": 'petg', "aliases": ["petg", "polyethylene terephthalate glycol"], "name": 'PETG', "definition": 'PETG copolyester from Amsler & Frey technical datasheet',
        "density_g_cm3": 1.27, "density_unit": "g/cm^3",
        "density_basis": 'PET G density is printed as 1.27 g/cm3 in the supplier datasheet.', "source_title": 'TECHNICAL DATASHEET — PETG', "source_url": 'https://www.shop.amsler-frey.ch/en?category=24&cmd=generate_datasheet',
        "retrieved": "2026-11-05", "provenance": 'PET G density is printed as 1.27 g/cm3 in the supplier datasheet.',
        "assumptions": {
            "bulk_vs_printed": "Bulk solid resin or alloy reference; not an effective printed-part density.",
            "porosity": "Treats B-rep as fully dense; infill, voids, and manufacturing porosity are excluded.",
            "moisture": "No moisture adjustment; absorbed moisture and surface contamination are excluded.",
            "composition": "Scoped to the named grade/alloy above; fillers and other grades are not represented.",
            "temperature": "No temperature correction; source does not give a density reference temperature.",
            "mass_guarantee": "Nominal density estimate only; finished-part mass is not guaranteed.",
        },
    },
    'abs': {
        "id": 'abs', "aliases": ["abs", "acrylonitrile butadiene styrene"], "name": 'Ensinger TECARAN ABS grey', "definition": 'Specific grey ABS stock grade TECARAN ABS grey by Ensinger',
        "density_g_cm3": 1.04, "density_unit": "g/cm^3",
        "density_basis": 'Ensinger lists density as 1.04 g/cm3.', "source_title": 'TECARAN ABS grey | Ensinger', "source_url": 'https://www.ensingerplastics.com/en-gb/shapes/tecaran-abs-grey',
        "retrieved": "2026-11-05", "provenance": 'Ensinger lists density as 1.04 g/cm3.',
        "assumptions": {
            "bulk_vs_printed": "Bulk solid resin or alloy reference; not an effective printed-part density.",
            "porosity": "Treats B-rep as fully dense; infill, voids, and manufacturing porosity are excluded.",
            "moisture": "No moisture adjustment; absorbed moisture and surface contamination are excluded.",
            "composition": "Scoped to the named grade/alloy above; fillers and other grades are not represented.",
            "temperature": "No temperature correction; source does not give a density reference temperature.",
            "mass_guarantee": "Nominal density estimate only; finished-part mass is not guaranteed.",
        },
    },
    'pa12': {
        "id": 'pa12', "aliases": ["pa12", "pa 12", "nylon 12", "nylon", "polyamide 12"], "name": 'Ensinger TECAMID 12 natural', "definition": 'Unfilled natural PA12 stock grade TECAMID 12 by Ensinger',
        "density_g_cm3": 1.02, "density_unit": "g/cm^3",
        "density_basis": 'Ensinger lists density as 1.02 g/cm3.', "source_title": 'PA 12 - TECAMID 12 natural | Ensinger', "source_url": 'https://www.ensingerplastics.com/en/shapes/pa12-tecamid-12-natural',
        "retrieved": "2026-11-05", "provenance": 'Ensinger lists density as 1.02 g/cm3.',
        "assumptions": {
            "bulk_vs_printed": "Bulk solid resin or alloy reference; not an effective printed-part density.",
            "porosity": "Treats B-rep as fully dense; infill, voids, and manufacturing porosity are excluded.",
            "moisture": "No moisture adjustment; absorbed moisture and surface contamination are excluded.",
            "composition": "Scoped to the named grade/alloy above; fillers and other grades are not represented.",
            "temperature": "No temperature correction; source does not give a density reference temperature.",
            "mass_guarantee": "Nominal density estimate only; finished-part mass is not guaranteed.",
        },
    },
    'aluminum_7075': {
        "id": 'aluminum_7075', "aliases": ["7075", "aluminium", "aluminum"], "name": 'Wrought aluminum alloy 7075', "definition": 'Wrought alloy 7075, temper unspecified',
        "density_g_cm3": 2.81, "density_unit": "g/cm^3",
        "density_basis": 'Aluminum Association table prints calculated nominal density 2.81 (kg/m3 x 10^3) for 7075.', "source_title": 'International Alloy Designations (Teal Sheets, December 2025)', "source_url": 'https://www.aluminum.org/sites/default/files/2026-01/Teal%20Sheets%20December%202025.pdf',
        "retrieved": "2026-11-05", "provenance": 'Aluminum Association table prints calculated nominal density 2.81 (kg/m3 x 10^3) for 7075.',
        "assumptions": {
            "bulk_vs_printed": "Bulk solid resin or alloy reference; not an effective printed-part density.",
            "porosity": "Treats B-rep as fully dense; infill, voids, and manufacturing porosity are excluded.",
            "moisture": "No moisture adjustment; absorbed moisture and surface contamination are excluded.",
            "composition": "Scoped to the named grade/alloy above; fillers and other grades are not represented.",
            "temperature": "No temperature correction; source does not give a density reference temperature.",
            "mass_guarantee": "Nominal density estimate only; finished-part mass is not guaranteed.",
        },
    },
    'aluminum_5052': {
        "id": 'aluminum_5052', "aliases": ["5052", "aluminium", "aluminum"], "name": 'Wrought aluminum alloy 5052', "definition": 'Wrought alloy 5052, temper unspecified',
        "density_g_cm3": 2.68, "density_unit": "g/cm^3",
        "density_basis": 'Aluminum Association table prints calculated nominal density 2.68 (kg/m3 x 10^3) for 5052.', "source_title": 'International Alloy Designations (Teal Sheets, December 2025)', "source_url": 'https://www.aluminum.org/sites/default/files/2026-01/Teal%20Sheets%20December%202025.pdf',
        "retrieved": "2026-11-05", "provenance": 'Aluminum Association table prints calculated nominal density 2.68 (kg/m3 x 10^3) for 5052.',
        "assumptions": {
            "bulk_vs_printed": "Bulk solid resin or alloy reference; not an effective printed-part density.",
            "porosity": "Treats B-rep as fully dense; infill, voids, and manufacturing porosity are excluded.",
            "moisture": "No moisture adjustment; absorbed moisture and surface contamination are excluded.",
            "composition": "Scoped to the named grade/alloy above; fillers and other grades are not represented.",
            "temperature": "No temperature correction; source does not give a density reference temperature.",
            "mass_guarantee": "Nominal density estimate only; finished-part mass is not guaranteed.",
        },
    },
    'asa': {
        "id": 'asa', "aliases": ['asa', 'acrylonitrile styrene acrylate'], "name": 'Spectrum THE FILAMENT ASA',
        "definition": 'Spectrum THE FILAMENT ASA FDM filament',
        "density_g_cm3": 1.07, "density_unit": "g/cm^3",
        "density_basis": 'Specific Gravity is printed as 1.07 g/cm3 (ASTM D792).', "source_title": 'TECHNICAL DATA SHEET — THE FILAMENT ASA', "source_url": 'https://spectrumfilaments.com/wp-content/uploads/2025/09/en_tds_the_filament_asa.pdf',
        "retrieved": "2026-11-05", "provenance": 'Specific Gravity is printed as 1.07 g/cm3 (ASTM D792).',
        "assumptions": {
            "bulk_vs_printed": "Bulk source grade or laminate reference; not a printed-part or composite layup correction.",
            "porosity": "Treats B-rep as fully dense; manufacturing voids, infill, and porosity are excluded.",
            "moisture": "No moisture adjustment; absorbed moisture and surface contamination are excluded.",
            "composition": "Scoped to the named grade, product, or alloy above; fillers and other grades may differ.",
            "temperature": "No temperature correction beyond the stated source condition.",
            "mass_guarantee": "Nominal density estimate only; finished-part mass is not guaranteed.",
        },
    },
    'tpu': {
        "id": 'tpu', "aliases": ['tpu', 'thermoplastic polyurethane', 'polyurethane'], "name": 'Covestro Desmopan 9380A GMP TPU',
        "definition": 'Covestro Desmopan 9380A GMP thermoplastic polyurethane',
        "density_g_cm3": 1.11, "density_unit": "g/cm^3",
        "density_basis": 'Density is printed as 1110 kg/m3 by ISO 1183-1, equivalent to 1.11 g/cm3.', "source_title": 'Desmopan 9380A GMP | Covestro', "source_url": 'https://solutions.covestro.com/en/products/desmopan/desmopan-9380a-gmp_000000000084876844',
        "retrieved": "2026-11-05", "provenance": 'Density is printed as 1110 kg/m3 by ISO 1183-1, equivalent to 1.11 g/cm3.',
        "assumptions": {
            "bulk_vs_printed": "Bulk source grade or laminate reference; not a printed-part or composite layup correction.",
            "porosity": "Treats B-rep as fully dense; manufacturing voids, infill, and porosity are excluded.",
            "moisture": "No moisture adjustment; absorbed moisture and surface contamination are excluded.",
            "composition": "Scoped to the named grade, product, or alloy above; fillers and other grades may differ.",
            "temperature": "No temperature correction beyond the stated source condition.",
            "mass_guarantee": "Nominal density estimate only; finished-part mass is not guaranteed.",
        },
    },
    'polycarbonate': {
        "id": 'polycarbonate', "aliases": ['polycarbonate', 'pc', 'tecanat'], "name": 'Ensinger TECANAT natural polycarbonate',
        "definition": 'Unfilled transparent Ensinger TECANAT natural PC stock',
        "density_g_cm3": 1.19, "density_unit": "g/cm^3",
        "density_basis": 'Ensinger lists density as 1.19 g/cm3.', "source_title": 'Polycarbonate (PC) - TECANAT natural | Ensinger', "source_url": 'https://www.ensingerplastics.com/en-gb/shapes/polycarbonate-tecanat-natural',
        "retrieved": "2026-11-05", "provenance": 'Ensinger lists density as 1.19 g/cm3.',
        "assumptions": {
            "bulk_vs_printed": "Bulk source grade or laminate reference; not a printed-part or composite layup correction.",
            "porosity": "Treats B-rep as fully dense; manufacturing voids, infill, and porosity are excluded.",
            "moisture": "No moisture adjustment; absorbed moisture and surface contamination are excluded.",
            "composition": "Scoped to the named grade, product, or alloy above; fillers and other grades may differ.",
            "temperature": "No temperature correction beyond the stated source condition.",
            "mass_guarantee": "Nominal density estimate only; finished-part mass is not guaranteed.",
        },
    },
    'pom_c': {
        "id": 'pom_c', "aliases": ['pom', 'pom-c', 'acetal', 'delrin'], "name": 'Ensinger TECAFORM AH natural POM-C acetal',
        "definition": 'Unfilled Ensinger TECAFORM AH natural POM-C copolymer stock',
        "density_g_cm3": 1.41, "density_unit": "g/cm^3",
        "density_basis": 'Ensinger lists density as 1.41 g/cm3.', "source_title": 'POM-C unfilled - TECAFORM AH natural | Ensinger', "source_url": 'https://www.ensingerplastics.com/en-us/shapes/acetal-tecaform-ah-natural',
        "retrieved": "2026-11-05", "provenance": 'Ensinger lists density as 1.41 g/cm3.',
        "assumptions": {
            "bulk_vs_printed": "Bulk source grade or laminate reference; not a printed-part or composite layup correction.",
            "porosity": "Treats B-rep as fully dense; manufacturing voids, infill, and porosity are excluded.",
            "moisture": "No moisture adjustment; absorbed moisture and surface contamination are excluded.",
            "composition": "Scoped to the named grade, product, or alloy above; fillers and other grades may differ.",
            "temperature": "No temperature correction beyond the stated source condition.",
            "mass_guarantee": "Nominal density estimate only; finished-part mass is not guaranteed.",
        },
    },
    'stainless_316_14401': {
        "id": 'stainless_316_14401', "aliases": ['stainless 316', '316', '316 stainless', '1.4401', 's31600'], "name": 'Outokumpu Supra 316/4401 stainless steel',
        "definition": 'Outokumpu Supra 316/4401, EN 1.4401, UNS S31600',
        "density_g_cm3": 8.0, "density_unit": "g/cm^3",
        "density_basis": 'Outokumpu Table 7 lists 8.0 kg/dm3 for Supra 316/4401, equivalent to 8.0 g/cm3.', "source_title": 'Outokumpu Supra range datasheet', "source_url": 'https://otke-cdn.outokumpu.com/-/media/files/products/supra/outokumpu-supra-range-datasheet.pdf?modified=20221122121441&revision=34cf75de-9c70-4dd1-acb8-a6addfa75ffe',
        "retrieved": "2026-11-05", "provenance": 'Outokumpu Table 7 lists 8.0 kg/dm3 for Supra 316/4401, equivalent to 8.0 g/cm3.',
        "assumptions": {
            "bulk_vs_printed": "Bulk source grade or laminate reference; not a printed-part or composite layup correction.",
            "porosity": "Treats B-rep as fully dense; manufacturing voids, infill, and porosity are excluded.",
            "moisture": "No moisture adjustment; absorbed moisture and surface contamination are excluded.",
            "composition": "Scoped to the named grade, product, or alloy above; fillers and other grades may differ.",
            "temperature": "No temperature correction beyond the stated source condition.",
            "mass_guarantee": "Nominal density estimate only; finished-part mass is not guaranteed.",
        },
    },
    'steel_s235': {
        "id": 'steel_s235', "aliases": ['s235', 's235jr', '1.0038', 'mild steel'], "name": 'S235JR structural steel',
        "definition": 'S235JR / EN 1.0038 hot-rolled structural steel',
        "density_g_cm3": 7.85, "density_unit": "g/cm^3",
        "density_basis": 'Datasheet prints density 7.85 g/cm3.', "source_title": 'Steel 1.0038 / S235JR datasheet | Xometry', "source_url": 'https://xometry.eu/wp-content/uploads/2021/02/Steel-1.0038.pdf',
        "retrieved": "2026-11-05", "provenance": 'Datasheet prints density 7.85 g/cm3.',
        "assumptions": {
            "bulk_vs_printed": "Bulk source grade or laminate reference; not a printed-part or composite layup correction.",
            "porosity": "Treats B-rep as fully dense; manufacturing voids, infill, and porosity are excluded.",
            "moisture": "No moisture adjustment; absorbed moisture and surface contamination are excluded.",
            "composition": "Scoped to the named grade, product, or alloy above; fillers and other grades may differ.",
            "temperature": "No temperature correction beyond the stated source condition.",
            "mass_guarantee": "Nominal density estimate only; finished-part mass is not guaranteed.",
        },
    },
    'steel_4140': {
        "id": 'steel_4140', "aliases": ['4140', 'aisi 4140', 'sae 4140', '42crmo4', 'chromoly'], "name": 'AISI 4140 chromium-molybdenum alloy steel',
        "definition": 'AISI/SAE 4140 Cr-Mo alloy steel, condition unspecified',
        "density_g_cm3": 7.85, "density_unit": "g/cm^3",
        "density_basis": 'NORAMARK lists typical density as 7.85 g/cm3.', "source_title": '4140 Steel: Properties, Machinability, Uses | NORAMARK', "source_url": 'https://www.noramark.com/resources/materials/4140-steel/',
        "retrieved": "2026-11-05", "provenance": 'NORAMARK lists typical density as 7.85 g/cm3.',
        "assumptions": {
            "bulk_vs_printed": "Bulk source grade or laminate reference; not a printed-part or composite layup correction.",
            "porosity": "Treats B-rep as fully dense; manufacturing voids, infill, and porosity are excluded.",
            "moisture": "No moisture adjustment; absorbed moisture and surface contamination are excluded.",
            "composition": "Scoped to the named grade, product, or alloy above; fillers and other grades may differ.",
            "temperature": "No temperature correction beyond the stated source condition.",
            "mass_guarantee": "Nominal density estimate only; finished-part mass is not guaranteed.",
        },
    },
    'brass_c360': {
        "id": 'brass_c360', "aliases": ['c360', 'c36000', 'brass', 'free cutting brass'], "name": 'UNS C36000 free-cutting brass',
        "definition": 'UNS C36000 leaded free-cutting brass; CuZn36Pb3',
        "density_g_cm3": 8.5, "density_unit": "g/cm^3",
        "density_basis": 'bedra lists density as 8.5 g/cm3 at 20°C.', "source_title": 'bedra asia: C36000', "source_url": 'https://www.bedra.com/asia/products/bedra-rod-bar-new/high-speed-free-cutting-alloys/c36000/',
        "retrieved": "2026-11-05", "provenance": 'bedra lists density as 8.5 g/cm3 at 20°C.',
        "assumptions": {
            "bulk_vs_printed": "Bulk source grade or laminate reference; not a printed-part or composite layup correction.",
            "porosity": "Treats B-rep as fully dense; manufacturing voids, infill, and porosity are excluded.",
            "moisture": "No moisture adjustment; absorbed moisture and surface contamination are excluded.",
            "composition": "Scoped to the named grade, product, or alloy above; fillers and other grades may differ.",
            "temperature": "No temperature correction beyond the stated source condition.",
            "mass_guarantee": "Nominal density estimate only; finished-part mass is not guaranteed.",
        },
    },
    'copper_c110': {
        "id": 'copper_c110', "aliases": ['c110', 'c11000', 'copper', 'etp copper'], "name": 'Kanthal C11000 ETP copper',
        "definition": 'C11000 electrolytic tough pitch copper, Kanthal conductive wire',
        "density_g_cm3": 8.89, "density_unit": "g/cm^3",
        "density_basis": 'Kanthal prints density as 8.89 g/cm3.', "source_title": 'C11000 ETP — Kanthal', "source_url": 'https://www.kanthal.com/en/products/datasheets/material-datasheets/wire/conductive-wire/c11000-etp/',
        "retrieved": "2026-11-05", "provenance": 'Kanthal prints density as 8.89 g/cm3.',
        "assumptions": {
            "bulk_vs_printed": "Bulk source grade or laminate reference; not a printed-part or composite layup correction.",
            "porosity": "Treats B-rep as fully dense; manufacturing voids, infill, and porosity are excluded.",
            "moisture": "No moisture adjustment; absorbed moisture and surface contamination are excluded.",
            "composition": "Scoped to the named grade, product, or alloy above; fillers and other grades may differ.",
            "temperature": "No temperature correction beyond the stated source condition.",
            "mass_guarantee": "Nominal density estimate only; finished-part mass is not guaranteed.",
        },
    },
    'titanium_ti64': {
        "id": 'titanium_ti64', "aliases": ['ti64', 'ti-6al-4v', 'grade 5 titanium', 'titanium', 'r56400', '3.7164'], "name": 'Ti-6Al-4V Grade 5 titanium alloy',
        "definition": 'Ti-6Al-4V / Grade 5 / UNS R56400, source scoped to grade designation',
        "density_g_cm3": 4.43, "density_unit": "g/cm^3",
        "density_basis": 'METALCOR lists density 4.43 g/cm3 under physical properties at 20°C.', "source_title": 'Titanium-Grade 5 (3.7164/65), R56400 | METALCOR', "source_url": 'https://www.metalcor.de/en/datenblatt/125/',
        "retrieved": "2026-11-05", "provenance": 'METALCOR lists density 4.43 g/cm3 under physical properties at 20°C.',
        "assumptions": {
            "bulk_vs_printed": "Bulk source grade or laminate reference; not a printed-part or composite layup correction.",
            "porosity": "Treats B-rep as fully dense; manufacturing voids, infill, and porosity are excluded.",
            "moisture": "No moisture adjustment; absorbed moisture and surface contamination are excluded.",
            "composition": "Scoped to the named grade, product, or alloy above; fillers and other grades may differ.",
            "temperature": "No temperature correction beyond the stated source condition.",
            "mass_guarantee": "Nominal density estimate only; finished-part mass is not guaranteed.",
        },
    },
    'fr4': {
        "id": 'fr4', "aliases": ['fr4', 'fr-4', 'glass epoxy', 'pcb laminate'], "name": 'Eagle FR-4 glass epoxy laminate',
        "definition": 'Eagle Performance Plastics FR-4 glass fabric filled epoxy laminate',
        "density_g_cm3": 1.85, "density_unit": "g/cm^3",
        "density_basis": 'Eagle lists density as 1.85 g/cm^3, ASTM D792.', "source_title": 'FR-4 Laminate | Eagle Performance Plastics', "source_url": 'https://eagle-plastics.com/materials/fr-4/',
        "retrieved": "2026-11-05", "provenance": 'Eagle lists density as 1.85 g/cm^3, ASTM D792.',
        "assumptions": {
            "bulk_vs_printed": "Bulk source grade or laminate reference; not a printed-part or composite layup correction.",
            "porosity": "Treats B-rep as fully dense; manufacturing voids, infill, and porosity are excluded.",
            "moisture": "No moisture adjustment; absorbed moisture and surface contamination are excluded.",
            "composition": "Scoped to the named grade, product, or alloy above; fillers and other grades may differ.",
            "temperature": "No temperature correction beyond the stated source condition.",
            "mass_guarantee": "Nominal density estimate only; finished-part mass is not guaranteed.",
        },
    },
    'pmma': {
        "id": 'pmma', "aliases": ['pmma', 'acrylic', 'acrylic glass'], "name": 'Ensinger cast PMMA acrylic sheet',
        "definition": 'Ensinger cast acrylic stock shapes; chemical designation PMMA',
        "density_g_cm3": 1.2, "density_unit": "g/cm^3",
        "density_basis": 'Datasheet prints density 1.2 g/cm3.', "source_title": 'Cast Acrylic sheet – Stock Shapes | Ensinger', "source_url": 'https://www.ensingerplastics.com/-/media/ensinger/files/document-teaser-files/uk-documents/datasheet-cast-acrylic-sheet-en.pdf?rev=3424c3eb184d4d6d9234d1e70b344976',
        "retrieved": "2026-11-05", "provenance": 'Datasheet prints density 1.2 g/cm3.',
        "assumptions": {
            "bulk_vs_printed": "Bulk source grade or laminate reference; not a printed-part or composite layup correction.",
            "porosity": "Treats B-rep as fully dense; manufacturing voids, infill, and porosity are excluded.",
            "moisture": "No moisture adjustment; absorbed moisture and surface contamination are excluded.",
            "composition": "Scoped to the named grade, product, or alloy above; fillers and other grades may differ.",
            "temperature": "No temperature correction beyond the stated source condition.",
            "mass_guarantee": "Nominal density estimate only; finished-part mass is not guaranteed.",
        },
    },
    'silicone_rubber': {
        "id": 'silicone_rubber', "aliases": ['silicone', 'silicone rubber', 'vmq', 'silastic'], "name": 'Dow SILASTIC DY 32-5107 U silicone rubber',
        "definition": 'Dow SILASTIC DY 32-5107 U 50 durometer extruded silicone rubber',
        "density_g_cm3": 1.16, "density_unit": "g/cm^3",
        "density_basis": 'Dow lists as-cured density as 1.16 g/cm3.', "source_title": 'SILASTIC DY 32-5107 U Silicone Rubber | Dow', "source_url": 'https://www.dow.com/en-us/pdp.silastic%E2%84%A2%2Bdy%2B32-5107%2Bu%2Bsilicone%2Brubber.03270564z.html',
        "retrieved": "2026-11-05", "provenance": 'Dow lists as-cured density as 1.16 g/cm3.',
        "assumptions": {
            "bulk_vs_printed": "Bulk source grade or laminate reference; not a printed-part or composite layup correction.",
            "porosity": "Treats B-rep as fully dense; manufacturing voids, infill, and porosity are excluded.",
            "moisture": "No moisture adjustment; absorbed moisture and surface contamination are excluded.",
            "composition": "Scoped to the named grade, product, or alloy above; fillers and other grades may differ.",
            "temperature": "No temperature correction beyond the stated source condition.",
            "mass_guarantee": "Nominal density estimate only; finished-part mass is not guaranteed.",
        },
    },
    'epdm': {
        "id": 'epdm', "aliases": ['epdm', 'ethylene propylene diene monomer', 'rubber'], "name": 'MacLellan C653 peroxide-cured EPDM, 70 Shore A',
        "definition": 'MacLellan C653 peroxide-cured EPDM rubber sheeting, 70 Shore A',
        "density_g_cm3": 1.23, "density_unit": "g/cm^3",
        "density_basis": 'Specific gravity is printed as 1.23 g/cm3.', "source_title": 'C653 70° Shore A Peroxide Cured EPDM Rubber Sheeting | MacLellan Rubber', "source_url": 'https://www.maclellanrubber.com/product/c653-70-shore-a-peroxide-cured-epdm-rubber-sheeting',
        "retrieved": "2026-11-05", "provenance": 'Specific gravity is printed as 1.23 g/cm3.',
        "assumptions": {
            "bulk_vs_printed": "Bulk source grade or laminate reference; not a printed-part or composite layup correction.",
            "porosity": "Treats B-rep as fully dense; manufacturing voids, infill, and porosity are excluded.",
            "moisture": "No moisture adjustment; absorbed moisture and surface contamination are excluded.",
            "composition": "Scoped to the named grade, product, or alloy above; fillers and other grades may differ.",
            "temperature": "No temperature correction beyond the stated source condition.",
            "mass_guarantee": "Nominal density estimate only; finished-part mass is not guaranteed.",
        },
    },
    'sla_clear_v5': {
        "id": 'sla_clear_v5', "aliases": ['sla resin', 'resin', 'clear resin v5', 'formlabs'], "name": 'Formlabs Clear Resin V5 SLA resin', "definition": 'Named Formlabs Clear Resin V5 stereolithography resin; density values correspond to the datasheet post-cure conditions.',
        "density_g_cm3": 1.21, "density_unit": "g/cm^3",
        "density_basis": 'Density is printed as 1.21 g/cm3 for both listed post-cure conditions (ASTM D792).', "source_title": 'Clear Resin V5 technical data sheet | Formlabs', "source_url": 'https://formlabs-media.formlabs.com/filer_public/dd/7f/dd7f3de9-55ae-4c82-ac52-408a4b5df64d/clear_resin_v5_tds___formlabs.pdf',
        "retrieved": "2026-11-05", "provenance": 'Density is printed as 1.21 g/cm3 for both listed post-cure conditions (ASTM D792).',
        "assumptions": {
            "bulk_vs_printed": "Named bulk resin, tool steel, or composite plate reference; not a generic process result.",
            "porosity": "Treats the modeled volume as fully dense; print voids and manufacturing defects are excluded.",
            "moisture": "No moisture adjustment or surface contamination is modeled.",
            "composition": "Scoped to the exact named product or grade; formulations and product lines may differ.",
            "temperature": "Uses the source condition; no other temperature correction is applied.",
            "mass_guarantee": "Nominal source-density estimate only; finished-part mass is not guaranteed.",
        },
    },
    'tool_steel_o1': {
        "id": 'tool_steel_o1', "aliases": ['tool steel', 'o1', 'aisi o1', 'uns t31501', '1.2510'], "name": 'O1 oil-hardening tool steel', "definition": 'O1 / UNS T31501 tool steel, Gautier Specialty Metals product data sheet reference.',
        "density_g_cm3": 7.83, "density_unit": "g/cm^3",
        "density_basis": 'The datasheet prints specific gravity 7.83 and density 0.283 lb/in3; catalog uses the printed specific gravity as the nominal g/cm3 equivalent.', "source_title": 'O1 Tool Steel technical data | Gautier Specialty Metals', "source_url": 'https://static1.squarespace.com/static/677d87c404e5393add2a5270/t/67ee9fe783efe55296ec16f6/1743691751153/GSM%2BO1%2BTool%2BSteel.pdf',
        "retrieved": "2026-11-05", "provenance": 'The datasheet prints specific gravity 7.83 and density 0.283 lb/in3; catalog uses the printed specific gravity as the nominal g/cm3 equivalent.',
        "assumptions": {
            "bulk_vs_printed": "Named bulk resin, tool steel, or composite plate reference; not a generic process result.",
            "porosity": "Treats the modeled volume as fully dense; print voids and manufacturing defects are excluded.",
            "moisture": "No moisture adjustment or surface contamination is modeled.",
            "composition": "Scoped to the exact named product or grade; formulations and product lines may differ.",
            "temperature": "Uses the source condition; no other temperature correction is applied.",
            "mass_guarantee": "Nominal source-density estimate only; finished-part mass is not guaranteed.",
        },
    },
    'carbon_fiber_exel_extens': {
        "id": 'carbon_fiber_exel_extens', "aliases": ['carbon fiber plate', 'carbon fibre plate', 'cfrp plate', 'carbon composite plate', 'exel extens'], "name": 'Exel EXTENS unidirectional carbon fibre strengthening plate', "definition": 'Pultruded Exel EXTENS carbon fibre plate with vinylester or epoxy resin and 68 vol-% fibre.',
        "density_g_cm3": 1.6, "density_unit": "g/cm^3",
        "density_basis": 'The manufacturer technical datasheet prints density as 1.6 g/cm3.', "source_title": 'EXEL EXTENS Carbon Fibre Strengthening Plate technical datasheet', "source_url": 'https://exelcomposites.com/wp-content/uploads/2020/06/EN_Exel-Extens-Technical-Datasheet.pdf',
        "retrieved": "2026-11-05", "provenance": 'The manufacturer technical datasheet prints density as 1.6 g/cm3.',
        "assumptions": {
            "bulk_vs_printed": "Named bulk resin, tool steel, or composite plate reference; not a generic process result.",
            "porosity": "Treats the modeled volume as fully dense; print voids and manufacturing defects are excluded.",
            "moisture": "No moisture adjustment or surface contamination is modeled.",
            "composition": "Scoped to the exact named product or grade; formulations and product lines may differ.",
            "temperature": "Uses the source condition; no other temperature correction is applied.",
            "mass_guarantee": "Nominal source-density estimate only; finished-part mass is not guaranteed.",
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
