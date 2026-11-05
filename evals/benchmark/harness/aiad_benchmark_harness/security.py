from __future__ import annotations

import base64
import hashlib
import json
import math
import re
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any, Mapping

_AUTH_KEY = re.compile(
    r"(?i)(?:^|[\s_-])(?:api[\s_-]?key|authorization|access[\s_-]?token|refresh[\s_-]?token|"
    r"client[\s_-]?secret|secret[\s_-]?access[\s_-]?key|secret[\s_-]?key|auth[\s_-]?token|"
    r"private[\s_-]?key|secret|oauth[\s_-]?token|session[\s_-]?token|password|"
    r"credential|credentials|bearer)(?:$|[\s_-])"
)
_SENSITIVE_PAIR = re.compile(
    r"(?i)\b(authorization|api[\s_-]?key|access[\s_-]?token|refresh[\s_-]?token|"
    r"client[\s_-]?secret|secret[\s_-]?access[\s_-]?key|secret[\s_-]?key|auth[\s_-]?token|"
    r"private[\s_-]?key|secret|oauth[\s_-]?token|session[\s_-]?token|password|"
    r"credential|credentials|bearer)\b(\s*[:=]\s*)"
    r"(?:bearer\s+)?([^\s,;\]}\"']+)"
)
_SECRET_PATTERNS = (
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/-]+=*"),
    re.compile(r"\bsk-ant-[A-Za-z0-9_-]{16,}\b"),
    re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_-]{20,}\b"),
    re.compile(r"\bmonid_(?:live|test|dev)_[A-Za-z0-9_-]{12,}\b"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"),
    re.compile(r"\bAIza[A-Za-z0-9_-]{30,}\b"),
)
_MESH_KEYS = {
    "mesh",
    "mesh_bytes",
    "mesh_data",
    "mesh_b64",
    "mesh_json",
    "mesh_buffer",
    "serialized_mesh",
    "ui_mesh",
    "scene_bytes",
    "scene_data",
    "scene_mesh",
    "geometry",
    "data_b64",
    "payload_b64",
    "vertices",
    "indices",
    "triangles",
    "points",
    "point_cloud",
    "pointcloud",
    "polygons",
    "edges",
    "edge_indices",
    "coordinates",
    "coordinate_array",
    "connectivity",
    "control_points",
    "positions",
    "position",
    "normals",
    "normal",
    "uvs",
    "faces",
    "face_indices",
    "vertex_buffer",
    "index_buffer",
    "position_buffer",
    "normal_buffer",
    "vertex_array",
    "index_array",
    "buffer_data",
    "buffers",
    "accessors",
    "primitives",
    "attributes",
}
_BASE64_RE = re.compile(r"^[A-Za-z0-9+/=_\r\n-]+$")
_INLINE_BINARY_FIELD = re.compile(
    r'(?is)("(?:mesh(?:_data|_b64|_json|_buffer)?|serialized_mesh|ui_mesh|scene_data|scene_mesh|geometry|data_b64|payload_b64|vertices|indices|triangles|points|point_cloud|pointcloud|polygons|edges|edge_indices|coordinates|coordinate_array|connectivity|control_points|positions|position|normals|normal|uvs|faces|face_indices|vertex_buffer|index_buffer|buffer_data|buffers|accessors|primitives|attributes)"\s*:\s*")([A-Za-z0-9+/=_-]+)(")'
)
_DATA_URI = re.compile(r"(?is)data:[^;]+;base64,([A-Za-z0-9+/=_-]+)")


