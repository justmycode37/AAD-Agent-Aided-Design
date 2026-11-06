"""Conservative review aggregation and reliability statistics."""

from __future__ import annotations

from collections import defaultdict
import hashlib
import itertools
import json
import math
import random
import statistics
from pathlib import Path
from typing import Any, Iterable

from evals.benchmark.statistics import MetricObservation, paired_task_bootstrap

from .corpus_loader import load_corpus, tasks_for_split


class ReviewError(ValueError):
    """A submitted rating is malformed or does not match the prepared study."""


def weighted_checklist_score(task: dict[str, Any], ratings: dict[str, int | None]) -> dict[str, Any]:
    """Score one checklist; unknown items prevent a complete task score."""
    checklist = task["checklist"]
    expected = {item["id"] for item in checklist}
    if set(ratings) - expected:
        raise ReviewError(f"unknown checklist item(s): {sorted(set(ratings) - expected)}")
    total_weight = sum(item["weight"] for item in checklist)
    known_weight = 0
    known_points = 0
    by_domain: dict[str, dict[str, float | int | None]] = {}
    for domain in ("subsystem", "interface"):
        items = [item for item in checklist if item["domain"] == domain]
        domain_weight = sum(item["weight"] for item in items)
        values = [(item, ratings.get(item["id"])) for item in items]
        assessed = [(item, value) for item, value in values if value is not None]
        weight = sum(item["weight"] for item, _ in assessed)
        points = sum(item["weight"] * value for item, value in assessed)
        known_weight += weight
        known_points += points
        complete = len(assessed) == len(items)
        by_domain[domain] = {
            "score_0_100": 100.0 * points / (4 * domain_weight) if complete else None,
            "known_only_score_0_100": 100.0 * points / (4 * weight) if weight else None,
            "known_items": len(assessed),
            "total_items": len(items),
            "weight_coverage": weight / domain_weight,
        }
    complete = known_weight == total_weight
    return {
        "score_0_100": 100.0 * known_points / (4 * total_weight) if complete else None,
        "known_only_score_0_100": 100.0 * known_points / (4 * known_weight) if known_weight else None,
        "complete": complete,
        "known_items": sum(value is not None for value in ratings.values()),
        "total_items": len(checklist),
        "weight_coverage": known_weight / total_weight,
        "by_domain": by_domain,
    }


def _cohen_weighted(left: list[int], right: list[int], *, quadratic: bool) -> float | None:
    if len(left) != len(right) or not left:
        return None
    levels = range(5)
    n = len(left)

    def weight(a: int, b: int) -> float:
        distance = abs(a - b) / 4
        return 1 - distance * distance if quadratic else 1 - distance

    observed = sum(weight(a, b) for a, b in zip(left, right)) / n
    left_marginal = [left.count(level) / n for level in levels]
    right_marginal = [right.count(level) / n for level in levels]
    expected = sum(
        left_marginal[a] * right_marginal[b] * weight(a, b)
        for a in levels for b in levels
    )
    if math.isclose(1 - expected, 0.0, abs_tol=1e-12):
        return None
    return (observed - expected) / (1 - expected)


