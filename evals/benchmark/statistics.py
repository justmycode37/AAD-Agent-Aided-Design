"""Task-clustered summaries; statistical evidence alone is not a release gate.

Inputs must come from the host's validated run/audit manifests. These helpers do
not authenticate provenance or prove that a declared sampling plan was followed.
No generated Python, AAD check, or model-provided measurement is evaluated here.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import asdict, dataclass
import math
import random
import re
import statistics
from typing import Iterable, Mapping


def _finite(value: object, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field} must be a finite number")
    try:
        result = float(value)
    except OverflowError as error:
        raise ValueError(f"{field} must be a finite number") from error
    if not math.isfinite(result):
        raise ValueError(f"{field} must be a finite number")
    return result


def _confidence(value: object) -> float:
    result = _finite(value, "confidence")
    if not 0 < result < 1:
        raise ValueError("confidence must be strictly between zero and one")
    return result


def _identity(task_id: object, repetition: object) -> None:
    if not isinstance(task_id, str) or not task_id.strip():
        raise ValueError("task_id must be nonempty")
    if type(repetition) is not int or repetition < 1:
        raise ValueError("repetition must be a positive integer")


@dataclass(frozen=True)
class MetricObservation:
    task_id: str
    repetition: int
    value: float | None
    arm_id: str

    def __post_init__(self) -> None:
        _identity(self.task_id, self.repetition)
        if not isinstance(self.arm_id, str) or not self.arm_id.strip():
            raise ValueError("arm_id must bind one provider/model/backend/budget/ablation configuration")
        if self.value is not None:
            _finite(self.value, "value")


def _index(points: Iterable[MetricObservation]) -> dict[tuple[str, int], float | None]:
    result: dict[tuple[str, int], float | None] = {}
    arms = set()
    for point in points:
        if not isinstance(point, MetricObservation):
            raise ValueError("expected MetricObservation records")
        arms.add(point.arm_id)
        if len(arms) > 1:
            raise ValueError("mixed comparison arms")
        key = (point.task_id, point.repetition)
        if key in result:
            raise ValueError(f"duplicate task/repetition: {key!r}")
        result[key] = None if point.value is None else float(point.value)
    return result


def _expected_keys(expected: Iterable[tuple[str, int]]) -> set[tuple[str, int]]:
    result = set()
    for key in expected:
        if not isinstance(key, tuple) or len(key) != 2:
            raise ValueError("expected matrix entries must be task/repetition tuples")
        _identity(*key)
        if key in result:
            raise ValueError("duplicate cell in expected run matrix")
        result.add(key)
    return result


def task_summary(points: Iterable[MetricObservation], *, expected: Iterable[tuple[str, int]]) -> dict:
    """Equal weight per task; missing observations are counted, never zeroed.

    A task with any missing value has an unknown mean. Known-only means remain
    visible for diagnosis but cannot be mistaken for a complete headline result.
    Sample standard deviations use n-1; n < 2 is unknown, not zero variance.
    """
    points = list(points)
    indexed = _index(points)
    planned = _expected_keys(expected)
    missing = sorted(planned - set(indexed))
    unexpected = sorted(set(indexed) - planned)
    grouped: dict[str, list[tuple[int, float | None]]] = defaultdict(list)
    for (task_id, repeat), value in indexed.items():
        grouped[task_id].append((repeat, value))
    tasks = []
    for task_id, items in sorted(grouped.items()):
        items.sort()
        known = [value for _, value in items if value is not None]
        complete = len(known) == len(items)
        tasks.append({
            "task_id": task_id,
            "n_repetitions": len(items),
            "n_known": len(known),
            "n_unknown": len(items) - len(known),
            "repetitions": [repeat for repeat, _ in items],
            "mean": statistics.mean(known) if known and complete else None,
            "known_only_mean": statistics.mean(known) if known else None,
            "sample_sd": statistics.stdev(known) if complete and len(known) > 1 else None,
            "known_only_sample_sd": statistics.stdev(known) if len(known) > 1 else None,
        })
    means = [task["mean"] for task in tasks if task["mean"] is not None]
    complete = bool(tasks) and len(means) == len(tasks) and not missing and not unexpected
    return {
        "unit": "task; repeated trials are clustered within their task",
        "n_tasks": len(tasks),
        "arm_id": points[0].arm_id if points else None,
        "n_expected_trials": len(planned),
        "missing": missing,
        "unexpected": unexpected,
        "n_trials": len(indexed),
        "n_unknown": sum(value is None for value in indexed.values()),
        "complete": complete,
        "task_balanced_mean": statistics.mean(means) if complete else None,
        "complete_task_subset_mean": statistics.mean(means) if means else None,
        "between_task_sample_sd": statistics.stdev(means) if complete and len(means) > 1 else None,
        "tasks": tasks,
    }


def _quantile(sorted_values: list[float], fraction: float) -> float:
    position = (len(sorted_values) - 1) * fraction
    lower = math.floor(position)
    upper = math.ceil(position)
    return sorted_values[lower] + (position - lower) * (
        sorted_values[upper] - sorted_values[lower]
    )


def paired_task_bootstrap(
    candidate: Iterable[MetricObservation],
    baseline: Iterable[MetricObservation],
    *,
    expected: Iterable[tuple[str, int]],
    statistic: str = "difference",
    confidence: float = 0.95,
    resamples: int = 10_000,
    seed: int = 0,
) -> dict:
    """Descriptive paired percentile bootstrap of complete task clusters.

    Both arms must contain the exact same (task, repetition) keys. Every task is
    weighted equally after averaging its repetitions. Resampling shares task
    indices between arms. Ratio means ratio of task-balanced means, not mean of
    per-task ratios. The caller must also match provider/model/budget/task hashes.
    This estimate assumes exchangeable task clusters; it is not itself a release
    decision, a proof of representativeness, or a multiplicity correction.
    """
    confidence = _confidence(confidence)
    if statistic not in {"difference", "ratio"}:
        raise ValueError("statistic must be difference or ratio")
    if type(resamples) is not int or not 1_000 <= resamples <= 1_000_000:
        raise ValueError("resamples must be an integer from 1000 to 1000000")
    if type(seed) is not int:
        raise ValueError("seed must be an integer")
    candidate, baseline = list(candidate), list(baseline)
    left, right = _index(candidate), _index(baseline)
    planned = _expected_keys(expected)
    missing_left = sorted(planned - set(left))
    missing_right = sorted(planned - set(right))
    unexpected = sorted((set(left) | set(right)) - planned)
    result = {
        "method": "paired task-cluster percentile bootstrap",
        "statistic": statistic,
        "confidence": confidence,
        "resamples": resamples,
        "seed": seed,
        "complete": False,
        "n_tasks": len({key[0] for key in set(left) | set(right)}),
        "candidate_missing": missing_left,
        "baseline_missing": missing_right,
        "unexpected": unexpected,
        "n_expected_trials": len(planned),
        "candidate_arm_id": candidate[0].arm_id if candidate else None,
        "baseline_arm_id": baseline[0].arm_id if baseline else None,
        "estimate": None,
        "interval": None,
        "bootstrap_sample_sd": None,
    }
    if missing_left or missing_right or unexpected:
        return {**result, "reason": "unpaired task/repetition matrix"}
    if not left or any(value is None for value in (*left.values(), *right.values())):
        return {**result, "reason": "empty or unknown observations"}
    task_ids = sorted({key[0] for key in left})
    if len(task_ids) < 2:
        return {**result, "reason": "fewer than two independent task clusters"}
    candidate_means, baseline_means = [], []
    for task_id in task_ids:
        keys = sorted(key for key in left if key[0] == task_id)
        candidate_means.append(statistics.mean(left[key] for key in keys))
        baseline_means.append(statistics.mean(right[key] for key in keys))
    if statistic == "ratio" and (
        any(value < 0 for value in candidate_means)
        or any(value <= 0 for value in baseline_means)
    ):
        return {**result, "reason": "ratio requires nonnegative numerator and positive denominator per task"}

    def calculate(indices: list[int]) -> float:
        a = statistics.mean(candidate_means[index] for index in indices)
        b = statistics.mean(baseline_means[index] for index in indices)
        return a - b if statistic == "difference" else a / b

    estimate = calculate(list(range(len(task_ids))))
    rng = random.Random(seed)
    distribution = sorted(
        calculate(rng.choices(range(len(task_ids)), k=len(task_ids)))
        for _ in range(resamples)
    )
    if not math.isfinite(estimate) or not all(math.isfinite(x) for x in distribution):
        return {**result, "reason": "nonfinite computed statistic"}
    tail = (1 - confidence) / 2
    return {
        **result,
        "complete": True,
        "task_ids": task_ids,
        "estimate": estimate,
        "interval": [_quantile(distribution, tail), _quantile(distribution, 1 - tail)],
        "bootstrap_sample_sd": statistics.stdev(distribution),
        "degenerate": distribution[0] == distribution[-1],
        "assumptions": "Exchangeable task clusters; paired models, budgets and public tasks checked by caller. Repetitions are not independent tasks. Percentile intervals are descriptive, including degenerate intervals.",
    }


def task_cluster_bootstrap(
    observations: Iterable[MetricObservation],
    *,
    expected: Iterable[tuple[str, int]],
    strata: Mapping[str, str],
    population_counts: Mapping[str, int],
    seed: int,
    resamples: int = 10000,
    confidence: float = 0.95,
) -> dict:
    """Stratified percentile bootstrap with task IDs as the sampling unit.

    The headline statistic is the population-size-weighted mean of each
    stratum's task means. All planned cells must be present and known; repeated
    observations are first averaged within task. Singleton strata are retained
    and reported as degenerate rather than silently dropped.
    """
    confidence = _confidence(confidence)
    if type(seed) is not int:
        raise ValueError("seed must be an integer")
    if type(resamples) is not int or resamples < 100:
        raise ValueError("resamples must be an integer of at least 100")
    points = list(observations)
    indexed = _index(points)
    planned = _expected_keys(expected)
    missing = sorted(planned - set(indexed))
    unexpected = sorted(set(indexed) - planned)
    unknown = sorted(key for key in planned & set(indexed) if indexed[key] is None)
    planned_tasks = {task_id for task_id, _ in planned}
    if set(strata) != planned_tasks:
        raise ValueError("strata must name each expected task exactly once")
    if not isinstance(population_counts, Mapping) or not population_counts:
        raise ValueError("population_counts must define the selected strata")
    if any(not isinstance(name, str) or type(count) is not int or count <= 0 for name, count in population_counts.items()):
        raise ValueError("population_counts must contain positive integer task counts")
    if set(strata.values()) != set(population_counts):
        raise ValueError("population_counts strata must match the expected task strata")
    task_values: dict[str, list[float]] = {task_id: [] for task_id in planned_tasks}
    for (task_id, _), value in indexed.items():
        if task_id in task_values and value is not None:
            task_values[task_id].append(float(value))
    missing_tasks = [task_id for task_id, values in task_values.items() if not values]
    if missing or unexpected or unknown or missing_tasks:
        return {
            "estimate": None,
            "confidence_interval": None,
            "method": "stratified task-cluster percentile bootstrap",
            "confidence": confidence,
            "seed": seed,
            "resamples": resamples,
            "expected_cells": len(planned),
            "observed_cells": len(indexed),
            "missing": [list(item) for item in missing],
            "unexpected": [list(item) for item in unexpected],
            "unknown": [list(item) for item in unknown],
            "missing_tasks": missing_tasks,
            "reason": "the planned task matrix is incomplete or has unknown observations",
        }

    means: dict[str, list[float]] = {name: [] for name in population_counts}
    for task_id, values in task_values.items():
        means[strata[task_id]].append(sum(values) / len(values))
    if any(not values or len(values) > population_counts[name] for name, values in means.items()):
        raise ValueError("each stratum must have a nonempty sample no larger than its population")
    population_total = sum(population_counts.values())

    def weighted_mean(sampled: Mapping[str, list[float]]) -> float:
        return sum(
            population_counts[name] / population_total * (sum(values) / len(values))
            for name, values in sampled.items()
        )

    estimate = weighted_mean(means)
    rng = random.Random(seed)
    draws = []
    for _ in range(resamples):
        sampled = {
            name: [rng.choice(values) for _ in range(len(values))]
            for name, values in means.items()
        }
        draws.append(weighted_mean(sampled))
    draws.sort()

    def quantile(probability: float) -> float:
        position = probability * (len(draws) - 1)
        lower = math.floor(position)
        upper = math.ceil(position)
        if lower == upper:
            return draws[lower]
        fraction = position - lower
        return draws[lower] * (1 - fraction) + draws[upper] * fraction

    alpha = 1 - confidence
    return {
        "estimate": estimate,
        "confidence_interval": [quantile(alpha / 2), quantile(1 - alpha / 2)],
        "method": "stratified task-cluster percentile bootstrap",
        "confidence": confidence,
        "seed": seed,
        "resamples": resamples,
        "expected_cells": len(planned),
        "observed_cells": len(indexed),
        "n_tasks": len(planned_tasks),
        "by_stratum": {
            name: {"selected_n": len(means[name]), "population_n": population_counts[name]}
            for name in sorted(means)
        },
        "degenerate_strata": sorted(name for name, values in means.items() if len(values) == 1),
        "sampling_unit": "task cluster; repetitions averaged within task",
        "assumptions": "within-stratum hash-ranked tasks are exchangeable samples; this interval is descriptive and does not establish corpus representativeness",
    }


@dataclass(frozen=True)
class AuditObservation:
    task_id: str
    repetition: int
    native_verified: bool | None
    verdict: str  # good, bad, indeterminate
    coverage: str  # complete, bounded
    origin: str  # real_agent, scripted_control
    arm_id: str

    def __post_init__(self) -> None:
        _identity(self.task_id, self.repetition)
        if not isinstance(self.arm_id, str) or not self.arm_id.strip():
            raise ValueError("arm_id must identify one comparison configuration")
        if self.native_verified is not None and type(self.native_verified) is not bool:
            raise ValueError("native_verified must be boolean or None")
        if self.verdict not in {"good", "bad", "indeterminate"}:
            raise ValueError("invalid audit verdict")
        if self.coverage not in {"complete", "bounded"}:
            raise ValueError("invalid audit coverage")
        if self.origin not in {"real_agent", "scripted_control"}:
            raise ValueError("invalid origin")
        if self.verdict == "good" and self.coverage != "complete":
            raise ValueError("bounded witnesses cannot establish a good original-request audit")


def exact_binomial_upper(bad: int, n: int, confidence: float = 0.95) -> dict:
    """One-sided Clopper-Pearson bound; empty denominator remains unknown."""
    confidence = _confidence(confidence)
    if type(bad) is not int or type(n) is not int or not 0 <= bad <= n:
        raise ValueError("counts must be integers with 0 <= bad <= n")
    if n == 0:
        return {"upper": None, "method": "one-sided Clopper-Pearson", "confidence": confidence}
    import scipy
    from scipy.stats import binomtest

    interval = binomtest(bad, n, alternative="less").proportion_ci(
        confidence_level=confidence, method="exact"
    )
    return {"upper": float(interval.high), "method": "one-sided Clopper-Pearson", "confidence": confidence, "scipy_version": scipy.__version__}


def false_verified_summary(
    observations: Iterable[AuditObservation],
    *,
    selected: Iterable[tuple[str, int]],
    arm_id: str,
    sampling_plan_sha256: str,
    sampling_assumptions: str,
    host_manifest_validated: bool = False,
    confidence: float = 0.95,
    threshold: float = 0.05,
) -> dict:
    """Audit a predeclared sample with at most one selected trial per task.

    Selection must precede examining outcomes. A host manifest must establish
    this externally; a hash or string here is not proof. Exact-binomial inference
    is conditional on selected trials that make a native verified claim, under
    the declared sampling assumptions. It does not pool repeated trials, model
    families or scripted controls. Invoke separately for each comparison arm.
    """
    confidence = _confidence(confidence)
    if not isinstance(arm_id, str) or not arm_id.strip():
        raise ValueError("arm_id is required")
    threshold = _finite(threshold, "threshold")
    if not 0 < threshold < 1:
        raise ValueError("threshold must be strictly between zero and one")
    if not isinstance(sampling_plan_sha256, str) or not re.fullmatch(r"[0-9a-f]{64}", sampling_plan_sha256):
        raise ValueError("a sampling plan SHA-256 is required")
    if not isinstance(sampling_assumptions, str) or not sampling_assumptions.strip():
        raise ValueError("sampling assumptions are required")
    if type(host_manifest_validated) is not bool:
        raise ValueError("host_manifest_validated must be a bool")
    indexed = {}
    for point in observations:
        if not isinstance(point, AuditObservation):
            raise ValueError("expected AuditObservation records")
        if point.arm_id != arm_id:
            raise ValueError("mixed comparison arms in native false-verified audit")
        key = point.task_id, point.repetition
        if key in indexed:
            raise ValueError("duplicate task/repetition audit")
        indexed[key] = point
    sample = list(selected)
    tasks = set()
    for key in sample:
        if not isinstance(key, tuple) or len(key) != 2:
            raise ValueError("sample entries must be task/repetition tuples")
        _identity(*key)
        if key[0] in tasks:
            raise ValueError("multiple selected trials of one task are not independent samples")
        tasks.add(key[0])
    missing = [key for key in sample if key not in indexed]
    points = [indexed[key] for key in sample if key in indexed]
    controls = [asdict(point) for point in points if point.origin != "real_agent"]
    unknown_claims = sum(point.native_verified is None for point in points)
    verified = [point for point in points if point.native_verified is True]
    counts = {verdict: sum(point.verdict == verdict for point in verified) for verdict in ("good", "bad", "indeterminate")}
    n = len(verified)
    blockers = []
    if not host_manifest_validated:
        blockers.append("authenticated host manifest validation is required")
    if not sample:
        blockers.append("empty independent audit sample")
    if missing:
        blockers.append("selected trials missing audit records")
    if controls:
        blockers.append("scripted controls are not sampled real-agent outcomes")
    if unknown_claims:
        blockers.append("unknown native verification claims")
    if not n:
        blockers.append("empty native-verified denominator")
    if counts["indeterminate"]:
        blockers.append("indeterminate original-request audits")
    # A failure witness proves bad even under bounded coverage. An unresolved
    # bounded audit cannot become good and therefore blocks the upper-bound claim.
    inference = exact_binomial_upper(counts["bad"], n, confidence) if not blockers else {
        "upper": None, "method": "one-sided Clopper-Pearson", "confidence": confidence,
    }
    return {
        "arm_id": arm_id,
        "sampling_plan_sha256": sampling_plan_sha256,
        "sampling_assumptions": sampling_assumptions,
        "host_manifest_validated": host_manifest_validated,
        "inference_scope": "selected native-verified trials, at most one trial per task, one comparison arm",
        "selected": [list(key) for key in sample],
        "n_selected_tasks": len(sample),
        "n_verified": n,
        **counts,
        "unknown_native_claims": unknown_claims,
        "missing": [list(key) for key in missing],
        "scripted_controls": controls,
        "observed_bad_fraction": counts["bad"] / n if n else None,
        "unresolved_fraction_range": [counts["bad"] / n, (counts["bad"] + counts["indeterminate"]) / n] if n else None,
        "inference": inference,
        "threshold": threshold,
        "statistical_criterion_met": not blockers and inference["upper"] < threshold,
        "blockers": blockers,
        "release_gate": "not evaluated; provenance, isolation, complete run matrix and baseline comparisons require separate validation",
    }