def to_plain(value: Any) -> Any:
    """Convert provider/MCP SDK values to JSON-safe primitives."""
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else {"__nonfinite_float__": repr(value)}
    if isinstance(value, bytes):
        return {
            "__binary_sha256__": hashlib.sha256(value).hexdigest(),
            "__byte_count__": len(value),
        }
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, Mapping):
        return {str(key): to_plain(nested) for key, nested in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [to_plain(nested) for nested in value]
    dump = getattr(value, "model_dump", None)
    if callable(dump):
        try:
            return to_plain(dump(mode="json", by_alias=True, exclude_none=False))
        except TypeError:
            return to_plain(dump())
    if is_dataclass(value):
        return to_plain(asdict(value))
    if hasattr(value, "__dict__"):
        return to_plain(vars(value))
    return str(value)


def canonical_json(value: Any) -> str:
    return json.dumps(
        to_plain(value),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def sha256_json(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _redact_string(text: str) -> str:
    safe = _SENSITIVE_PAIR.sub(lambda m: f"{m.group(1)}{m.group(2)}<redacted>", text)
    for pattern in _SECRET_PATTERNS:
        safe = pattern.sub("<redacted>", safe)
    return safe


def _binary_summary(value: bytes | str) -> dict[str, Any]:
    if isinstance(value, bytes):
        raw = value
    else:
        try:
            raw = base64.b64decode(value, validate=False)
        except Exception:
            raw = value.encode("utf-8", errors="replace")
    return {
        "omitted": "binary geometry payload",
        "byte_count": len(raw),
        "sha256": hashlib.sha256(raw).hexdigest(),
    }


def _strip_geometry_payloads(value: Any, key: str | None = None) -> Any:
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        image = value.get("type") == "image" and value.get("mimeType", value.get("mime_type")) in {
            "image/png", "image/jpeg", "image/gif", "image/webp",
        }
        for raw_key, nested in value.items():
            key_text = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", str(raw_key))
            normalized = key_text.casefold().replace("-", "_")
            # Preserve requested image evidence in the host trace, and small
            # vectors needed to use CAD measurements. Neither is a UI mesh.
            if image and normalized == "data" and isinstance(nested, str):
                result[str(raw_key)] = nested
                continue
            if normalized in {"position", "normal", "coordinates"} and isinstance(nested, (list, tuple)) and len(nested) in {2, 3} and all(
                type(coordinate) in {int, float} and math.isfinite(coordinate)
                for coordinate in nested
            ):
                result[str(raw_key)] = list(nested)
                continue
            if normalized in _MESH_KEYS:
                if normalized == "triangles" and type(nested) is int:
                    result[str(raw_key)] = nested
                    continue
                if isinstance(nested, (bytes, str)):
                    result[str(raw_key)] = _binary_summary(nested)
                    continue
                if isinstance(nested, (list, tuple)):
                    encoded = canonical_json(nested).encode("utf-8")
                    result[str(raw_key)] = {
                        "omitted": "large geometry array",
                        "item_count": len(nested),
                        "sha256": hashlib.sha256(encoded).hexdigest(),
                    }
                    continue
            result[str(raw_key)] = _strip_geometry_payloads(nested, normalized)
        return result
    if isinstance(value, (list, tuple)):
        return [_strip_geometry_payloads(nested, key) for nested in value]
    if isinstance(value, bytes):
        return _binary_summary(value)
    if isinstance(value, str) and len(value) > 4096 and _BASE64_RE.fullmatch(value):
        return _binary_summary(value)
    return value


def redact_secrets(value: Any, key: str | None = None) -> Any:
    if isinstance(value, Mapping):
        redacted: dict[str, Any] = {}
        for raw_key, nested in value.items():
            name = str(raw_key)
            normalized = name.casefold().replace("-", "_")
            if _AUTH_KEY.search(normalized):
                redacted[name] = "<redacted>"
            else:
                redacted[name] = redact_secrets(nested, normalized)
        return redacted
    if isinstance(value, (list, tuple)):
        return [redact_secrets(nested, key) for nested in value]
    if isinstance(value, str):
        return _redact_string(value)
    return value


def safe_observation(value: Any) -> Any:
    """Keep useful raw observations while removing credentials and UI meshes."""
    return redact_secrets(_strip_geometry_payloads(to_plain(value)))


def safe_tool_text(value: str) -> str:
    """Redact auth strings and omit serialized binary geometry from MCP text."""
    text = value
    stripped = text.lstrip()
    if stripped[:1] in {"{", "["}:
        try:
            parsed = json.loads(text)
        except (json.JSONDecodeError, TypeError):
            parsed = None
        if isinstance(parsed, (dict, list)):
            safe = safe_observation(parsed)
            return json.dumps(safe, sort_keys=True, separators=(",", ":"), ensure_ascii=False)

    def replace_field(match: re.Match[str]) -> str:
        binary = _binary_summary(match.group(2))
        marker = json.dumps(binary, sort_keys=True, separators=(",", ":"))
        return match.group(1) + marker + match.group(3)

    text = _INLINE_BINARY_FIELD.sub(replace_field, text)
    text = _DATA_URI.sub(lambda match: "data:application/octet-stream;base64,<omitted-geometry>", text)
    return _redact_string(text)


def safe_exception_message(error: BaseException) -> str:
    return _redact_string(f"{type(error).__name__}: {error}")


def path_is_within(path: Path, root: Path) -> bool:
    resolved_path = path.resolve(strict=False)
    resolved_root = root.resolve(strict=False)
    try:
        resolved_path.relative_to(resolved_root)
        return True
    except ValueError:
        return False


def validate_external_output_path(path: Path, solver_roots: tuple[Path, ...]) -> Path:
    resolved = path.expanduser().resolve(strict=False)
    for root in solver_roots:
        if path_is_within(resolved, root):
            raise ValueError("host trace path must be outside every solver write root")
    return resolved


def validate_relative_artifact_path(value: str) -> tuple[bool, str]:
    """Reject absolute, URL, drive-qualified, and traversing path claims."""
    if not isinstance(value, str) or not value.strip():
        return False, "empty_artifact_claim"
    candidate = value.strip()
    if candidate != value or any(ord(char) < 32 for char in candidate):
        return False, "artifact_path_has_whitespace_or_control_characters"
    if candidate.startswith("aiad://"):
        return False, "opaque_aiad_reference_not_a_local_artifact_path"
    if _redact_string(candidate) != candidate:
        return False, "artifact_claim_contains_auth_like_value"
    if "://" in candidate or candidate.startswith(("/", "\\", "~")):
        return False, "external_or_absolute_artifact_path"
    # Reject Windows drive paths even when this runs on POSIX.
    if re.match(r"^[A-Za-z]:", candidate):
        return False, "drive_qualified_artifact_path"
    parts = candidate.replace("\\", "/").split("/")
    if any(part in {"..", ".", ""} for part in parts):
        return False, "traversing_or_empty_artifact_path_component"
    return True, "accepted_relative_artifact_path"