def inter_rater_statistics(records: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """Pairwise exact agreement and weighted Cohen kappas across raters."""
    by_scope: dict[str, dict[tuple[str, str, str], dict[str, int]]] = defaultdict(lambda: defaultdict(dict))
    for record in records:
        rating = record.get("rating")
        if rating is None:
            continue
        scope = record["domain"]
        key = (record["task_id"], record["arm"], record["item_id"])
        by_scope[scope][key][record["reviewer_id"]] = rating
        by_scope["all"][key][record["reviewer_id"]] = rating

    result: dict[str, Any] = {}
    for scope, items in sorted(by_scope.items()):
        left_values: list[int] = []
        right_values: list[int] = []
        for raters in items.values():
            for first, second in itertools.combinations(sorted(raters), 2):
                left_values.append(raters[first])
                right_values.append(raters[second])
        pair_count = len(left_values)
        kappas = []
        for first, second in itertools.combinations(sorted({
            reviewer for raters in items.values() for reviewer in raters
        }), 2):
            pairs = [
                (raters[first], raters[second])
                for raters in items.values()
                if first in raters and second in raters
            ]
            if pairs:
                a, b = [x for x, _ in pairs], [y for _, y in pairs]
                kappas.append({
                    "reviewers": [first, second],
                    "n_paired_items": len(pairs),
                    "exact_agreement": sum(x == y for x, y in pairs) / len(pairs),
                    "linear_weighted_cohen_kappa": _cohen_weighted(a, b, quadratic=False),
                    "quadratic_weighted_cohen_kappa": _cohen_weighted(a, b, quadratic=True),
                })
        defined_linear = [item["linear_weighted_cohen_kappa"] for item in kappas if item["linear_weighted_cohen_kappa"] is not None]
        defined_quadratic = [item["quadratic_weighted_cohen_kappa"] for item in kappas if item["quadratic_weighted_cohen_kappa"] is not None]
        result[scope] = {
            "n_check_items_with_pair": len(items),
            "n_paired_ratings": pair_count,
            "exact_agreement": sum(a == b for a, b in zip(left_values, right_values)) / pair_count if pair_count else None,
            "mean_pairwise_linear_weighted_cohen_kappa": statistics.mean(defined_linear) if defined_linear else None,
            "mean_pairwise_quadratic_weighted_cohen_kappa": statistics.mean(defined_quadratic) if defined_quadratic else None,
            "pairwise": kappas,
            "kappa_note": "Pairwise ordinal agreement; undefined kappas are null when chance agreement leaves no denominator.",
        }
    return result


def _bootstrap_mean_ci(values: list[float], *, seed: int, resamples: int = 10000, confidence: float = 0.95) -> dict[str, Any]:
    if len(values) < 2:
        return {
            "estimate": statistics.mean(values) if values else None,
            "interval": None,
            "method": "reviewer percentile bootstrap",
            "confidence": confidence,
            "n_reviewers": len(values),
            "resamples": resamples,
            "seed": seed,
            "reason": "fewer than two independent reviewer scores",
        }
    rng = random.Random(seed)
    distribution = sorted(
        statistics.mean(rng.choices(values, k=len(values)))
        for _ in range(resamples)
    )

    def quantile(fraction: float) -> float:
        position = (len(distribution) - 1) * fraction
        low, high = math.floor(position), math.ceil(position)
        return distribution[low] + (position - low) * (distribution[high] - distribution[low])

    tail = (1 - confidence) / 2
    return {
        "estimate": statistics.mean(values),
        "interval": [quantile(tail), quantile(1 - tail)],
        "method": "two-sided percentile bootstrap over reviewer scores",
        "confidence": confidence,
        "n_reviewers": len(values),
        "resamples": resamples,
        "seed": seed,
        "assumptions": "Reviewers are exchangeable for the artifact; this interval describes reviewer-judgment uncertainty only, not task-population or engineering uncertainty.",
    }


def _stable_seed(text: str, base: int = 20261106) -> int:
    return base + int.from_bytes(hashlib.sha256(text.encode()).digest()[:4], "big")


def score_study(prepared_dir: Path, ratings_dir: Path) -> dict[str, Any]:
    """Score submitted blind forms; unknown evidence never becomes a pass."""
    prepared_dir, ratings_dir = Path(prepared_dir), Path(ratings_dir)
    try:
        study = json.loads((prepared_dir / "study.json").read_text(encoding="utf-8"))
        assignment_map = json.loads((prepared_dir / "restricted" / "assignment-map.json").read_text(encoding="utf-8"))
        image_hashes = json.loads((prepared_dir / "restricted" / "sanitized-image-sha256.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ReviewError(f"cannot read prepared study: {exc}") from exc
    data, split = load_corpus()
    if study.get("corpus_sha256") != split["tasks_sha256"] or assignment_map.get("tasks_sha256") != split["tasks_sha256"]:
        raise ReviewError("prepared study corpus hash does not match the frozen corpus")
    split_name = study.get("split")
    tasks = tasks_for_split(data, split, split_name)
    task_by_id = {task["id"]: task for task in tasks}
    reviewer_map = assignment_map.get("reviewers", {})
    expected_reviewers = set(reviewer_map)
    if len(expected_reviewers) < 2:
        raise ReviewError("at least two reviewers are required")
    try:
        input_files = {path.stem: path for path in ratings_dir.glob("*.json") if path.is_file()}
    except OSError as exc:
        raise ReviewError(f"cannot list rating forms: {exc}") from exc
    if set(input_files) - expected_reviewers:
        raise ReviewError("ratings include an unassigned reviewer")

    assignment_index: dict[str, dict[str, dict[str, str]]] = {}
    all_packet_ids: set[str] = set()
    for reviewer, assignments in reviewer_map.items():
        assignment_index[reviewer] = {}
        for entry in assignments:
            packet_id = entry.get("packet_id")
            if not isinstance(packet_id, str) or packet_id in all_packet_ids:
                raise ReviewError("packet assignment IDs must be unique opaque strings")
            if entry.get("task_id") not in task_by_id or entry.get("arm") not in {"before", "after"}:
                raise ReviewError("assignment map contains an unexpected task or arm")
            all_packet_ids.add(packet_id)
            assignment_index[reviewer][packet_id] = entry

    if not isinstance(image_hashes, dict):
        raise ReviewError("sanitized image hash manifest is invalid")
    item_records: list[dict[str, Any]] = []
    reviewer_packet_scores: dict[tuple[str, str, str], dict[str, Any]] = {}
    missing_forms: list[str] = sorted(expected_reviewers - set(input_files))
    missing_packets = []
    rating_records_submitted = 0
    for reviewer in sorted(expected_reviewers):
        expected_packets = assignment_index[reviewer]
        if reviewer in missing_forms:
            for packet_id, assignment in expected_packets.items():
                missing_packets.append({"reviewer_id": reviewer, "packet_id": packet_id, **assignment})
                task = task_by_id[assignment["task_id"]]
                reviewer_packet_scores[(reviewer, assignment["task_id"], assignment["arm"])] = weighted_checklist_score(task, {})
                for item in task["checklist"]:
                    item_records.append({
                        "reviewer_id": reviewer,
                        "task_id": assignment["task_id"],
                        "arm": assignment["arm"],
                        "item_id": item["id"],
                        "domain": item["domain"],
                        "rating": None,
                    })
            continue
        try:
            form = json.loads(input_files[reviewer].read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ReviewError(f"invalid rating form for {reviewer}: {exc}") from exc
        if form.get("schema_version") != 1 or form.get("reviewer_id") != reviewer or not isinstance(form.get("reviews"), list):
            raise ReviewError(f"rating form identity/schema mismatch for {reviewer}")
        observed: dict[str, dict[str, Any]] = {}
        for review in form["reviews"]:
            if not isinstance(review, dict) or review.get("packet_id") not in expected_packets:
                raise ReviewError(f"{reviewer} submitted an unassigned packet")
            packet_id = review["packet_id"]
            if packet_id in observed:
                raise ReviewError(f"{reviewer} submitted packet {packet_id} twice")
            items = review.get("items", [])
            if not isinstance(items, list):
                raise ReviewError(f"{reviewer}/{packet_id} items must be a list")
            item_map = {}
            for item in items:
                if not isinstance(item, dict) or not isinstance(item.get("id"), str) or item["id"] in item_map:
                    raise ReviewError(f"{reviewer}/{packet_id} has invalid or duplicate item")
                item_map[item["id"]] = item
            observed[packet_id] = item_map
        for packet_id, assignment in expected_packets.items():
            task_id, arm = assignment["task_id"], assignment["arm"]
            task = task_by_id[task_id]
            submitted = observed.get(packet_id, {})
            image_prefix = f"{reviewer}/{packet_id}/"
            allowed_views = {
                Path(path).name for path in image_hashes
                if isinstance(path, str) and path.startswith(image_prefix)
            }
            expected_ids = {item["id"] for item in task["checklist"]}
            if set(submitted) - expected_ids:
                raise ReviewError(f"{reviewer}/{packet_id} has unknown checklist items")
            score_values: dict[str, int | None] = {}
            for item in task["checklist"]:
                value = submitted.get(item["id"])
                rating = None if value is None else value.get("rating")
                confidence = None if value is None else value.get("confidence")
                evidence = [] if value is None else value.get("evidence", [])
                rationale = "" if value is None else value.get("rationale", "")
                if value is not None:
                    rating_records_submitted += 1
                if rating is not None and (type(rating) is not int or not 0 <= rating <= 4):
                    raise ReviewError(f"{reviewer}/{packet_id}/{item['id']} rating must be null or an integer from 0 to 4")
                if rating is not None and (type(confidence) is not int or not 1 <= confidence <= 4):
                    raise ReviewError(f"{reviewer}/{packet_id}/{item['id']} confidence must be an integer from 1 to 4")
                if rating is None and confidence is not None:
                    raise ReviewError(f"{reviewer}/{packet_id}/{item['id']} unassessable response must have null confidence")
                if not isinstance(evidence, list) or any(
                    not isinstance(name, str) or not re_full_view(name) for name in evidence
                ):
                    raise ReviewError(f"{reviewer}/{packet_id}/{item['id']} evidence must name packet view files")
                if any(name not in allowed_views for name in evidence):
                    raise ReviewError(f"{reviewer}/{packet_id}/{item['id']} evidence names an image outside this packet")
                if rating is not None and not evidence:
                    raise ReviewError(f"{reviewer}/{packet_id}/{item['id']} needs a view citation")
                if not isinstance(rationale, str) or len(rationale) > 1000:
                    raise ReviewError(f"{reviewer}/{packet_id}/{item['id']} rationale must be at most 1000 characters")
                if rating is None and value is not None and not rationale.strip():
                    raise ReviewError(f"{reviewer}/{packet_id}/{item['id']} unassessable response needs a reason")
                score_values[item["id"]] = rating
                item_records.append({
                    "reviewer_id": reviewer,
                    "task_id": task_id,
                    "arm": arm,
                    "item_id": item["id"],
                    "domain": item["domain"],
                    "rating": rating,
                    "confidence": confidence,
                    "evidence": evidence,
                    "rationale": rationale,
                })
            if packet_id not in observed:
                missing_packets.append({"reviewer_id": reviewer, "packet_id": packet_id, **assignment})
            reviewer_packet_scores[(reviewer, task_id, arm)] = weighted_checklist_score(task, score_values)

    # Item-level ratings aggregate across reviewers only for the same task/arm/item.
    item_groups: dict[tuple[str, str, str], list[int]] = defaultdict(list)
    item_raters: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for record in item_records:
        if record["rating"] is not None:
            key = (record["task_id"], record["arm"], record["item_id"])
            item_groups[key].append(record["rating"])
            item_raters[key].append(record)
    inter_rater = inter_rater_statistics(item_records)

    by_task_arm: dict[str, dict[str, Any]] = {}
    arm_task_values: dict[str, dict[str, float | None]] = {"before": {}, "after": {}}
    paired_reviewer_changes: dict[str, Any] = {}
    for task in tasks:
        task_id = task["id"]
        by_task_arm[task_id] = {}
        for arm in ("before", "after"):
            assignments = [reviewer for reviewer in reviewer_map if any(
                entry["task_id"] == task_id and entry["arm"] == arm
                for entry in reviewer_map[reviewer]
            )]
            values = [reviewer_packet_scores.get((reviewer, task_id, arm)) for reviewer in assignments]
            complete_values = [value["score_0_100"] for value in values if value and value["complete"]]
            complete = len(complete_values) == len(assignments) and bool(assignments)
            mean_score = statistics.mean(complete_values) if complete else None
            reviewer_ci = _bootstrap_mean_ci(
                [float(value) for value in complete_values],
                seed=_stable_seed(f"{task_id}:{arm}"),
            )
            by_task_arm[task_id][arm] = {
                "score_0_100": mean_score,
                "complete": complete,
                "n_reviewers_expected": len(assignments),
                "n_reviewers_complete": len(complete_values),
                "reviewer_ci_95": reviewer_ci,
                "reviewer_scores": complete_values,
                "subsystem": _mean_domain(values, "subsystem"),
                "interface": _mean_domain(values, "interface"),
            }
            arm_task_values[arm][task_id] = mean_score
        before_scores = {
            reviewer: reviewer_packet_scores.get((reviewer, task_id, "before"), {}).get("score_0_100")
            for reviewer in reviewer_map
        }
        after_scores = {
            reviewer: reviewer_packet_scores.get((reviewer, task_id, "after"), {}).get("score_0_100")
            for reviewer in reviewer_map
        }
        deltas = [after_scores[reviewer] - before_scores[reviewer] for reviewer in reviewer_map
                  if before_scores[reviewer] is not None and after_scores[reviewer] is not None]
        paired_reviewer_changes[task_id] = _bootstrap_mean_ci(
            [float(value) for value in deltas],
            seed=_stable_seed(f"{task_id}:paired-reviewer-delta"),
        )

    expected = [(task["id"], 1) for task in tasks]
    before_observations = [
        MetricObservation(task["id"], 1, arm_task_values["before"][task["id"]], "before")
        for task in tasks
    ]
    after_observations = [
        MetricObservation(task["id"], 1, arm_task_values["after"][task["id"]], "after")
        for task in tasks
    ]
    paired_delta = paired_task_bootstrap(
        after_observations,
        before_observations,
        expected=expected,
        statistic="difference",
        confidence=0.95,
        resamples=10000,
        seed=20261106,
    )
    arm_summary = {}
    for arm in ("before", "after"):
        values = [arm_task_values[arm][task["id"]] for task in tasks]
        complete_values = [float(value) for value in values if value is not None]
        arm_summary[arm] = {
            "n_tasks_expected": len(tasks),
            "n_tasks_complete": len(complete_values),
            "task_balanced_mean_0_100": statistics.mean(complete_values) if len(complete_values) == len(tasks) else None,
            "known_only_mean_0_100": statistics.mean(complete_values) if complete_values else None,
            "task_cluster_ci_95": _one_arm_task_ci(arm, tasks, arm_task_values[arm]),
        }

    return {
        "schema_version": 1,
        "study_id": study.get("corpus_sha256"),
        "corpus_id": data["corpus_id"],
        "corpus_sha256": split["tasks_sha256"],
        "split": split_name,
        "task_ids": [task["id"] for task in tasks],
        "measurement": "human weighted checklist score, 0-100",
        "interpretation": "Reviewer judgments against cited subsystem/interface checks. Not calibrated as an absolute realism measure and not proof of engineering correctness.",
        "arms": arm_summary,
        "paired_after_minus_before": paired_delta,
        "paired_reviewer_delta_ci_95_by_task": paired_reviewer_changes,
        "per_task": by_task_arm,
        "inter_rater": inter_rater,
        "denominators": {
            "reviewers_expected": len(expected_reviewers),
            "forms_missing": missing_forms,
            "packets_expected": sum(len(value) for value in reviewer_map.values()),
            "packets_missing": missing_packets,
            "checklist_items_per_review": sum(len(task["checklist"]) for task in tasks),
            "rating_records_expected": sum(len(task["checklist"]) * 2 for task in tasks) * len(expected_reviewers),
            "rating_records_submitted": rating_records_submitted,
            "rating_values_scored": sum(record["rating"] is not None for record in item_records),
            "unassessable_or_missing_ratings": sum(record["rating"] is None for record in item_records),
        },
        "confidence_interval_scope": {
            "reviewer_ci_95": "Two-sided percentile bootstrap over independent reviewers for one exact task/arm. It describes human judgment uncertainty only.",
            "task_cluster_ci_95": "Paired task-cluster percentile bootstrap reused from evals/benchmark/statistics.py; resamples independent frozen object clusters, never checklist items or repeated reviewer ratings. It is descriptive and needs at least two complete task clusters.",
            "paired_reviewer_delta_ci_95_by_task": "Two-sided percentile bootstrap over reviewers, paired within one exact task. It is not task-population uncertainty.",
        },
        "claim_limit": "No report or interval from this harness is a claim that a model is realistic, operational, safe, flightworthy, or manufacture-ready.",
    }


def re_full_view(value: str) -> bool:
    import re
    return re.fullmatch(r"view-[0-9]{2}\.png", value) is not None


def _mean_domain(values: list[dict[str, Any] | None], domain: str) -> float | None:
    scores = [value["by_domain"][domain]["score_0_100"] for value in values if value and value["complete"]]
    if len(scores) != len(values) or not scores:
        return None
    return statistics.mean(scores)


def _one_arm_task_ci(arm: str, tasks: list[dict[str, Any]], values: dict[str, float | None]) -> dict[str, Any]:
    observations = [MetricObservation(task["id"], 1, values[task["id"]], arm) for task in tasks]
    zeroes = [MetricObservation(task["id"], 1, 0.0, "zero") for task in tasks]
    result = paired_task_bootstrap(
        observations,
        zeroes,
        expected=[(task["id"], 1) for task in tasks],
        statistic="difference",
        confidence=0.95,
        resamples=10000,
        seed=_stable_seed(f"arm:{arm}"),
    )
    if result["complete"]:
        return {"estimate": result["estimate"], "interval": result["interval"], "method": result["method"], "confidence": 0.95, "n_task_clusters": len(tasks), "resamples": 10000, "seed": result["seed"], "assumptions": result["assumptions"]}
    complete_values = [values[task["id"]] for task in tasks if values[task["id"]] is not None]
    return {
        "estimate": statistics.mean(complete_values) if len(complete_values) == len(tasks) and complete_values else None,
        "interval": None,
        "method": result["method"],
        "confidence": 0.95,
        "n_task_clusters": len(complete_values),
        "reason": result.get("reason", "incomplete task observations"),
    }
