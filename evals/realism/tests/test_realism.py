from __future__ import annotations

import hashlib
import json
from pathlib import Path
import struct
import zlib

import pytest

from evals.realism.corpus_loader import CorpusError, load_corpus, tasks_for_split, validate_corpus
from evals.realism.negative_controls import synthetic_control_score
from evals.realism.packets import PacketError, prepare_study, sanitize_png
from evals.realism.scoring import ReviewError, inter_rater_statistics, score_study, weighted_checklist_score


def _png_chunk(chunk_type: bytes, payload: bytes) -> bytes:
    return struct.pack(">I", len(payload)) + chunk_type + payload + struct.pack(">I", zlib.crc32(chunk_type + payload) & 0xFFFFFFFF)


def _png(*, metadata: bytes = b"") -> bytes:
    header = struct.pack(">IIBBBBB", 2, 2, 8, 2, 0, 0, 0)
    pixel_bytes = b"\x00\xff\x00\x00\x00\xff\x00\x00\x00\x00\xff\xff\xff\xff"
    pixels = zlib.compress(pixel_bytes)
    return (
        b"\x89PNG\r\n\x1a\n"
        + _png_chunk(b"IHDR", header)
        + (metadata if metadata else b"")
        + _png_chunk(b"IDAT", pixels)
        + _png_chunk(b"IEND", b"")
    )


def _prepared(tmp_path: Path, split_name: str = "tuning") -> Path:
    data, split = load_corpus()
    tasks = tasks_for_split(data, split, split_name)
    before, after = tmp_path / "before", tmp_path / "after"
    for root in (before, after):
        for task in tasks:
            task_dir = root / task["id"]
            task_dir.mkdir(parents=True)
            (task_dir / "section-xz.png").write_bytes(_png(metadata=_png_chunk(b"tEXt", b"Author\x00private author")))
            (task_dir / "section-yz.png").write_bytes(_png())
            (task_dir / "exterior-iso.png").write_bytes(_png())
    counter = iter(range(1, 100))
    output = tmp_path / f"prepared-{split_name}"
    prepare_study(before, after, output, reviewers=["rater_1", "rater_2"], split_name=split_name,
                  token_factory=lambda: f"{next(counter):016x}")
    return output


def test_frozen_corpus_has_tasks_sources_licenses_and_hash_split():
    data, split = load_corpus()
    assert {task["family"] for task in data["tasks"]} >= {"gearbox", "small_drone", "electronics_enclosure"}
    assert {entry["split"] for entry in split["ranked"]} == {"tuning", "held_out"}
    assert [entry["split"] for entry in split["ranked"]] == ["tuning", "tuning", "held_out"]
    tasks_bytes = (Path(__file__).parents[1] / "corpus" / "tasks.json").read_bytes()
    assert hashlib.sha256(tasks_bytes).hexdigest() == split["tasks_sha256"]
    included = [task for task in data["tasks"] if task["reference_3d"]["status"] == "included"]
    assert [task["id"] for task in included] == ["drone_crazyflie_21"]
    assert included[0]["reference_3d"]["license"] == "MIT"
    assert all(item["source_ids"] for task in data["tasks"] for item in task["checklist"])


def test_split_validator_rejects_a_modified_rank():
    data, split = load_corpus()
    changed = json.loads(json.dumps(split))
    changed["ranked"][0]["sha256"] = "0" * 64
    with pytest.raises(CorpusError, match="SHA-256"):
        validate_corpus(data, changed, (Path(__file__).parents[1] / "corpus" / "tasks.json").read_bytes())


def test_weighted_score_is_bounded_and_incomplete_is_unknown():
    data, _ = load_corpus()
    task = data["tasks"][0]
    perfect = weighted_checklist_score(task, {item["id"]: 4 for item in task["checklist"]})
    assert perfect["score_0_100"] == 100
    assert perfect["complete"]
    incomplete = weighted_checklist_score(task, {task["checklist"][0]["id"]: 4})
    assert incomplete["score_0_100"] is None
    assert incomplete["known_only_score_0_100"] is not None
    assert not incomplete["complete"]


