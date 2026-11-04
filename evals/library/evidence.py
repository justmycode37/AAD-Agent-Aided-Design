"""Strict validation for deterministic controls; no assertions or CAD imports."""
from __future__ import annotations
import hashlib
import json
import math
from pathlib import Path
import statistics
import uuid

KINDS = ("part", "material", "playbook", "api")
MEASUREMENTS = ("valid", "bbox_mm", "thread_zone_xy_mm", "solids", "material_probes", "passed")


def require(condition, message):
    if not condition:
        raise ValueError(message)


def load_document(path):
    def pairs(values):
        result = {}
        for key, value in values:
            require(key not in result, f"Duplicate evidence key: {key}")
            result[key] = value
        return result
    def number(text):
        value = float(text)
        require(math.isfinite(value), "Non-finite evidence number")
        return value
    def invalid(text):
        raise ValueError(f"Non-finite evidence number: {text}")
    return json.loads(Path(path).read_text(), object_pairs_hook=pairs, parse_float=number, parse_constant=invalid)


def vector(value, length):
    return isinstance(value, list) and len(value) == length and all(
        type(x) in (float, int) and math.isfinite(x) for x in value)


def same_measurement(actual, retained):
    if isinstance(actual, dict):
        return isinstance(retained, dict) and actual.keys() == retained.keys() and all(
            same_measurement(v, retained[k]) for k, v in actual.items())
    if isinstance(actual, list):
        return isinstance(retained, list) and len(actual) == len(retained) and all(
            same_measurement(a, b) for a, b in zip(actual, retained))
    if type(actual) is bool or actual is None or isinstance(actual, str):
        return type(actual) is type(retained) and actual == retained
    if type(actual) is int:
        return type(retained) is int and actual == retained
    return type(retained) in (int, float) and math.isfinite(retained) and abs(actual-retained) <= 1e-8


def summaries(records, expected_ids):
    result = []
    for part_id in expected_ids:
        rows = [r for r in records if r["id"] == part_id]
        axes = list(zip(*(r["bbox_mm"] for r in rows)))
        result.append({"id": part_id, "n": len(rows), "valid_count": sum(r["valid"] for r in rows),
                       "bbox_mean_mm": [statistics.mean(axis) for axis in axes],
                       "bbox_sample_sd_mm": [statistics.stdev(axis) if len(axis) > 1 else None for axis in axes]})
    return result


def artifact_path(base, run_id, row):
    require(isinstance(run_id, str), "run_id must be a UUID string")
    try:
        parsed = uuid.UUID(run_id)
    except (ValueError, AttributeError) as exc:
        raise ValueError("run_id must be a canonical UUID") from exc
    require(str(parsed) == run_id and parsed.version == 4, "run_id must be a canonical UUID4")
    value = row.get("artifact")
    require(isinstance(value, str) and not Path(value).is_absolute(), "artifact path must be relative")
    require(".." not in Path(value).parts, "artifact path cannot traverse directories")
    expected = f".runs/{run_id}/{row['id']}-{row['repeat']}.brep"
    require(value == expected, "artifact must name its exact run/trial BREP")
    base = Path(base).resolve()
    runs = base / ".runs"
    require(not runs.is_symlink(), ".runs cannot be a symlink")
    root = (runs / run_id).resolve()
    require(root.is_relative_to(runs.resolve()), "run directory escapes .runs")
    path = (base / value).resolve()
    require(path.is_relative_to(root), "artifact escapes its fixed run directory")
    require(path.is_file() and path.stat().st_size > 0, "Reproduce local constructor artifacts first")
    return path


