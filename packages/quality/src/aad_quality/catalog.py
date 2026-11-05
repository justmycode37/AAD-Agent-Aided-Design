"""Bounded, kernel-free catalog; construction belongs in model workers."""
from __future__ import annotations

from copy import deepcopy
from importlib.resources import files
import json
from pathlib import Path
import re
from typing import Any

import yaml

KINDS = ("part", "material", "playbook", "api")
_SEARCH_LIMIT = 5
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z")


class LibraryInputError(ValueError):
    code = "BAD_INPUT"
    phase = "input"


_API = [
    {
        "id": "api.build123d",
        "title": "build123d modeling API",
        "summary": "Builder and algebra modeling, topology and units for pinned build123d 0.13.0.",
        "tags": ["python", "builder", "algebra", "topology", "units"],
        "source_url": "https://build123d.readthedocs.io/",
        "text": "Use build123d 0.13.0 with cadquery-ocp-novtk 8.0.1.1.0. Model coordinates are numeric; project units do not rescale Python literals. Resolve current geometry after a rebuild. A valid B-rep establishes kernel validity, not the original request or manufacturability.",
    },
    {
        "id": "api.quality.spec",
        "title": "AAD requirement evidence",
        "summary": "Spec parsing, measured requirements, unsupported evidence and readiness.",
        "tags": ["spec", "yaml", "verification", "requirements", "mass", "holes"],
        "source_url": "https://github.com/justmycode37/AAD-Agent-Aided-Design/blob/main/packages/quality/SPEC.md",
        "text": "load_spec(raw_yaml) rejects duplicate keys and invalid measurements. spec_checks(ctx) returns one check per requirement. Unknown types and unsupported geometry skip. verification_ready(ctx.spec, checks, hard_checks, process=ctx.process) requires measured coverage but cannot attest mutable worker evidence. min_wall and pointwise symmetry proof remain unsupported. Mass is a fully dense nominal material estimate. See SPEC.md shipped with the source for the full evidence matrix.",
    },
    {
        "id": "api.quality.process",
        "title": "AAD process settings",
        "summary": "Finite heuristic manufacturing screens and declared assumptions.",
        "tags": ["process", "fdm", "sla", "cnc", "laser", "bend", "draft"],
        "source_url": "https://github.com/justmycode37/AAD-Agent-Aided-Design/blob/main/packages/quality/PROCESS.md",
        "text": "process_checks(ctx) uses fdm_pla, sla, cnc_3axis, sheet_metal, laser or injection profiles. Every result is a heuristic with assumptions; finite samples do not prove global minimum wall or complete tool access. Dimensional process_settings are in mm regardless of context units. Choose thresholds from the actual supplier/process; do not invent universal limits. Missing evidence stays unknown.",
    },
]


def _guidance_root():
    bundled = files("aad_quality").joinpath("guidance")
    if bundled.is_dir():
        return bundled
    # Editable checkouts read the same files that force-include puts in wheels.
    root = Path(__file__).resolve().parents[4]
    if (root / "packages/quality/pyproject.toml").is_file() and (root / "skills").is_dir():
        return root / "skills"
    raise FileNotFoundError("The installed quality package is missing its guidance resources")


def _playbooks() -> list[dict[str, Any]]:
    folder = _guidance_root().joinpath("playbooks")
    entries = []
    seen = set()
    for path in sorted(folder.iterdir(), key=lambda p: p.name):
        if not path.name.endswith(".md"):
            continue
        text = path.read_text(encoding="utf-8")
        if not text.startswith("---\n") or "\n---\n" not in text[4:]:
            raise ValueError(f"Playbook {path.name!r} lacks YAML frontmatter")
        front, body = text[4:].split("\n---\n", 1)
        metadata = yaml.safe_load(front)
        if not isinstance(metadata, dict):
            raise ValueError(f"Playbook {path.name!r} has invalid metadata")
        entry_id, title = metadata.get("id"), metadata.get("title")
        tags = metadata.get("tags")
        if not isinstance(entry_id, str) or not _ID.fullmatch(entry_id) or entry_id in seen:
            raise ValueError(f"Playbook {path.name!r} has an invalid or duplicate ID")
        if not isinstance(title, str) or not title.strip() or not isinstance(tags, list) or any(not isinstance(t, str) for t in tags):
            raise ValueError(f"Playbook {path.name!r} lacks a title or string tags")
        if metadata.get("candidate_status") != "unmeasured":
            raise ValueError(f"Playbook {entry_id!r} needs measured evidence before changing candidate status")
        seen.add(entry_id)
        entries.append({"id": entry_id, "title": title, "tags": tags,
                        "summary": "Unmeasured candidate guidance; request the entry to read its assumptions.",
                        "candidate_status": "unmeasured", "text": body.strip()})
    return entries


