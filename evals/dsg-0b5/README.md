# 7-speed dual-clutch gearbox (Audi 0B5 / DL501)

A test of AAD on a large assembly: 247 solids, built through `aad_build`. The target was 99 % accuracy. **It did not reach 99 %**, and this folder records how far it got.

Status from AAD: `draft`. The 17 passing checks include the 8 requirements in `spec.yaml`. Those only prove what they state, such as part count, length and walls at named points. They do not prove the design matches a real gearbox.

## Results

No free, licence-usable 3D CAD model of a real car dual-clutch gearbox was found, so the user chose web pictures as the reference. The score is the outline overlap (IoU) with each picture, after a camera fit per picture. See `COMPARISON_RULES.md`.

| Version | Tuning pictures (4) | Held-out pictures (2) |
|---|---|---|
| v1, before any scoring | 82.8 % | 84.7 % |
| v2, measured layout and tooth counts | 89.9 % | 87.6 % |
| v3, fitted housing | 91.0 % | 87.4 % |
| v4, bounded fit, added a far-angle picture | 91.3 % | 87.2 % |
| v5, final | 91.0 % | 86.8 % |

Tuning scores rose while held-out scores stalled, so further fitting would be overfitting. The stop rule fired after v5.

## What the score does not show

- An outline says nothing about internal parts, wall thickness or surfaces hidden from the camera. It is not a 3D or CAD accuracy figure.
- Each picture gets its own camera scale, so outlines test proportions, not absolute size.
- Three pictures are cutaways whose outlines sit below the real housing in places. An uncut model cannot match those and the exterior views at once, so 99 % is probably out of reach with this picture set.

## Where the numbers come from

- **Published:** 89 mm shaft spacing, the two-clutch layout, and the tooth counts for all gears, from the sources below.
- **Measured from pictures:** axial gear positions and diameters from Audi's side view, scaled by the shaft spacing. Reading the same positions from a second Audi image agrees within 1.7 mm after a scale fit, but the two images differ in scale by about 6 %, so overall length is uncertain by that much.
- **Fitted:** housing wall radii and bosses, as bounded parameters. No outline was traced.
- **Assumed:** normal modules and so helix angles, front-axle shaft position, wall thickness, and everything hidden from the pictures. Bevel and crown teeth are tapered stand-ins, not true flank forms. Bearings, clutches and synchronisers are simplified.

## Sources

Not stored here because they belong to their owners.

- [Audi eSSP 950143, 0B5 transmission](https://content.datarunners.net/content/SSP/950143.pdf): pictures `p12`, `p20`, `p27`, side view `p14`
- [Audi Technology Portal, S tronic](https://www.audi-technology-portal.de/en/drivetrain/transmission-technologies/s-tronic_en): cutaway and schema
- [at-manuals.com, DL501](https://at-manuals.com/manuals/dsg-7-dl501-0b5-s-tronic/): `atm_transmission` and tooth-count sheet
- [TVS Engineering, DL501](https://tvsengineering.com/en/dsg-gearbox/dl501/): `tvs_dl501`

The harness in `tools/` is the throwaway code used for this run, kept so the scores can be reproduced. It needs `trimesh`, `vtk`, `opencv-python-headless` and `scipy`, plus the pictures above placed by hand. For 3D references, use `aad compare` or the `matches_reference` spec requirement.
