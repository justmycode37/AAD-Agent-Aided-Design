# Comparison rules and result log (first frozen 2026-11-05, before any model was scored)

## Definition of "99 % accuracy"
Gate metric: F@0.1%D >= 0.99.
- D = diagonal of the reference bounding box. tau = 0.001 * D.
- precision = share of model surface samples within tau of the reference surface.
- recall    = share of reference surface samples within tau of the model surface.
- F = 2PR / (P + R). Exact point-to-triangle distance, 100k area-weighted samples per side, fixed seed.
- Registration: rigid only. No scaling. No mirroring.
Reported, not gating: F at 0.05/0.25/0.5/1/2 % D, volumetric IoU, Chamfer, Hausdorff, bbox error.

Why this tolerance: at about 1 mm on a 1 m gearbox, gear teeth matter. A looser tolerance lets a smooth cylinder
stand in for a gear (checked in the harness self-test: gear vs. cylinder).

## Anti-gaming rules
1. model.py is generated B-rep from parameters. It must not import, embed, or trace reference geometry
   (no import_step/asset of the reference, no point lists or splines fitted through reference samples).
2. Metric and threshold above do not change after the first score. Any change is logged here with a reason,
   and scores are re-reported under the original rule too.
3. The first score, made before the reference was inspected, is kept as the BLIND result forever.
   Later iterations may take design parameters from the reference (tooth counts, widths, positions).
   Those scores measure reproduction of that reference, not independent design accuracy, and are labelled so.
4. A reference must be the same kind of object as the design. Scores against a different machine are calibration only.
5. Stop when the gate passes, or when 3 consecutive iterations gain < 0.5 points. Report the stop reason as-is.

## Amendment 1 (2026-11-05, BEFORE any image score): reference changed to pictures at the user's direction
No free, licence-usable 3D CAD reference of a real car DCT was found. The user directed: use detailed web pictures.
The 3D gate F@0.1%D above cannot run on pictures and is NOT replaced as a claim of 3D accuracy.

Image gate: IMG-99 = mean silhouette IoU >= 0.99, required separately on TUNING and HOLDOUT images.
- Each image gets its own rigid camera fit (rotation, translation, focal length). No scaling of the model along any axis,
  no mirroring, no per-image model changes. Holes are filled in both masks. Masks are checked by eye before scoring.
- Scale sanity: IoU 0.99 on a 1000 px-long gearbox is about 1.5 px mean outline error, roughly 1.3 mm, which is close to the
  original 1 mm tolerance.
- Reported, not gating: contour F at tau = 0.25 % of the mask diagonal, and per-image IoU.
- Image set, frozen now. Tuning: user_photo, p12 (Audi section view), p27 (Audi exterior).
  Holdout, never used to set a parameter: atm_transmission (far-side cutaway), tvs_dl501 (real exterior photo).
- Blind v1 was designed from user_photo and the Audi manual, so it is blind to the score, not to those two images.

What this can and cannot show: a silhouette measures only the outer outline from those viewpoints.
It says nothing about internal parts, wall thickness or 3D surfaces hidden from the camera.
Passing IMG-99 must never be reported as 99 % 3D or CAD accuracy.
Internal layout gets a separate, non-gating check: gear positions and sizes measured from Audi's side view (p14),
scaled by the published 89 mm shaft spacing.

Anti-tracing rule 1 still applies: parameters may be measured from pictures. Image outlines must not be traced into splines.

## Result log
- Blind v1 (frozen model, sha in git history is not available for it; see results.json): IMG-99 FAIL. Tuning IoU 0.8259, holdout IoU 0.8473. Contour F 0.130 / 0.221.
- After blind v1 the holdout overlays were viewed once on a shared sheet. To limit leakage, later iterations use only tuning overlays and holdout numbers.
- Later iterations use measured data: published tooth counts (DL501 data sheet) and Audi side view p14. They are reference-informed, not blind.
- tvs_dl501 mask retains a small shadow leak under the tail, and p12 shows a flange tail while the other pictures show a splined shaft. Both cap the reachable IoU slightly.
- v2 (AAD rev 3): IMG-99 FAIL. Tuning IoU 0.8993, holdout IoU 0.8756. Contour F 0.208 / 0.205. Camera focal was unbounded in this run (near-orthographic); from v3 it is capped at 60 x image width.
- Limit found: each picture has its own camera scale, so silhouettes test proportions, not absolute size. Absolute size comes only from the 89 mm shaft spacing via the side view.
- v3 (AAD rev 4, zero overlaps, all bodies watertight, spec 8/8 passes): IMG-99 FAIL. Tuning IoU 0.9152, holdout IoU 0.8737. Contour F 0.272 / 0.162.
  Overfitting signal: tuning rose 1.6 points, holdout fell 0.2. All three tuning images view the same side, so the far side is unconstrained.

## Amendment 2 (2026-11-05, before v4 scoring): tuning set extended
p20 (Audi exterior from another angle, cooler pipes masked out) joins TUNING because all earlier tuning views share one side.
Holdout set is unchanged. The tuning mean now covers four images, so v1-v3 tuning means are also recomputed on four for comparison.
Fitted parameters are bounded to physically plausible ranges, because v3's unbounded fit produced non-physical flange lobes.
- Four-image tuning means after adding p20: v1 0.8280, v2 0.8990, v3 0.9102. Holdout means unchanged: 0.8473, 0.8756, 0.8737.
- v4 (AAD rev 5, zero overlaps, all bodies watertight, spec 8/8 passes): IMG-99 FAIL. Tuning IoU 0.9129, holdout IoU 0.8724. Contour F 0.268 / 0.183.
- Limit found: user_photo, p12 and atm_transmission are cutaways. In p12 the upper outline along the rear half is the red cut surface, so it sits below the true housing. These silhouettes understate the real shape, and an uncut model cannot match them and the exterior views at once, so IMG-99 is probably unreachable with this image set.
- v5 plan: the fit weights exterior views (p27, p20) 3x over cutaways. The gate metric itself does not change.
- v5 (AAD rev 6): IMG-99 FAIL. Tuning IoU 0.9120, holdout IoU 0.8717. Contour F 0.312 / 0.180. One overlap left at build time, fixed in the final rebuild.
- STOP: the gate needs both means, and holdout is the limiting one. Holdout gain was below 0.5 points for three consecutive iterations (v3 -0.19, v4 -0.13, v5 -0.07). Tuning alone gained +1.12, +0.27, -0.09, so the stop is judged on holdout. Stop rule 5 fires.
- Final v5 rebuild (AAD rev 7, zero overlaps, all bodies watertight, spec 8/8 passes): IMG-99 FAIL. Tuning IoU 0.9095, holdout IoU 0.8681. Contour F 0.283 / 0.185. This replaces the rev 6 figures above as the v5 result. Holdout gain stays below 0.5 points (-0.43 from v4), so the stop stands.
