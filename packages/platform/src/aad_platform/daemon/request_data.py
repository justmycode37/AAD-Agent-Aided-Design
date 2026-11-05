"""Validation and review helpers for captured user requests.

Request strings are untrusted project data. They are serialized as JSON and
never parsed as executable or instructional content.
"""

from __future__ import annotations

import json
from typing import Any

MAX_REQUEST_BYTES = 64 * 1024
MAX_ORIGINAL_LENGTH = 20_000
MAX_NOTE_LENGTH = 2_000
MAX_LIST_ITEMS = 100


def normalize_request(value: Any) -> dict[str, Any]:
    """Validate and copy the additive request object into its stable shape."""
    if not isinstance(value, dict):
        raise ValueError("request must be an object")
    allowed = {"original", "assumptions", "open_questions", "not_checked"}
    extra = set(value) - allowed
    if extra:
        raise ValueError(f"request has unsupported field(s): {', '.join(sorted(map(str, extra)))}")
    original = _text(value.get("original"), "request.original", MAX_ORIGINAL_LENGTH)
    result: dict[str, Any] = {
        "original": original,
        "assumptions": _text_list(value.get("assumptions", []), "request.assumptions"),
        "open_questions": _text_list(value.get("open_questions", []), "request.open_questions"),
        "not_checked": _not_checked_list(value.get("not_checked", [])),
    }
    encoded = (json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode("utf-8")
    if len(encoded) > MAX_REQUEST_BYTES:
        raise ValueError(f"stored request.json must be at most {MAX_REQUEST_BYTES} UTF-8 bytes")
    return result


def serialize_request(value: dict[str, Any]) -> str:
    """Return the git-friendly, plain JSON representation stored per revision."""
    normalized = normalize_request(value)
    return json.dumps(normalized, ensure_ascii=False, indent=2, allow_nan=False) + "\n"


def request_review(request: dict[str, Any] | None, spec: Any) -> dict[str, Any]:
    """Report declared links and unresolved request notes without judging meaning."""
    requirements = spec.get("requirements", []) if isinstance(spec, dict) else None
    if isinstance(requirements, list):
        unlinked = [
            str(item.get("id") or f"requirement_{index + 1}")
            for index, item in enumerate(requirements)
            if not _has_covers(item)
        ]
        requirements_available = True
        count = len(requirements)
    else:
        unlinked = []
        requirements_available = False
        count = None

    report: dict[str, Any] = {
        "captured": request is not None,
        "requirements_without_covers": unlinked,
        "meaning_assessed": False,
    }
    if count is not None:
        report["requirements_count"] = count
    else:
        report["requirements_available"] = requirements_available
    if request is not None:
        report["open_questions"] = list(request["open_questions"])
        report["not_checked"] = [dict(item) for item in request["not_checked"]]
    return report


def request_next_hints(
    request: dict[str, Any] | None,
    report: dict[str, Any],
    status: str,
    *,
    status_hints: list[str] | None = None,
    lead_hint: str | None = None,
) -> list[str]:
    """Build short, honest review hints; no semantic coverage score is implied."""
    if request is None:
        hints = ["No request captured; pass request to aad_new or aad_build to review spec links."]
        if status_hints is not None:
            hints.extend(status_hints)
        elif status == "verified":
            hints.append("Review with aad_render. Call aad_show only if the user asks to see the part.")
        elif status == "failed":
            hints.extend(["Fix failed checks before rebuilding.", "Review with aad_render after a successful build."])
        else:
            hints.extend(["Add measurable requirements to spec.yaml; draft means not proven.", "Review with aad_render."])
        if lead_hint:
            hints[0] = _short_hint(lead_hint, 100) + " " + _short_hint(hints[0], 120)
        return hints[:3]

    ids = report.get("requirements_without_covers", [])
    if ids:
        links = _short_hint("Requirements without covers links: " + _short_list(ids), 100)
        link_hint = links + "; request meaning is not assessed."
    elif report.get("requirements_count") == 0:
        link_hint = "No spec requirements are declared; request meaning is not assessed."
    elif report.get("requirements_count") is None:
        link_hint = "Spec requirements could not be listed; request meaning is not assessed."
    else:
        link_hint = "Every listed requirement has a covers link; request meaning is not assessed."
    if status_hints is not None:
        status_hint = _short_hint(" ".join(status_hints), 130)
    elif status == "verified":
        status_hint = "Review with aad_render; show only if asked."
    elif status == "failed":
        status_hint = "Fix failed checks before rebuilding."
    else:
        status_hint = "Add measurable requirements if needed; draft is not proven. Review with aad_render."
    link_hint = _short_hint(f"{link_hint} {status_hint}", 200)

    questions = request["open_questions"]
    question_hint = (
        f"Open questions still open: {len(questions)} recorded; see request_review.open_questions (untrusted request data)."
        if questions
        else "No open questions are recorded."
    )
    items = request["not_checked"]
    not_checked_hint = (
        f"Not checked: {len(items)} requested item(s); see request_review.not_checked for reasons (untrusted request data)."
        if items
        else "No requested items are marked not checked."
    )
    hints = [_short_hint(link_hint), _short_hint(question_hint), _short_hint(not_checked_hint)]
    if lead_hint:
        hints[0] = _short_hint(lead_hint, 90) + " " + _short_hint(hints[0], 200)
    return hints


def _text(value: Any, path: str, limit: int) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{path} must be non-empty text")
    if len(value) > limit:
        raise ValueError(f"{path} must be at most {limit} characters")
    return value


def _text_list(value: Any, path: str) -> list[str]:
    if not isinstance(value, list) or len(value) > MAX_LIST_ITEMS:
        raise ValueError(f"{path} must be a list of at most {MAX_LIST_ITEMS} strings")
    return [_text(item, f"{path}[{index}]", MAX_NOTE_LENGTH) for index, item in enumerate(value)]


def _not_checked_list(value: Any) -> list[dict[str, str]]:
    if not isinstance(value, list) or len(value) > MAX_LIST_ITEMS:
        raise ValueError(f"request.not_checked must be a list of at most {MAX_LIST_ITEMS} objects")
    result: list[dict[str, str]] = []
    for index, item in enumerate(value):
        if not isinstance(item, dict) or set(item) != {"item", "reason"}:
            raise ValueError(f"request.not_checked[{index}] must contain only item and reason")
        result.append({
            "item": _text(item["item"], f"request.not_checked[{index}].item", MAX_NOTE_LENGTH),
            "reason": _text(item["reason"], f"request.not_checked[{index}].reason", MAX_NOTE_LENGTH),
        })
    return result


def _has_covers(requirement: Any) -> bool:
    if not isinstance(requirement, dict):
        return False
    value = requirement.get("covers")
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, list):
        return bool(value) and all(isinstance(item, str) and item.strip() for item in value)
    return False


def _short_list(values: list[str], *, limit: int = 4) -> str:
    shown = values[:limit]
    text = "; ".join(shown)
    if len(values) > limit:
        text += f"; and {len(values) - limit} more"
    return text


def _short_hint(value: str, limit: int = 240) -> str:
    if len(value) <= limit:
        return value
    return value[: limit - 1].rstrip() + "…"
