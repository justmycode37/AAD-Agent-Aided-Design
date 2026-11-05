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


## Additional source-checked entries (retrieved 2026-11-05)

| Catalog ID | Scoped material | Density as printed | Source |
|---|---|---:|---|
| `abs` | Ensinger TECARAN ABS grey stock | 1.04 g/cm3 | [Ensinger product page](https://www.ensingerplastics.com/en-gb/shapes/tecaran-abs-grey) |
| `pa12` | Ensinger TECAMID 12 natural, unfilled | 1.02 g/cm³ | [Ensinger product page](https://www.ensingerplastics.com/en/shapes/pa12-tecamid-12-natural) |
| `petg` | PET G, Amsler & Frey supplier datasheet | 1.27 g/cm3 | [Technical datasheet](https://www.shop.amsler-frey.ch/en?category=24&cmd=generate_datasheet) |
| `aluminum_5052` | Wrought alloy 5052, temper unspecified | 2.68 (kg/m³ × 10³) | [Aluminum Association Teal Sheets, December 2025](https://www.aluminum.org/sites/default/files/2026-01/Teal%20Sheets%20December%202025.pdf) |
| `aluminum_7075` | Wrought alloy 7075, temper unspecified | 2.81 (kg/m³ × 10³) | [Aluminum Association Teal Sheets, December 2025](https://www.aluminum.org/sites/default/files/2026-01/Teal%20Sheets%20December%202025.pdf) |

The Teal Sheets figures are numerically g/cm³ when expressed as listed ×10³ kg/m³. PETG is grade/supplier-specific rather than a universal polymer density. ABS and PA12 likewise identify the named Ensinger stock grades. For all entries, mass calculation treats the B-rep as fully dense; printing infill, porosity, conditioning, additives, and finished-part variation are not represented. No density temperature correction is made where the source gives no reference temperature.

## Additional source-checked entries (retrieved 2026-11-05)

| Catalog ID | Scoped material | Density as printed | Source |
|---|---|---:|---|
| `asa` | Spectrum THE FILAMENT ASA | Specific gravity 1.07 g/cm3 (ASTM D792) | [Spectrum ASA technical data sheet](https://spectrumfilaments.com/wp-content/uploads/2025/09/en_tds_the_filament_asa.pdf) |
| `tpu` | Covestro Desmopan 9380A GMP | 1110 kg/m3 (ISO 1183-1), represented as 1.11 g/cm3 | [Covestro product data](https://solutions.covestro.com/en/products/desmopan/desmopan-9380a-gmp_000000000084876844) |
| `polycarbonate` | Ensinger TECANAT natural, unfilled PC | 1.19 g/cm3 | [Ensinger product page](https://www.ensingerplastics.com/en-gb/shapes/polycarbonate-tecanat-natural) |
| `pom_c` | Ensinger TECAFORM AH natural, unfilled POM-C | 1.41 g/cm3 | [Ensinger product page](https://www.ensingerplastics.com/en-us/shapes/acetal-tecaform-ah-natural) |
| `stainless_316_14401` | Outokumpu Supra 316/4401, EN 1.4401 / UNS S31600 | 8.0 kg/dm3 | [Outokumpu Supra datasheet](https://otke-cdn.outokumpu.com/-/media/files/products/supra/outokumpu-supra-range-datasheet.pdf?modified=20221122121441&revision=34cf75de-9c70-4dd1-acb8-a6addfa75ffe) |
| `steel_s235` | S235JR / EN 1.0038 structural steel | 7.85 g/cm3 | [Xometry S235JR datasheet](https://xometry.eu/wp-content/uploads/2021/02/Steel-1.0038.pdf) |
| `steel_4140` | AISI/SAE 4140 Cr-Mo alloy steel | 7.85 g/cm3 | [NORAMARK 4140 reference](https://www.noramark.com/resources/materials/4140-steel/) |
| `brass_c360` | bedra UNS C36000 free-cutting brass | 8.5 g/cm3 at 20°C | [bedra C36000 product data](https://www.bedra.com/asia/products/bedra-rod-bar-new/high-speed-free-cutting-alloys/c36000/) |
| `copper_c110` | Kanthal C11000 ETP copper wire | 8.89 g/cm3 | [Kanthal C11000 data sheet](https://www.kanthal.com/en/products/datasheets/material-datasheets/wire/conductive-wire/c11000-etp/) |
| `titanium_ti64` | Ti-6Al-4V Grade 5 / UNS R56400 | 4.43 g/cm3 at 20°C | [METALCOR Grade 5 datasheet](https://www.metalcor.de/en/datenblatt/125/) |
| `fr4` | Eagle FR-4 glass epoxy laminate | 1.85 g/cm^3 (ASTM D792) | [Eagle FR-4 data](https://eagle-plastics.com/materials/fr-4/) |
| `pmma` | Ensinger cast acrylic sheet, PMMA | 1.2 g/cm3 | [Ensinger cast acrylic datasheet](https://www.ensingerplastics.com/-/media/ensinger/files/document-teaser-files/uk-documents/datasheet-cast-acrylic-sheet-en.pdf?rev=3424c3eb184d4d6d9234d1e70b344976) |
| `silicone_rubber` | Dow SILASTIC DY 32-5107 U, as cured | 1.16 g/cm3 | [Dow product page](https://www.dow.com/en-us/pdp.silastic%E2%84%A2%2Bdy%2B32-5107%2Bu%2Bsilicone%2Brubber.03270564z.html) |
| `epdm` | MacLellan C653 peroxide-cured EPDM, 70 Shore A | Specific gravity 1.23 g/cm3 | [MacLellan product page](https://www.maclellanrubber.com/product/c653-70-shore-a-peroxide-cured-epdm-rubber-sheeting) |

The values are limited to the listed product or grade, not all members of each polymer or alloy family. Values printed in kg/dm3 or kg/m3 are represented in the catalog as numerically equivalent g/cm3 values. As with the other entries, the mass estimate assumes a fully dense solid B-rep and does not model print infill or the as-manufactured condition.

## Further named datasheet-backed entries (retrieved 2026-11-05)

| Catalog ID | Scoped material | Density as printed | Source |
|---|---|---:|---|
| `sla_clear_v5` | Formlabs Clear Resin V5, SLA resin | 1.21 g/cm³ for both listed post-cure conditions (ASTM D792) | [Formlabs Clear Resin V5 technical data sheet](https://formlabs-media.formlabs.com/filer_public/dd/7f/dd7f3de9-55ae-4c82-ac52-408a4b5df64d/clear_resin_v5_tds___formlabs.pdf) |
| `tool_steel_o1` | O1 / UNS T31501 tool steel | Specific gravity 7.83; density 0.283 lb/in³ | [Gautier O1 Tool Steel technical data sheet](https://static1.squarespace.com/static/677d87c404e5393add2a5270/t/67ee9fe783efe55296ec16f6/1743691751153/GSM%2BO1%2BTool%2BSteel.pdf) |
| `carbon_fiber_exel_extens` | Exel EXTENS pultruded unidirectional carbon fibre strengthening plate; 68 vol-% fibre | 1.6 g/cm3 | [Exel EXTENS technical datasheet](https://exelcomposites.com/wp-content/uploads/2020/06/EN_Exel-Extens-Technical-Datasheet.pdf) |

For O1, the catalog uses the sheet's specific gravity as the nominal g/cm³ equivalent and retains the printed imperial density in this evidence note. The Exel value is specific to the named pultruded plate, not a generic density for all carbon-fibre laminates.
