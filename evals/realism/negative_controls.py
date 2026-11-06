"""Synthetic rubric controls, not CAD artifacts or review outcomes."""

from __future__ import annotations

from typing import Any

from .scoring import weighted_checklist_score


def synthetic_control_score(task: dict[str, Any], control: str) -> dict[str, Any]:
    """Return a deliberately weak rating card for an exterior shell or labels.

    These fixtures test rubric arithmetic only. They do not show that a reviewer
    would rate a rendered shell or block model poorly: the floating-block control receives
    zero for every item because labels alone are not geometry evidence. The
    shell receives a single token-shape rating of one and zero elsewhere. They
    are not rendered objects, agent outputs, or realism scores.
    """
    if control not in {"exterior_only_shell", "labeled_floating_blocks"}:
        raise ValueError("unknown synthetic negative control")
    ratings = {item["id"]: 0 for item in task["checklist"]}
    if control == "exterior_only_shell":
        token_item = {
            "gearbox": "housing_and_case_joint",
            "small_drone": "pcb_structural_frame",
            "electronics_enclosure": "wall_strength_and_opening_edges",
        }[task["family"]]
        ratings[token_item] = 1
    return weighted_checklist_score(task, ratings)
