"""Isolated command target for the pilot's independent geometry audit."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from .corpus.loader import load_corpus
from .oracles import evaluate_task


def main() -> int:
    request = json.load(sys.stdin)
    if not isinstance(request, dict) or set(request) != {"task_id", "stage_evidence"}:
        raise ValueError("audit worker request must contain task_id and stage_evidence")
    corpus = load_corpus()
    task = next((item for item in corpus["tasks"] if item["id"] == request["task_id"]), None)
    if task is None:
        raise ValueError("audit worker task_id is not in the validated corpus")
    evidence = request["stage_evidence"]
    if not isinstance(evidence, dict):
        raise ValueError("stage_evidence must be an object")
    normalized: dict[str, dict[str, Any]] = {}
    for stage_id, row in evidence.items():
        if not isinstance(stage_id, str) or not isinstance(row, dict):
            raise ValueError("stage evidence rows must be objects keyed by stage ID")
        normalized[stage_id] = {
            key: str(Path(row[key])) if row.get(key) else None
            for key in ("brep_path", "sheet_json_path", "drawing_svg_path")
        }
        normalized[stage_id]["response_text"] = str(row.get("response_text", ""))
    audit = evaluate_task(task, normalized)
    print(json.dumps(audit.as_dict(), sort_keys=True, ensure_ascii=False, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