@pytest.mark.parametrize("control", ["exterior_only_shell", "labeled_floating_blocks"])
def test_synthetic_control_cards_have_low_weighted_arithmetic(control: str):
    data, _ = load_corpus()
    scores = [synthetic_control_score(task, control)["score_0_100"] for task in data["tasks"]]
    assert all(score is not None and score < 10 for score in scores)


def test_floating_labels_are_not_structural_evidence():
    data, _ = load_corpus()
    task = next(item for item in data["tasks"] if item["family"] == "gearbox")
    score = synthetic_control_score(task, "labeled_floating_blocks")
    assert score["score_0_100"] == 0
    assert score["by_domain"]["interface"]["score_0_100"] == 0


def test_png_metadata_is_removed_without_changing_pixel_payload():
    original = _png(metadata=_png_chunk(b"tEXt", b"Author\x00CAD agent owner"))
    safe = sanitize_png(original)
    assert b"CAD agent owner" not in safe
    pixel_bytes = b"\x00\xff\x00\x00\x00\xff\x00\x00\x00\x00\xff\xff\xff\xff"
    assert _png_chunk(b"IDAT", zlib.compress(pixel_bytes)) in safe
    assert b"IHDR" in safe and b"IDAT" in safe and b"IEND" in safe


def test_packet_preparation_blinds_names_and_uses_plain_image_directories(tmp_path: Path):
    prepared = _prepared(tmp_path)
    data, split = load_corpus()
    tasks = tasks_for_split(data, split, "tuning")
    packet_root = prepared / "review_packets"
    assignment = json.loads((prepared / "restricted" / "assignment-map.json").read_text())
    for reviewer, entries in assignment["reviewers"].items():
        assert len(entries) == 2 * len(tasks)
        for entry in entries:
            packet = packet_root / reviewer / entry["packet_id"]
            assert {path.name for path in packet.iterdir()} == {
                "request.txt", "view-01.png", "view-02.png", "view-03.png"
            }
            assert b"private author" not in (packet / "view-01.png").read_bytes()
            assert not any(name in str(packet) for name in (entry["task_id"], entry["arm"]))
    form = json.loads((prepared / "review_forms" / "rater_1.json").read_text())
    assert all(not ({"arm", "author", "reasoning"} & set(review)) for review in form["reviews"])


def test_prepare_requires_two_sections_and_exterior(tmp_path: Path):
    data, split = load_corpus()
    task = tasks_for_split(data, split, "tuning")[0]
    before, after = tmp_path / "before", tmp_path / "after"
    for root in (before, after):
        task_dir = root / task["id"]
        task_dir.mkdir(parents=True)
        (task_dir / "section-only.png").write_bytes(_png())
    with pytest.raises(PacketError, match="two section"):
        prepare_study(before, after, tmp_path / "out", reviewers=["r1", "r2"])


def test_interrater_reports_exact_and_weighted_agreement():
    records = [
        {"reviewer_id": "r1", "task_id": "t", "arm": "before", "item_id": "i", "domain": "interface", "rating": 4},
        {"reviewer_id": "r2", "task_id": "t", "arm": "before", "item_id": "i", "domain": "interface", "rating": 3},
    ]
    result = inter_rater_statistics(records)["interface"]
    assert result["n_paired_ratings"] == 1
    assert result["exact_agreement"] == 0
    assert result["pairwise"][0]["linear_weighted_cohen_kappa"] is not None


def test_score_report_contains_interrater_and_confidence_intervals(tmp_path: Path):
    prepared = _prepared(tmp_path)
    ratings = tmp_path / "ratings"
    ratings.mkdir()
    assignments = json.loads((prepared / "restricted" / "assignment-map.json").read_text())["reviewers"]
    for reviewer, entries in assignments.items():
        form = json.loads((prepared / "review_forms" / f"{reviewer}.json").read_text())
        for review in form["reviews"]:
            for item in review["items"]:
                item["rating"] = 3 if reviewer == "rater_1" else 2
                item["confidence"] = 3
                item["evidence"] = ["view-01.png"]
                item["rationale"] = "Section and exterior evidence reviewed."
        (ratings / f"{reviewer}.json").write_text(json.dumps(form))
    report = score_study(prepared, ratings)
    assert report["arms"]["before"]["task_balanced_mean_0_100"] == 62.5
    assert report["paired_after_minus_before"]["complete"]
    assert report["paired_after_minus_before"]["interval"] is not None
    assert report["per_task"]
    assert report["per_task"][report["task_ids"][0]]["before"]["reviewer_ci_95"]["interval"] is not None
    assert "all" in report["inter_rater"]
    assert "linear_weighted_cohen_kappa" in report["inter_rater"]["all"]["pairwise"][0]
    assert report["claim_limit"].startswith("No report")


