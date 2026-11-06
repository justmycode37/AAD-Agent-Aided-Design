"""Synthetic statistical controls, never sampled agent outcomes."""

import math
from dataclasses import replace

import pytest

from evals.benchmark.statistics import (
    AuditObservation,
    MetricObservation,
    exact_binomial_upper,
    false_verified_summary,
    paired_task_bootstrap as _paired_task_bootstrap,
    task_summary as _task_summary,
)


def task_summary(records):
    return _task_summary(records, expected=sorted({(point.task_id, point.repetition) for point in records}))


def paired_task_bootstrap(left, right, **options):
    expected = sorted({(point.task_id, point.repetition) for point in [*left, *right]})
    return _paired_task_bootstrap(left, right, expected=expected, **options)


def points(task, values):
    return [MetricObservation(task, i, value, "synthetic-arm") for i, value in enumerate(values, 1)]


def audit(task, *, verified=True, verdict="good", coverage="complete", origin="real_agent", repetition=1):
    return AuditObservation(task, repetition, verified, verdict, coverage, origin, "synthetic-arm")


def summarize(observations, selected=None, *, host_manifest_validated=True):
    return false_verified_summary(
        observations,
        arm_id="synthetic-arm",
        selected=selected if selected is not None else [(p.task_id, p.repetition) for p in observations],
        sampling_plan_sha256="a" * 64,
        sampling_assumptions="Synthetic fixture only; no real-world inference.",
        host_manifest_validated=host_manifest_validated,
    )


def test_task_balancing_does_not_give_repeated_tasks_extra_weight():
    report = task_summary(points("one", [0] * 12) + points("two", [1, 1]))
    assert report["task_balanced_mean"] == 0.5
    assert report["n_trials"] == 14
    assert report["n_tasks"] == 2
    assert report["between_task_sample_sd"] == pytest.approx(math.sqrt(0.5))
    assert all(task["sample_sd"] == 0 for task in report["tasks"])


def test_unknown_and_single_sample_variance_are_not_zero():
    report = task_summary(points("one", [0, None]) + points("two", [1]))
    assert not report["complete"]
    assert report["n_unknown"] == 1
    assert report["task_balanced_mean"] is None
    assert report["tasks"][0]["mean"] is None
    assert report["tasks"][0]["known_only_mean"] == 0
    assert report["tasks"][1]["sample_sd"] is None
    assert not task_summary([])["complete"]


@pytest.mark.parametrize("value", [True, "1", math.inf, math.nan])
def test_bad_metric_values_are_rejected(value):
    with pytest.raises(ValueError):
        MetricObservation("task", 1, value, "synthetic-arm")


def test_duplicates_and_bool_repetitions_are_rejected():
    with pytest.raises(ValueError, match="duplicate"):
        task_summary(points("one", [1]) * 2)
    with pytest.raises(ValueError):
        MetricObservation("one", True, 1, "synthetic-arm")


def test_paired_bootstrap_resamples_task_clusters_and_is_reproducible():
    left = points("one", [1, 1, 1]) + points("two", [2, 2, 2])
    right = points("one", [0, 0, 0]) + points("two", [1, 1, 1])
    report = paired_task_bootstrap(left, right, seed=17, resamples=1000)
    assert report == paired_task_bootstrap(left, right, seed=17, resamples=1000)
    assert report["estimate"] == 1
    assert report["interval"] == [1, 1]
    assert report["bootstrap_sample_sd"] == 0
    assert report["n_tasks"] == 2
    assert report["degenerate"] is True


def test_bootstrap_ratio_is_ratio_of_task_balanced_means():
    left = points("one", [2]) + points("two", [10])
    right = points("one", [1]) + points("two", [10])
    report = paired_task_bootstrap(left, right, statistic="ratio", resamples=1000)
    assert report["estimate"] == pytest.approx(12 / 11)
    assert report["interval"][0] <= report["estimate"] <= report["interval"][1]


@pytest.mark.parametrize("left,right", [
    (points("one", [1]), points("one", [1])),
    (points("one", [1]) + points("two", [None]), points("one", [1]) + points("two", [1])),
    (points("one", [1, 2]), points("one", [1])),
    ([], []),
])
def test_incomplete_comparisons_do_not_get_intervals(left, right):
    report = paired_task_bootstrap(left, right, resamples=1000)
    assert not report["complete"]
    assert report["interval"] is None


def test_nonpositive_ratio_denominator_is_unproven():
    report = paired_task_bootstrap(
        points("one", [1]) + points("two", [1]),
        points("one", [0]) + points("two", [1]),
        statistic="ratio", resamples=1000,
    )
    assert not report["complete"]