def validate_parts(data, *, base, expected, source_sha256, measure, repetitions=3):
    require(isinstance(data, dict) and data.get("complete") is True and data.get("passed") is True,
            "Constructor evidence must have boolean complete/passed=true")
    require(type(data.get("repetitions")) is int and data["repetitions"] == repetitions, "Wrong repetition count")
    require(data.get("source_sha256") == source_sha256, "Stale constructor evidence")
    rows = data.get("records")
    require(isinstance(rows, list) and len(rows) == len(expected)*repetitions, "Missing constructor trials")
    pairs = set()
    for row in rows:
        require(isinstance(row, dict) and isinstance(row.get("id"), str) and row["id"] in expected and type(row.get("repeat")) is int, "Invalid trial identity")
        pair = (row["id"], row["repeat"])
        require(1 <= pair[1] <= repetitions and pair not in pairs, "Duplicate or unexpected trial")
        pairs.add(pair)
        require(row.get("passed") is True and row.get("valid") is True, "Unproven constructor result")
        require(vector(row.get("bbox_mm"), 3) and all(x > 0 for x in row["bbox_mm"]), "Invalid extent measurement")
        require(type(row.get("solids")) is int and row["solids"] > 0, "Invalid solid count")
        path = artifact_path(base, data.get("run_id"), row)
        require(hashlib.sha256(path.read_bytes()).hexdigest() == row.get("artifact_sha256"), "Artifact hash mismatch")
        actual = measure(path, row["id"])
        require(actual.get("passed") is True, "Fresh BREP audit failed")
        require(same_measurement(actual, {key: row.get(key) for key in MEASUREMENTS}), "Recorded geometry disagrees with fresh BREP audit")
    require(pairs == {(part_id, n) for part_id in expected for n in range(1, repetitions+1)}, "Incomplete trial matrix")
    require(same_measurement(summaries(rows, expected), data.get("summaries")), "Summaries disagree with records")


def _search_response(response, kind):
    require(isinstance(response, dict) and response.get("ok") is True, "MCP search did not succeed")
    listing = response.get("library")
    require(isinstance(listing, dict) and type(listing.get("total")) is int and listing["total"] > 0, "Invalid search total")
    items = listing.get("items")
    require(isinstance(items, list) and 0 < len(items) <= 5 and len(items) <= listing["total"], "Invalid bounded search list")
    require(type(listing.get("truncated")) is bool and listing["truncated"] == (listing["total"] > len(items)), "Invalid search truncation flag")
    ids = []
    for item in items:
        require(isinstance(item, dict) and item.get("kind") == kind and isinstance(item.get("id"), str) and item["id"], "Invalid search entry")
        ids.append(item["id"])
    require(len(ids) == len(set(ids)), "Duplicate search IDs")
    return ids[0]


def validate_mcp_records(records):
    require(isinstance(records, list) and len(records) == 10 and all(isinstance(r, dict) for r in records), "Incomplete MCP call sequence")
    for n, kind in enumerate(KINDS):
        search, get = records[2*n:2*n+2]
        require(search.get("args") == {"action": "search", "kind": kind}, "Unexpected MCP search request")
        requested_id = _search_response(search.get("response"), kind)
        require(get.get("args") == {"action": "get", "kind": kind, "id": requested_id}, "Unexpected MCP get request")
        response = get.get("response")
        require(isinstance(response, dict) and response.get("ok") is True, "MCP get did not succeed")
        library = response.get("library")
        require(isinstance(library, dict) and library.get("found") is True, "MCP get did not find its ID")
        item = library.get("item")
        require(isinstance(item, dict) and item.get("id") == requested_id and item.get("kind") == kind, "MCP get returned the wrong ID/kind")
    missing, invalid = records[8:]
    require(missing.get("args") == {"action": "get", "kind": "material", "id": "missing"}, "Missing unknown-ID control")
    response = missing.get("response")
    require(isinstance(response, dict) and response.get("ok") is True, "Unknown-ID request failed")
    library = response.get("library")
    require(isinstance(library, dict) and library.get("found") is False and library.get("item") is None, "Unknown ID was not explicitly absent")
    require(invalid.get("args") == {"action": "get", "id": "../secret"}, "Missing invalid-path control")
    response = invalid.get("response")
    require(isinstance(response, dict) and response.get("ok") is False, "Invalid path was accepted")
    failure = response.get("failure")
    require(isinstance(failure, dict) and failure.get("code") == "BAD_INPUT" and failure.get("phase") == "input", "Invalid path did not return BAD_INPUT/input")


def validate_mcp(data, *, source_sha256, probe_sha256):
    require(isinstance(data, dict) and data.get("complete") is True and data.get("passed") is True, "MCP evidence needs boolean complete/passed=true")
    require(data.get("source_sha256") == source_sha256, "Stale MCP source/guidance evidence")
    require(data.get("probe_sha256") == probe_sha256, "Stale MCP probe")
    validate_mcp_records(data.get("records"))
