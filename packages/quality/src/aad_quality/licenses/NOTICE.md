Standard-part adapter attribution

The selected size and nominal-length data in aad_quality.standard_parts are a subset adapted from gumyr/bd_warehouse v0.4.0, commit 8e9be0645b53db3d7414f74828c2d67b0da21138, licensed under Apache-2.0. Upstream: https://github.com/gumyr/bd_warehouse/tree/v0.4.0 . The upstream license is retained as bd_warehouse.txt.

Upstream source notices identify Copyright 2024 Gumyr (fastener.py), Copyright 2022 Gumyr (bearing.py), and Copyright 2023 Gumyr (thread.py). The AAD adapter is a new closed-registry wrapper; it selects a subset of upstream size/length choices and fixes construction modes. It does not vendor upstream constructors. Upstream modeling dimensions are not a certificate of hardware compliance, fit, or load rating.

The fastener, bearing, retaining-ring, key, O-ring and heat-set insert values in aad_quality/components/standard_tables.json are generated from the same bd_warehouse v0.4.0 data tables, licensed under Apache-2.0, with some hand-entered additions described in COMPONENTS.md. The component evidence file names KiCad library symbols and footprints as evidence that part numbers exist, and for some records repeats the datasheet link from the KiCad symbol's Datasheet property. It contains no KiCad symbol or footprint geometry.
