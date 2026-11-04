"""Minimal geometric hard checks for the M0 worker."""

from __future__ import annotations

from .types import Check, ModelContext


def hard_checks(ctx: ModelContext) -> list[Check]:
    """Check B-rep validity and that at least one solid is present."""
    if ctx.shape is None:
        return [
            Check(
                id="shape_present",
                suite="hard",
                level="error",
                status="fail",
                message="The model did not produce a B-rep shape.",
                expected="at least one solid",
                actual="no shape",
            )
        ]

    checks: list[Check] = []
    try:
        valid = bool(ctx.shape.is_valid)
        checks.append(
            Check(
                id="brep_valid",
                suite="hard",
                level="error",
                status="pass" if valid else "fail",
                message="BRepCheck reports the shape as valid." if valid else "BRepCheck reports the shape as invalid.",
                expected="valid B-rep",
                actual=str(valid).lower(),
            )
        )
    except Exception as exc:
        checks.append(
            Check(
                id="brep_valid",
                suite="hard",
                level="error",
                status="fail",
                message="B-rep validity could not be checked.",
                expected="valid B-rep",
                actual=f"{type(exc).__name__}: {exc}",
            )
        )

    try:
        solid_count = len(ctx.shape.solids())
        checks.append(
            Check(
                id="solid_count",
                suite="hard",
                level="error",
                status="pass" if solid_count >= 1 else "fail",
                message="The shape contains one or more solids." if solid_count >= 1 else "The shape contains no solids.",
                expected="at least 1",
                actual=str(solid_count),
            )
        )
    except Exception as exc:
        checks.append(
            Check(
                id="solid_count",
                suite="hard",
                level="error",
                status="fail",
                message="The solid count could not be checked.",
                expected="at least 1",
                actual=f"{type(exc).__name__}: {exc}",
            )
        )
    checks.append(Check(
        id="hard_gate_incomplete", suite="hard", level="error", status="skip",
        message="The M0 gate does not yet check closed shells, small topology, STEP round trips, or mesh watertightness; verification is withheld.",
    ))
    return checks
