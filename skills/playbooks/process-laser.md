---
id: process.laser.v1
title: Laser cutting
tags: [process, laser, 2d, kerf, contours]
candidate_status: unmeasured
---

# Laser cutting

Unmeasured candidate guidance. Ask for process, material, thickness, finish, and the actual fabricator before choosing feature sizes.

Treat the part as a 2D cut profile plus any separately specified forming or finishing steps. Check closed contours, duplicate paths, tabs or bridges, hole-to-edge spacing, and kerf compensation against the selected service's material-and-thickness rules. Do not infer a universal minimum cut or web size.

AIAD heuristic screen: laser requires kerf_mm to screen recognized through-like circular B-rep cuts against max(2 times kerf_mm, minimum_feature_mm when supplied). It does not screen general slots, open contours, kerf compensation, or the current 2D sheet API.

A pass covers only recognized circular cuts under declared assumptions. It is not proof of cut quality, contour validity, or material-specific feasibility.

Sources: [SendCutSend DFM lesson](https://sendcutsend.com/education/video/designing-parts-to-pass-dfm/) and [material-specific design specifications](https://sendcutsend.com/faq/what-are-the-material-specifications/).