def test_exact_zero_failure_bound_matches_independent_closed_form():
    for n in (1, 10, 58, 59, 100):
        bound = exact_binomial_upper(0, n)
        assert bound["upper"] == pytest.approx(-math.expm1(math.log(0.05) / n), abs=1e-10)
    assert exact_binomial_upper(0, 58)["upper"] > 0.05
    assert exact_binomial_upper(0, 59)["upper"] < 0.05
    assert exact_binomial_upper(0, 0)["upper"] is None
    assert exact_binomial_upper(4, 4)["upper"] == 1


@pytest.mark.parametrize("bad,n", [(True, 4), (0, False), (-1, 4), (5, 4), (0, -1)])
def test_invalid_binomial_counts_are_rejected(bad, n):
    with pytest.raises(ValueError):
        exact_binomial_upper(bad, n)


def test_empty_unknown_and_scripted_audits_cannot_support_threshold():
    for observations in (
        [],
        [audit("one", verified=False)],
        [audit("one", verified=None)],
        [audit("one", verdict="indeterminate", coverage="bounded")],
        [audit("one", origin="scripted_control")],
    ):
        report = summarize(observations)
        assert report["statistical_criterion_met"] is False
        assert report["blockers"]
        assert report["inference"]["upper"] is None


def test_unauthenticated_host_manifest_blocks_false_verified_inference():
    report = summarize([audit("one")], host_manifest_validated=False)
    assert report["host_manifest_validated"] is False
    assert "authenticated host manifest validation is required" in report["blockers"]
    assert report["statistical_criterion_met"] is False
    assert report["inference"]["upper"] is None


def test_missing_selected_audit_cannot_be_dropped():
    report = summarize([audit("one")], selected=[("one", 1), ("two", 1)])
    assert report["missing"] == [["two", 1]]
    assert not report["statistical_criterion_met"]


def test_repeated_task_cannot_inflate_independent_audit_size():
    observations = [audit("one", repetition=i) for i in range(1, 101)]
    with pytest.raises(ValueError, match="not independent"):
        summarize(observations)
    report = summarize(observations, selected=[("one", 1)])
    assert report["n_verified"] == 1
    assert not report["statistical_criterion_met"]


def test_bad_witness_counts_even_with_bounded_coverage():
    report = summarize([audit("bad", verdict="bad", coverage="bounded"), audit("good"), audit("unknown", verdict="indeterminate")])
    assert [report[key] for key in ("bad", "good", "indeterminate")] == [1, 1, 1]
    assert report["unresolved_fraction_range"] == pytest.approx([1 / 3, 2 / 3])
    assert report["inference"]["upper"] is None


def test_synthetic_math_control_can_meet_statistical_criterion_but_never_claims_release():
    # Origin is a fixture input, not authenticated by this statistical function.
    report = summarize([audit(f"task-{i}") for i in range(59)])
    assert report["statistical_criterion_met"] is True
    assert report["release_gate"].startswith("not evaluated")
    assert "provenance" in report["release_gate"]


def test_bounded_good_truthy_flags_and_duplicate_audits_are_rejected():
    with pytest.raises(ValueError):
        audit("one", coverage="bounded")
    with pytest.raises(ValueError):
        audit("one", verified="false")
    with pytest.raises(ValueError, match="duplicate"):
        summarize([audit("one"), audit("one")])


def test_expected_matrix_detects_a_task_omitted_from_both_arms():
    expected = [("one", 1), ("two", 1), ("omitted", 1)]
    records = points("one", [1]) + points("two", [1])
    report = _task_summary(records, expected=expected)
    assert report["complete"] is False
    assert report["missing"] == [("omitted", 1)]
    comparison = _paired_task_bootstrap(records, records, expected=expected, resamples=1000)
    assert comparison["complete"] is False
    assert comparison["candidate_missing"] == comparison["baseline_missing"] == [("omitted", 1)]
    assert comparison["interval"] is None


def test_mixed_arms_are_rejected_for_metrics_and_audits():
    records = points("one", [1]) + [replace(points("two", [1])[0], arm_id="other-arm")]
    with pytest.raises(ValueError, match="mixed"):
        task_summary(records)
    with pytest.raises(ValueError, match="mixed"):
        summarize([audit("one"), replace(audit("two"), arm_id="other-arm")])


def test_no_implicit_expected_matrix_or_unexpected_rows():
    with pytest.raises(TypeError):
        _task_summary(points("one", [1]))
    report = _task_summary(points("one", [1]) + points("extra", [1]), expected=[("one", 1)])
    assert report["complete"] is False and report["unexpected"] == [("extra", 1)]
