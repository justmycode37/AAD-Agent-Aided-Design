"""Canonical, kernel-free identity for the inputs to a build.

This module does not stage files, store revisions, or verify build results.
See ``packages/engine/identity.md`` for the caller's integration obligations.
"""

from __future__ import annotations

import hashlib
import json
import math
from typing import Any


_SCHEMA = "aiad.cache-identity.v1"
_HEX = frozenset("0123456789abcdef")


def cache_identity(
    *,
    source: str,
    applied_params: dict[str, Any],
    spec: dict[str, Any],
    kind: str,
    units: str,
    material: str | None,
    process: str | None,
    kernel_versions: dict[str, str],
    tolerances: dict[str, Any],
    asset_sha256: dict[str, str],
    pipeline_identity: str,
) -> tuple[str, dict[str, Any]]:
    """Return the lowercase SHA256 key and a detached canonical input record.

    All arguments are required, including explicit empty maps or ``None``
    where appropriate. JSON objects and arrays must be plain dicts and lists;
    no coercion of arbitrary Python objects is performed.
    """
    _text(source, "source", allow_blank=True)
    for name, value in (
        ("kind", kind),
        ("units", units),
        ("pipeline_identity", pipeline_identity),
    ):
        _text(value, name)
    for name, value in (("material", material), ("process", process)):
        if value is not None:
            _text(value, name)

    for name, value in (
        ("applied_params", applied_params),
        ("spec", spec),
        ("kernel_versions", kernel_versions),
        ("tolerances", tolerances),
        ("asset_sha256", asset_sha256),
    ):
        if type(value) is not dict:
            raise TypeError(f"{name} must be a plain JSON object (dict)")

    if not kernel_versions:
        raise ValueError("kernel_versions must identify at least one kernel component")
    for name, version in kernel_versions.items():
        _text(name, "kernel_versions key")
        _text(version, f"kernel_versions[{name!r}]")
    for name, digest in asset_sha256.items():
        _text(name, "asset_sha256 key")
        _text(digest, f"asset_sha256[{name!r}]")
        if len(digest) != 64 or any(char not in _HEX for char in digest):
            raise ValueError(f"asset_sha256[{name!r}] must be a lowercase SHA256 hex digest")

    try:
        record = _json_tree(
            {
                "schema": _SCHEMA,
                "source": source,
                "applied_params": applied_params,
                "spec": spec,
                "kind": kind,
                "units": units,
                "material": material,
                "process": process,
                "kernel_versions": kernel_versions,
                "tolerances": tolerances,
                "asset_sha256": asset_sha256,
                "pipeline_identity": pipeline_identity,
            },
            "$",
            set(),
        )
        encoded = json.dumps(
            record, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
    except RecursionError as exc:
        raise ValueError("input is too deeply nested to canonicalize") from exc
    return hashlib.sha256(encoded).hexdigest(), record


def _text(value: object, name: str, *, allow_blank: bool = False) -> None:
    if type(value) is not str:
        raise TypeError(f"{name} must be a string")
    if not allow_blank and not value.strip():
        raise ValueError(f"{name} must not be blank")
    try:
        value.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise ValueError(f"{name} must contain valid Unicode scalar values") from exc


def _json_tree(value: Any, path: str, seen: set[int]) -> Any:
    """Validate and copy a JSON tree, sorting every object by its keys."""
    if value is None or type(value) in (bool, int):
        return value
    if type(value) is float:
        if not math.isfinite(value):
            raise ValueError(f"{path} must be finite")
        return value
    if type(value) is str:
        _text(value, path, allow_blank=True)
        return value
    if type(value) not in (dict, list):
        raise TypeError(f"{path} contains an unsupported JSON value: {type(value).__name__}")

    # A JSON tree cannot express Python container aliasing, which a model could
    # otherwise observe. Reject shared containers as well as actual cycles.
    if id(value) in seen:
        raise ValueError(f"{path} contains a shared or cyclic container")
    seen.add(id(value))
    if type(value) is list:
        return [_json_tree(item, f"{path}[{index}]", seen) for index, item in enumerate(value)]
    for key in value:
        _text(key, f"{path} object key", allow_blank=True)
    return {key: _json_tree(value[key], f"{path}[{key!r}]", seen) for key in sorted(value)}
