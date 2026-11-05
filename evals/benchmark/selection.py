"""Frozen, outcome-blind task selection for the S5 20-task pilot.

The selection is a hash-ranked stratified sample from the public task IDs.  The
same rule can regenerate ``pilot-selection.json`` without consulting oracle
support, reference builders, task difficulty, or prior results.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

from .corpus.loader import TASKS_PATH, load_corpus


SEED = "aad-benchmark-pilot-v1"
QUOTAS = {
    "part": 5,
    "edit_chain": 4,
    "assembly": 3,
    "2d_profile": 3,
    "drawing": 2,
    "clarification": 2,
    "infeasible": 1,
}
SELECTION_PATH = Path(__file__).resolve().with_name("pilot-selection.json")


def _rank(category: str, task_id: str) -> str:
    return hashlib.sha256(f"{SEED}\0{category}\0{task_id}".encode("utf-8")).hexdigest()


def _order_rank(task_id: str) -> str:
    return hashlib.sha256(f"{SEED}\0order\0{task_id}".encode("utf-8")).hexdigest()


def selection_document(corpus: Mapping[str, Any], *, corpus_bytes: bytes) -> dict[str, Any]:
    """Return the complete deterministic ranking and chosen task order."""
    strata: dict[str, Any] = {}
    selected: list[tuple[str, str]] = []
    tasks = corpus.get("tasks")
    if not isinstance(tasks, list):
        raise ValueError("corpus must contain a tasks list")

    for category, quota in QUOTAS.items():
        candidates = sorted(
            (task["id"] for task in tasks if task.get("category") == category),
            key=lambda task_id: (_rank(category, task_id), task_id),
        )
        if len(candidates) < quota:
            raise ValueError(f"stratum {category!r} has {len(candidates)} tasks; needs {quota}")
        rankings = [{"task_id": task_id, "sha256": _rank(category, task_id)} for task_id in candidates]
        chosen = candidates[:quota]
        selected.extend((category, task_id) for task_id in chosen)
        strata[category] = {
            "population_n": len(candidates),
            "quota": quota,
            "ranked": rankings,
            "selected_task_ids": chosen,
        }

    selected_by_order = sorted(selected, key=lambda item: (_order_rank(item[1]), item[1]))
    ordered_ids = [task_id for _, task_id in selected_by_order]
    return {
        "schema_version": 1,
        "selection_id": "s5-benchmark-pilot-20-v1",
        "seed": SEED,
        "rule": (
            "Within each predeclared task-category stratum, rank every task by "
            "SHA-256(seed + NUL + category + NUL + task_id), ascending, and take "
            "the first fixed quota. Order the chosen tasks by SHA-256(seed + NUL "
            "+ 'order' + NUL + task_id), ascending. No difficulty, oracle support, "
            "reference availability, or observed outcome is used to select tasks."
        ),
        "corpus_sha256": hashlib.sha256(corpus_bytes).hexdigest(),
        "quotas": dict(QUOTAS),
        "strata": strata,
        "run_order": ordered_ids,
        "stage_count": sum(
            len(task["stages"])
            for task in tasks
            if task["id"] in set(ordered_ids)
        ),
    }


def load_selection(
    *, corpus_path: Path = TASKS_PATH, selection_path: Path = SELECTION_PATH
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Load the corpus and reject a stale or edited selection manifest."""
    corpus_bytes = corpus_path.read_bytes()
    corpus = load_corpus(corpus_path, corpus_dir=corpus_path.parent)
    expected = selection_document(corpus, corpus_bytes=corpus_bytes)
    recorded = json.loads(selection_path.read_text(encoding="utf-8"))
    if recorded != expected:
        raise ValueError(
            "pilot-selection.json is stale or does not match the frozen hash-ranking rule; "
            "regenerate it with python -m evals.benchmark.selection --write"
        )
    return corpus, recorded


def write_selection(path: Path = SELECTION_PATH) -> dict[str, Any]:
    corpus_bytes = TASKS_PATH.read_bytes()
    corpus = load_corpus(TASKS_PATH, corpus_dir=TASKS_PATH.parent)
    value = selection_document(corpus, corpus_bytes=corpus_bytes)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return value


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="store_true", help="write the frozen selection manifest")
    args = parser.parse_args(argv)
    if args.write:
        selection = write_selection()
    else:
        _, selection = load_selection()
    print(json.dumps({
        "selection_id": selection["selection_id"],
        "selected": len(selection["run_order"]),
        "stages": selection["stage_count"],
        "run_order": selection["run_order"],
    }, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
