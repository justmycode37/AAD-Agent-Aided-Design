"""Validation for the frozen, public real-object corpus and split."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path, PurePosixPath
from typing import Any

ROOT = Path(__file__).resolve().parent
CORPUS_DIR = ROOT / "corpus"
TASKS_PATH = CORPUS_DIR / "tasks.json"
SPLIT_PATH = CORPUS_DIR / "split.json"
_ID = re.compile(r"^[a-z0-9]+(?:_[a-z0-9]+)*$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_DOMAINS = {"subsystem", "interface"}


class CorpusError(ValueError):
    """The frozen corpus, source map, split, or references are invalid."""


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CorpusError(f"cannot read {path.name}: {exc}") from exc


def _safe_file(relative: object, *, label: str) -> Path:
    if not isinstance(relative, str) or not relative:
        raise CorpusError(f"{label} must be a relative POSIX file path")
    path = PurePosixPath(relative)
    if path.is_absolute() or "\\" in relative or any(part in {"", ".", ".."} for part in path.parts):
        raise CorpusError(f"{label} must be a safe relative POSIX file path")
    candidate = (CORPUS_DIR / Path(*path.parts)).resolve()
    try:
        candidate.relative_to(CORPUS_DIR.resolve())
    except ValueError as exc:
        raise CorpusError(f"{label} escapes the corpus") from exc
    if not candidate.is_file():
        raise CorpusError(f"{label} is not a file")
    return candidate


def validate_corpus(data: dict[str, Any], split: dict[str, Any], task_bytes: bytes) -> None:
    if data.get("schema_version") != 1 or data.get("corpus_id") != "aad-realism-objects-v1":
        raise CorpusError("unsupported corpus schema or id")
    source_ids: set[str] = set()
    for source in data.get("sources", []):
        if not isinstance(source, dict) or not _ID.fullmatch(str(source.get("id", ""))):
            raise CorpusError("each source needs a lowercase identifier")
        if source["id"] in source_ids:
            raise CorpusError(f"duplicate source id: {source['id']}")
        source_ids.add(source["id"])
        if not all(isinstance(source.get(field), str) and source[field].strip() for field in ("title", "url", "citation", "retrieved")):
            raise CorpusError(f"source {source['id']} must have title, URL, citation, and retrieval date")
        if not source["url"].startswith("https://"):
            raise CorpusError(f"source {source['id']} must use HTTPS")

    tasks = data.get("tasks")
    if not isinstance(tasks, list) or len(tasks) < 3:
        raise CorpusError("the corpus must contain at least three real-object tasks")
    task_ids: set[str] = set()
    cluster_ids: set[str] = set()
    required_families = {"gearbox", "small_drone", "electronics_enclosure"}
    families: set[str] = set()
    for task in tasks:
        if not isinstance(task, dict):
            raise CorpusError("each task must be an object")
        task_id, cluster_id = task.get("id"), task.get("cluster_id")
        if not isinstance(task_id, str) or not _ID.fullmatch(task_id) or task_id in task_ids:
            raise CorpusError(f"invalid or duplicate task id: {task_id!r}")
        if not isinstance(cluster_id, str) or not _ID.fullmatch(cluster_id) or cluster_id in cluster_ids:
            raise CorpusError(f"invalid or duplicate object cluster: {cluster_id!r}")
        task_ids.add(task_id)
        cluster_ids.add(cluster_id)
        families.add(task.get("family"))
        if task.get("kind") != "assembly" or not isinstance(task.get("request"), str) or not task["request"].strip():
            raise CorpusError(f"{task_id} needs a complete assembly request")

        checklist = task.get("checklist")
        if not isinstance(checklist, list) or len(checklist) < 5:
            raise CorpusError(f"{task_id} needs a subsystem and interface checklist")
        checklist_ids: set[str] = set()
        for item in checklist:
            if not isinstance(item, dict):
                raise CorpusError(f"{task_id} checklist items must be objects")
            item_id = item.get("id")
            if not isinstance(item_id, str) or not _ID.fullmatch(item_id) or item_id in checklist_ids:
                raise CorpusError(f"{task_id} has an invalid or duplicate checklist item")
            checklist_ids.add(item_id)
            if item.get("domain") not in _DOMAINS or type(item.get("weight")) is not int or item["weight"] <= 0:
                raise CorpusError(f"{task_id}/{item_id} has invalid domain or weight")
            if not isinstance(item.get("prompt"), str) or not item["prompt"].strip():
                raise CorpusError(f"{task_id}/{item_id} needs a review question")
            cited = item.get("source_ids")
            if not isinstance(cited, list) or not cited or any(source_id not in source_ids for source_id in cited):
                raise CorpusError(f"{task_id}/{item_id} must cite known public sources")
        domains = {item["domain"] for item in checklist}
        if domains != _DOMAINS:
            raise CorpusError(f"{task_id} must include both subsystem and interface checks")

        reference = task.get("reference_3d")
        if not isinstance(reference, dict):
            raise CorpusError(f"{task_id} needs an explicit 3D-reference slot")
        if reference.get("status") == "empty":
            if reference.get("path") is not None or not isinstance(reference.get("reason"), str) or not reference["reason"].strip():
                raise CorpusError(f"{task_id} empty reference slot needs a reason and null path")
        elif reference.get("status") == "included":
            model_path = _safe_file(reference.get("path"), label=f"{task_id} reference")
            if reference.get("format") not in {"stl", "step", "obj", "ply", "glb", "dae"}:
                raise CorpusError(f"{task_id} reference has unsupported format")
            if reference.get("license") != "MIT" or not _SHA256.fullmatch(str(reference.get("sha256", ""))):
                raise CorpusError(f"{task_id} reference needs a recorded permissive license and SHA-256")
            if hashlib.sha256(model_path.read_bytes()).hexdigest() != reference["sha256"]:
                raise CorpusError(f"{task_id} reference digest mismatch")
            license_path = _safe_file(reference.get("license_path"), label=f"{task_id} reference license")
            license_text = license_path.read_text(encoding="utf-8")
            if "MIT License" not in license_text or "Copyright (c) 2022 Bitcraze" not in license_text:
                raise CorpusError(f"{task_id} reference license notice is missing")
            if reference.get("license_source_id") not in source_ids:
                raise CorpusError(f"{task_id} reference needs a cited license source")
        else:
            raise CorpusError(f"{task_id} reference status must be included or empty")

    if not required_families.issubset(families):
        raise CorpusError(f"corpus must include these object families: {sorted(required_families)}")
    audit_ids = {entry.get("object_id") for entry in data.get("reference_audit", []) if isinstance(entry, dict)}
    if audit_ids != cluster_ids:
        raise CorpusError("reference audit must record searches for every object cluster")

    expected_corpus_hash = hashlib.sha256(task_bytes).hexdigest()
    if split.get("schema_version") != 1 or split.get("split_id") != data["corpus_id"]:
        raise CorpusError("unsupported split manifest")
    if split.get("seed") != data.get("split_seed") or split.get("rule") != data.get("split_rule"):
        raise CorpusError("corpus and split seed/rule do not match")
    if split.get("tasks_sha256") != expected_corpus_hash:
        raise CorpusError("split manifest does not freeze these task bytes")
    if split.get("frozen_before_scoring") is not True:
        raise CorpusError("split must be marked frozen before scoring")
    ranked = split.get("ranked")
    if not isinstance(ranked, list) or len(ranked) != len(tasks):
        raise CorpusError("split must rank every object cluster exactly once")
    expected_ranked = []
    for task in tasks:
        digest = hashlib.sha256((split["seed"] + "\0" + task["cluster_id"]).encode()).hexdigest()
        expected_ranked.append((digest, task["cluster_id"], task["id"]))
    expected_ranked.sort()
    count = split.get("tuning_clusters")
    if type(count) is not int or not 1 <= count < len(tasks):
        raise CorpusError("tuning_clusters must leave at least one held-out object")
    actual = [(entry.get("sha256"), entry.get("cluster_id"), entry.get("task_id")) for entry in ranked]
    expected = [(digest, cluster, task_id) for digest, cluster, task_id in expected_ranked]
    if actual != expected:
        raise CorpusError("split ranks do not follow the frozen SHA-256 rule")
    expected_splits = ["tuning" if index < count else "held_out" for index in range(len(expected))]
    if [entry.get("split") for entry in ranked] != expected_splits:
        raise CorpusError("split assignments do not follow the frozen fixed quota")


def load_corpus() -> tuple[dict[str, Any], dict[str, Any]]:
    tasks_bytes = TASKS_PATH.read_bytes()
    data = json.loads(tasks_bytes)
    split = _read_json(SPLIT_PATH)
    validate_corpus(data, split, tasks_bytes)
    return data, split


def tasks_for_split(data: dict[str, Any], split: dict[str, Any], name: str) -> list[dict[str, Any]]:
    if name not in {"tuning", "held_out", "all"}:
        raise CorpusError("split must be tuning, held_out, or all")
    allowed = None if name == "all" else {entry["task_id"] for entry in split["ranked"] if entry["split"] == name}
    return [task for task in data["tasks"] if allowed is None or task["id"] in allowed]
