"""Builds the result envelope and its short text (CONTRACTS section 6)."""

from __future__ import annotations

from typing import Any

MAX_NEXT = 3


def compact(value: Any) -> Any:
    """Drop None and empty containers. Unknown is omitted, never invented."""
    if isinstance(value, dict):
        items = ((k, compact(v)) for k, v in value.items())
        return {k: v for k, v in items if v is not None and v != [] and v != {} and v != ""}
    if isinstance(value, list):
        return [compact(v) for v in value]
    return value


def failure_envelope(
    project: str,
    code: str,
    phase: str,
    message: str,
    *,
    head_rev: int | None = None,
    base_rev: int | None = None,
    **extra: Any,
) -> dict[str, Any]:
    failure = compact({"code": code, "phase": phase, "message": message, "last_good_rev": head_rev, **extra})
    return compact(
        {
            "ok": False,
            "project": project,
            "status": "failed",
            "base_rev": base_rev,
            "head_rev": head_rev,
            "summary": f"{code}: {message}",
            "failure": failure,
        }
    )


def sort_checks(checks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Failures and warnings first. Passes are collapsed into checks_summary."""
    order = {"fail": 0, "warn": 1, "skip": 2}
    kept = [c for c in checks if c.get("status") != "pass"]
    return sorted(kept, key=lambda c: order.get(c.get("status", ""), 3))


def _number(value: float) -> str:
    return f"{value:.4g}" if abs(value) < 1e6 else f"{value:.0f}"


def text_for(envelope: dict[str, Any]) -> str:
    """Stands alone for the model: status, key numbers, failed checks and next steps."""
    lines = [envelope.get("summary") or envelope.get("status", "")]
    metrics = envelope.get("metrics") or {}
    if metrics:
        units = metrics.get("units", "")
        bbox = " x ".join(_number(v) for v in metrics.get("bbox", []))
        parts = [f"bbox {bbox} {units}".strip()] if bbox else []
        if "volume" in metrics:
            parts.append(f"volume {_number(metrics['volume'])} {units}^3")
        if "mass_g" in metrics:
            parts.append(f"mass {_number(metrics['mass_g'])} g")
        if "solids" in metrics:
            parts.append(f"{metrics['solids']} solid(s), {metrics.get('faces', '?')} faces")
        if "valid" in metrics:
            parts.append("valid" if metrics["valid"] else "INVALID")
        lines.append(", ".join(parts))
    summary = envelope.get("checks_summary")
    if summary:
        lines.append(
            "Checks: "
            + ", ".join(f"{summary.get(key, 0)} {key}" for key in ("pass", "fail", "warn", "skip"))
        )
    for check in (envelope.get("checks") or [])[:5]:
        if check.get("status") in ("fail", "warn"):
            lines.append(f"- {check['status'].upper()} {check.get('id')}: {check.get('message', '')}")
    failure = envelope.get("failure")
    if failure:
        where = ", ".join(
            f"{key} {failure[key]}" for key in ("feature", "line") if failure.get(key) is not None
        )
        if where:
            lines.append(f"At {where}.")
        for repair in (failure.get("repair") or [])[:3]:
            lines.append(f"- Try: {repair}")
    viewer = envelope.get("viewer") or {}
    if viewer.get("url"):
        lines.append(f"Viewer window: {viewer['url']}")
    for hint in (envelope.get("next") or [])[:MAX_NEXT]:
        lines.append(f"Next: {hint}")
    return "\n".join(line for line in lines if line)
