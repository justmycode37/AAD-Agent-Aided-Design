"""Bounded, kernel-free catalog; construction belongs in model workers."""
from __future__ import annotations

from copy import deepcopy
from importlib.resources import files
import json
from pathlib import Path
import re
from typing import Any

import yaml

KINDS = ("part", "component", "material", "playbook", "api")
GUIDE_ID = "aad.guide.v1"
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
    # The main skill file is also the library's guide, for hosts that only have the MCP tools.
    guide = _guidance_root().joinpath("aad/SKILL.md").read_text(encoding="utf-8")
    entries.append({"id": GUIDE_ID, "title": "AAD guide: every tool, model.py, spec.yaml and current limits",
                    "tags": ["guide", "tools", "workflow", "overview", "capabilities", "limits"],
                    "summary": "Start here. How to use all of AAD, and what does not work yet.",
                    "candidate_status": "unmeasured", "text": guide.split("\n---\n", 1)[1].strip()})
    return entries


def _entries(kind: str) -> list[dict[str, Any]]:
    if kind == "material":
        from .materials import list_materials
        return [{**item, "title": item["name"], "summary": item["definition"],
                 "tags": ["density", "nominal", item["id"], *item.get("aliases", [])]}
                for item in list_materials()]
    if kind == "part":
        from .standard_parts import list_parts
        return list_parts()
    if kind == "component":
        from .components import list_components
        return list_components()
    if kind == "playbook":
        return _playbooks()
    return deepcopy(_API)


# "m3.5" is one size; a plain word split would also match it as "m3".
_WORD = r"[a-z]+\d+\.\d+|\w+"


def _search_terms(query: str | None) -> list[str]:
    # "M3x16" is a thread and a length; "8x22x7" stays whole to match a bearing tag.
    text = re.sub(r"\b(m\d+(?:\.\d+)?)x(?=\d)", r"\1 ", (query or "").casefold())
    return re.findall(_WORD, text)


def _rank(terms: list[str], entry: dict[str, Any]) -> int:
    identity = f"{entry['id']} {entry.get('title', entry.get('name', ''))}".casefold()
    words = set(re.findall(_WORD, identity)) | {tag.casefold() for tag in entry.get("tags", ()) if "x" in tag.casefold()}
    score = 0
    for term in terms:
        if term in words:
            score += 2
        elif term in identity:
            # A size inside a longer token is a different part: M3 is not M30.
            score += -5 if any(ch.isdigit() for ch in term) else 1
    return score


def library(action: str, *, kind: str | None = None, query: str | None = None,
            id: str | None = None, offset: int | None = None,
            items: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Search small summaries, retrieve one entry, or check a component list.

    IDs are looked up in a closed catalog, never interpreted as paths/code.
    Lookup does not instantiate a part or import a geometry kernel.
    """
    if not isinstance(action, str) or action not in {"search", "get", "check"}:
        raise LibraryInputError("action must be search, get or check")
    if action == "check":
        if kind not in (None, "component") or query is not None or id is not None or offset is not None:
            raise LibraryInputError("check accepts only items and kind=component")
        from .components import check_items
        try:
            return check_items(items)
        except ValueError as exc:
            raise LibraryInputError(str(exc)) from exc
    if items is not None:
        raise LibraryInputError("items is only accepted by check")
    if offset is not None and (isinstance(offset, bool) or not isinstance(offset, int) or offset < 0):
        raise LibraryInputError("offset must be a non-negative integer")
    if action == "get" and offset is not None:
        raise LibraryInputError("get does not accept offset")
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
    terms = _search_terms(query)
    matches = []
    hits: list[tuple[int, int, dict[str, Any]]] = []
    wants_screw = re.search(r"\bm\d+(?:\.\d+)?x\d", (query or "").casefold()) is not None
    for entry in entries:
        # Every component shares this boilerplate, so it must not make a query match.
        searchable = {k: v for k, v in entry.items() if k not in ("limitations", "evidence")}
        if entry["kind"] == "material":
            # Keep source URLs, density prose, and provenance out of alias search:
            # digits in URLs must not make (for example) 316 match a 304 grade.
            searchable = {k: entry[k] for k in ("id", "title", "summary", "tags") if k in entry}
        haystack = json.dumps(searchable, ensure_ascii=False).casefold()
        # The index mentions every family, so it should only answer queries about itself.
        if entry["id"] == "component.index" and terms and _rank(terms, entry) <= 0:
            continue
        found = sum(term in haystack for term in terms)
        if found == len(terms):
            matches.append((_rank(terms, entry) + (wants_screw and entry.get("category") == "fastener"), entry))
        elif found and entry["kind"] == "component":
            hits.append((found, _rank(terms, entry), entry))
    partial = False
    if not matches and hits and kind == "component":
        # Values like "10k" live in option ranges, so offer the closest families.
        partial = True
        matches = [(found * 1000 + rank, entry) for found, rank, entry in hits]
    # Components only outrank other kinds on score, so an unfiltered browse stays mixed.
    matches.sort(key=lambda pair: (-pair[0], pair[1]["kind"] == "component", pair[1]["kind"], pair[1]["id"]))
    start = offset or 0
    summaries = []
    for _, entry in matches[start:start + _SEARCH_LIMIT]:
        summary = {"id": entry["id"], "kind": entry["kind"],
                   "title": entry.get("title", entry.get("name", entry["id"])),
                   "summary": entry.get("summary", "")[:180]}
        if entry["kind"] == "component" and "evidence" in entry:
            summary["evidence"] = entry["evidence"]["status"]
        summaries.append(summary)
    result = {"query": query or "", "items": summaries, "total": len(matches),
              "truncated": len(matches) > start + _SEARCH_LIMIT}
    if result["truncated"]:
        result["next_offset"] = start + _SEARCH_LIMIT
    if partial:
        result["partial_match"] = True
    return result


def tool_description_candidates() -> dict[str, Any]:
    """Unmeasured candidates for S2/harness ablations, not tuned defaults."""
    return json.loads(_guidance_root().joinpath("tool-descriptions.json").read_text(encoding="utf-8"))


def server_instruction_candidate() -> str:
    return _guidance_root().joinpath("server-instructions.txt").read_text(encoding="utf-8").strip()
