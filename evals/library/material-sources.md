# Nominal-density material sources

This file records source-backed catalog entries. The entries are not agent-eval samples or results, and they make no performance claim.

The evaluator estimates mass by multiplying the model's B-rep volume, converted to cm³, by the selected density in g/cm³. This treats the B-rep as a fully dense solid. It does not account for print infill, voids, machining stock, coatings, moisture, or other manufacturing effects. A nominal-density estimate does not guarantee finished-part mass.

Catalog IDs are exact lookups. The PLA ID is scoped to one named resin grade; the steel ID is scoped to the AISI 1018 reference below. Neither is an alias for other grades, alloys, filled polymers, or print materials.

## pla

**Definition:** NatureWorks Ingeo biopolymer grade 3D700 for large-format additive manufacturing, treated as the unfilled base resin. NatureWorks describes Ingeo as primarily PLA.

**Nominal density:** 1.24 g/cm³. NatureWorks lists 1.24 g/cc for 3D700 under physical properties, tested by ASTM D1505. The source row does not state a density test temperature.

**Sources:**

- [Ingeo Biopolymer 3D700 Technical Data Sheet](https://natureworksllc.com/getContentAsset/ad296463-f132-4a11-8763-e9297c353e24/53ffd608-340f-457b-b656-6b8cc0000bd4/TechnicalDataSheet_3D700_large-format.pdf?language=en)
- [NatureWorks: Composting](https://natureworksllc.com/sustainability/end-of-life-opportunities/composting) — identifies Ingeo biopolymer as primarily PLA.

**Assumptions and limits:** This is the base resin's bulk density, not an effective density for a printed part. The TDS says pellets can be printed directly or converted to FFF filament, and warns that its bulk mechanical values are not properties of a printed part. Printed mass depends on infill, shells, bead gaps, and process porosity. NatureWorks says drying before melt extrusion is essential for process consistency; moisture is not adjusted in the catalog. The TDS also says the resin can be formulated with additives, so this ID assumes no pigments, fillers, reinforcements, impact modifiers, or process aids. The cited density row gives no test temperature. Actual printed-part mass is not guaranteed.

## steel_1018

**Definition:** AISI 1018 mild steel associated in the source with nominal composition Fe-0.18 wt% C-0.8 wt% Mn-0.18 wt% Si. The source does not specify a rolled product form or heat-treatment condition.

**Nominal density:** 7.9 g/cm³. Argonne Table 4 lists 7.9 g/mL for its AISI 1018 sample with that nominal composition. The report says the densities in the table are either measured or obtained from literature; it does not identify which applies to this row. The catalog therefore retains the reported value at its stated one-decimal precision as a coarse nominal reference, not a guaranteed value for every 1018 heat or stock condition.

**Source:**

- [Electrochemical Corrosion Testing of Metal Waste Forms (ANL/CMT/CP-100737), Table 4](https://www.osti.gov/servlets/purl/750628)

**Assumptions and limits:** This is bulk alloy density for a fully dense solid B-rep, not for metal powder or an additively manufactured part. Porosity, inclusions, cracks, scale, plating, coatings, corrosion, oil, and surface water are not modeled. Chemistry and product condition vary; this ID does not alias A36, 1020, or other steels. The report does not state a density test temperature, so no thermal expansion correction is applied. Actual stock and finished-part mass are not guaranteed.

## aluminum_6061

**Definition:** Wrought aluminum alloy designation 6061, with temper and product form unspecified.

**Nominal density:** 2.70 g/cm³. The Aluminum Association's Teal Sheets list a calculated nominal density of 2.70 × 10³ kg/m³ for designation 6061, equivalent to 2.70 g/cm³. This is an alloy-level reference, not a temper-specific measurement.

**Source:**

- [International Alloy Designations (Teal Sheets, December 2025)](https://www.aluminum.org/sites/default/files/2026-01/Teal%20Sheets%20December%202025.pdf)

**Assumptions and limits:** This is nominal density for wrought 6061, not powder or an additively manufactured part. The B-rep is treated as fully dense; porosity, casting or additive defects, cladding, voids, and coatings are not modeled. The entry does not stand for another alloy such as 6063 or 7075, and it does not distinguish 6061 tempers. The source does not state a density reference temperature; no thermal expansion correction is applied. Actual part mass is not guaranteed.

## stainless_304_4301

**Definition:** Outokumpu Core 304/4301, EN 1.4301, ASTM Type 304, UNS S30400. This is distinct from Core 304L/4307 and other stainless grades.

**Nominal density:** 7.9 g/cm³. Outokumpu's physical-properties table lists 7.9 kg/dm³ for Core 304/4301 according to EN 10088-1 Table 7; this is numerically 7.9 g/cm³.

**Source:**

- [Outokumpu Core Range Datasheet: Stainless Steels for Corrosive Environments](https://www.outokumpu.com/sv-se/products/product-ranges/-/media/files/products/core/outokumpu-core-range-datasheet.pdf?modified=20251117111909&revision=025e9931-a1d5-4c8f-8ff5-f881d38916da)

**Assumptions and limits:** This is bulk density for the named wrought stainless grade, treated as a fully dense solid. Voids, porosity, scale, coatings, plating, corrosion, surface water, and other contamination are not modeled. The ID does not alias 304L/4307, 304LN/4311, 316, or another stainless grade. The datasheet gives a grade-level density and separate temperature-dependent physical properties; this catalog applies no temperature correction. Actual part mass is not guaranteed.
