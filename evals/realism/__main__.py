"""CLI for freezing, preparing, and scoring a blind realism-review study."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys

from .corpus_loader import load_corpus, tasks_for_split
from .packets import PacketError, prepare_study
from .scoring import ReviewError, score_study


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m evals.realism")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("freeze-check", help="validate the corpus, reference licenses, and pre-scoring split")
    prepare = commands.add_parser("prepare", help="make anonymous before/after section-image packets")
    prepare.add_argument("--before", required=True, type=Path, help="directory with <task_id>/*.png before images")
    prepare.add_argument("--after", required=True, type=Path, help="directory with <task_id>/*.png after images")
    prepare.add_argument("--output-dir", required=True, type=Path)
    prepare.add_argument("--reviewers", nargs="+", required=True, help="at least two anonymous reviewer IDs")
    prepare.add_argument("--split", choices=("tuning", "held_out"), default="tuning")
    score = commands.add_parser("score", help="aggregate completed reviewer JSON forms")
    score.add_argument("--prepared-dir", required=True, type=Path)
    score.add_argument("--ratings-dir", required=True, type=Path)
    score.add_argument("--output", required=True, type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "freeze-check":
            data, split = load_corpus()
            tasks_path = Path(__file__).resolve().parent / "corpus" / "tasks.json"
            print(json.dumps({
                "corpus_id": data["corpus_id"],
                "tasks_sha256": hashlib.sha256(tasks_path.read_bytes()).hexdigest(),
                "ranked": split["ranked"],
                "tuning_task_ids": [task["id"] for task in tasks_for_split(data, split, "tuning")],
                "held_out_task_ids": [task["id"] for task in tasks_for_split(data, split, "held_out")],
                "validated": True,
            }, indent=2))
            return 0
        if args.command == "prepare":
            summary = prepare_study(
                args.before,
                args.after,
                args.output_dir,
                reviewers=args.reviewers,
                split_name=args.split,
            )
            print(json.dumps(summary, indent=2))
            return 0
        if args.output.exists():
            raise ReviewError("report output already exists; choose a new path")
        report = score_study(args.prepared_dir, args.ratings_dir)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"Wrote review report to {args.output}")
        return 0
    except (OSError, PacketError, ReviewError, ValueError) as exc:
        print(f"realism evaluation error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