def test_unassessable_item_needs_reason_and_cannot_pass(tmp_path: Path):
    prepared = _prepared(tmp_path)
    ratings = tmp_path / "ratings"
    ratings.mkdir()
    assignments = json.loads((prepared / "restricted" / "assignment-map.json").read_text())["reviewers"]
    for reviewer, entries in assignments.items():
        form = json.loads((prepared / "review_forms" / f"{reviewer}.json").read_text())
        for review in form["reviews"]:
            for item in review["items"]:
                item["rating"] = 2
                item["confidence"] = 2
                item["evidence"] = ["view-01.png"]
                item["rationale"] = "Partial evidence."
        form["reviews"][0]["items"][0].update({"rating": None, "confidence": None, "evidence": [], "rationale": "Section image is corrupt."})
        (ratings / f"{reviewer}.json").write_text(json.dumps(form))
    report = score_study(prepared, ratings)
    assert report["denominators"]["unassessable_or_missing_ratings"] > 0
    assert any(
        not condition["complete"]
        for arms in report["per_task"].values()
        for condition in arms.values()
    )


def test_rating_without_image_evidence_is_rejected(tmp_path: Path):
    prepared = _prepared(tmp_path)
    ratings = tmp_path / "ratings"
    ratings.mkdir()
    assignments = json.loads((prepared / "restricted" / "assignment-map.json").read_text())["reviewers"]
    reviewer = next(iter(assignments))
    form = json.loads((prepared / "review_forms" / f"{reviewer}.json").read_text())
    form["reviews"][0]["items"][0].update({"rating": 4, "confidence": 4, "evidence": [], "rationale": ""})
    (ratings / f"{reviewer}.json").write_text(json.dumps(form))
    with pytest.raises(ReviewError, match="needs a view citation"):
        score_study(prepared, ratings)


def test_freeze_check_refuses_edited_tasks_with_a_matching_manifest():
    data, split = load_corpus()
    edited = (Path(__file__).parents[1] / "corpus" / "tasks.json").read_bytes() + b"\n"
    changed = json.loads(json.dumps(split))
    changed["tasks_sha256"] = hashlib.sha256(edited).hexdigest()
    with pytest.raises(CorpusError, match="pinned"):
        validate_corpus(data, changed, edited)


def test_png_sanitizer_removes_color_chunks_that_can_cue_an_arm():
    safe = sanitize_png(_png(metadata=_png_chunk(b"gAMA", struct.pack(">I", 45455))))
    assert b"gAMA" not in safe


def test_prepare_refuses_arms_with_different_views(tmp_path: Path):
    data, split = load_corpus()
    tasks = tasks_for_split(data, split, "tuning")
    before, after = tmp_path / "before", tmp_path / "after"
    for root in (before, after):
        for task in tasks:
            task_dir = root / task["id"]
            task_dir.mkdir(parents=True)
            for name in ("section-xz.png", "section-yz.png", "exterior-iso.png"):
                (task_dir / name).write_bytes(_png())
    (after / tasks[0]["id"] / "interior-1.png").write_bytes(_png())
    with pytest.raises(PacketError, match="same view filenames"):
        prepare_study(before, after, tmp_path / "out", reviewers=["rater_1", "rater_2"], split_name="tuning")


@pytest.mark.parametrize("change", ["replace", "extra", "missing"])
def test_scoring_refuses_packets_changed_after_preparation(tmp_path: Path, change: str):
    prepared = _prepared(tmp_path)
    view = next((prepared / "review_packets").rglob("view-01.png"))
    if change == "replace":
        view.write_bytes(view.read_bytes() + b"x")
    elif change == "extra":
        (view.parent / "note.txt").write_text("before")
    else:
        view.unlink()
    with pytest.raises(ReviewError, match="packet"):
        score_study(prepared, prepared / "review_forms")