def _entries(kind: str) -> list[dict[str, Any]]:
    if kind == "material":
        from .materials import list_materials
        return [{**item, "title": item["name"], "summary": item["definition"],
                 "tags": ["density", "nominal", item["id"]]} for item in list_materials()]
    if kind == "part":
        from .standard_parts import list_parts
        return list_parts()
    if kind == "playbook":
        return _playbooks()
    return deepcopy(_API)


def library(action: str, *, kind: str | None = None, query: str | None = None,
            id: str | None = None) -> dict[str, Any]:
    """Search small summaries or retrieve one complete catalog entry.

    IDs are looked up in a closed catalog, never interpreted as paths/code.
    Lookup does not instantiate a part or import a geometry kernel.
    """
    if not isinstance(action, str) or action not in {"search", "get"}:
        raise LibraryInputError("action must be search or get")
    if kind is not None and (not isinstance(kind, str) or kind not in KINDS):
        raise LibraryInputError(f"kind must be one of {', '.join(KINDS)}")
    if query is not None and (not isinstance(query, str) or len(query) > 512):
        raise LibraryInputError("query must be a string of at most 512 characters")
    if id is not None and (not isinstance(id, str) or not _ID.fullmatch(id)):
        raise LibraryInputError("id must be a catalog ID, not a file path")
    if action == "get" and (id is None or query is not None):
        raise LibraryInputError("get requires id and does not accept query")
    if action == "search" and id is not None:
        raise LibraryInputError("search accepts query, not id")
    kinds = (kind,) if kind else KINDS
    entries = [{**item, "kind": entry_kind} for entry_kind in kinds for item in _entries(entry_kind)]
    if action == "get":
        matches = [entry for entry in entries if entry["id"] == id]
        if len(matches) > 1:
            raise LibraryInputError("id is ambiguous; specify kind")
        return {"found": bool(matches), "id": id, "kind": kind,
                "item": deepcopy(matches[0]) if matches else None}
    terms = re.findall(r"[\w]+", (query or "").casefold())
    matches = []
    for entry in entries:
        haystack = json.dumps(entry, ensure_ascii=False).casefold()
        if all(term in haystack for term in terms):
            identity = f"{entry['id']} {entry.get('title', entry.get('name', ''))}".casefold()
            score = sum(term in identity for term in terms)
            matches.append((score, entry))
    matches.sort(key=lambda pair: (-pair[0], pair[1]["kind"], pair[1]["id"]))
    summaries = []
    for _, entry in matches[:_SEARCH_LIMIT]:
        summaries.append({"id": entry["id"], "kind": entry["kind"],
                          "title": entry.get("title", entry.get("name", entry["id"])),
                          "summary": entry.get("summary", "")[:180]})
    return {"query": query or "", "items": summaries, "total": len(matches),
            "truncated": len(matches) > _SEARCH_LIMIT}


def tool_description_candidates() -> dict[str, Any]:
    """Unmeasured candidates for S2/harness ablations, not tuned defaults."""
    return json.loads(_guidance_root().joinpath("tool-descriptions.json").read_text(encoding="utf-8"))


def server_instruction_candidate() -> str:
    return _guidance_root().joinpath("server-instructions.txt").read_text(encoding="utf-8").strip()
