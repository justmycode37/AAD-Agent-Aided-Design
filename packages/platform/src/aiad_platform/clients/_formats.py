"""Loss-aware editors for the client config formats."""

from __future__ import annotations

import json
import re
import tomllib
from dataclasses import dataclass
from typing import Any

import yaml

from . import _ClientDefinition


class ConfigError(ValueError):
    """A config is invalid or cannot be edited without changing unrelated data."""


class _Absent:
    pass


ABSENT = _Absent()


def empty_text(definition: _ClientDefinition) -> str:
    if definition.format in {"json", "jsonc"}:
        return "{}"
    return ""


def parse_document(definition: _ClientDefinition, text: str) -> dict[str, Any]:
    try:
        if definition.format == "json":
            parsed = json.loads(text)
        elif definition.format == "jsonc":
            parsed = _parse_jsonc(text)
        elif definition.format == "toml":
            parsed = tomllib.loads(text)
        else:
            parsed = yaml.safe_load(text)
            if parsed is None:
                parsed = {}
    except (json.JSONDecodeError, tomllib.TOMLDecodeError, yaml.YAMLError, ConfigError) as exc:
        raise ConfigError(f"config could not be parsed: {exc}") from exc
    if not isinstance(parsed, dict):
        raise ConfigError("config top level must be an object or mapping")
    return parsed


def get_entry(definition: _ClientDefinition, document: dict[str, Any]) -> Any:
    container = document.get(definition.root_key, ABSENT)
    if container is ABSENT:
        return ABSENT
    if definition.format == "yaml" and container is None:
        return ABSENT
    if not isinstance(container, dict):
        raise ConfigError(f"top-level {definition.root_key!r} must be a mapping")
    return container.get("aiad", ABSENT)


def set_entry(definition: _ClientDefinition, text: str, entry: dict[str, Any]) -> str:
    if definition.format == "json":
        return _set_json(definition, text, entry)
    if definition.format == "jsonc":
        return _set_jsonc(definition, text, entry)
    if definition.format == "toml":
        return _set_toml(definition, text, entry)
    return _set_yaml(definition, text, entry)


def remove_entry(definition: _ClientDefinition, text: str) -> str:
    if definition.format == "json":
        document = parse_document(definition, text)
        container = document.get(definition.root_key)
        if not isinstance(container, dict) or "aiad" not in container:
            return text
        del container["aiad"]
        return _dump_json(document, text)
    if definition.format == "jsonc":
        return _remove_jsonc(definition, text)
    if definition.format == "toml":
        return _remove_toml(definition, text)
    return _remove_yaml(definition, text)


def _set_json(definition: _ClientDefinition, text: str, entry: dict[str, Any]) -> str:
    document = parse_document(definition, text)
    container = document.setdefault(definition.root_key, {})
    if not isinstance(container, dict):
        raise ConfigError(f"top-level {definition.root_key!r} must be an object")
    container["aiad"] = entry
    rendered = _dump_json(document, text)
    parsed = parse_document(definition, rendered)
    if get_entry(definition, parsed) != entry:
        raise ConfigError("edited JSON did not preserve the aiad entry")
    return rendered


def _json_indent(text: str) -> int | str | None:
    for line in text.splitlines():
        match = re.match(r"([ \t]+)(?:\"|\S)", line)
        if match:
            prefix = match.group(1)
            if "\t" in prefix:
                return "\t"
            return len(prefix)
    if "\n" in text or "\r" in text:
        return 2
    return None


def _dump_json(document: dict[str, Any], original: str) -> str:
    indent = _json_indent(original)
    if indent is None:
        separators = (",", ": ") if ": " in original else (",", ":")
        rendered = json.dumps(document, ensure_ascii=False, separators=separators)
    else:
        rendered = json.dumps(document, ensure_ascii=False, indent=indent)
    newline = "\r\n" if "\r\n" in original else "\n"
    if newline != "\n":
        rendered = rendered.replace("\n", newline)
    if original.endswith(("\n", "\r")) or not original:
        rendered += newline
    return rendered


# ---- JSONC (Zed) ---------------------------------------------------------


@dataclass(frozen=True)
class _JsonMember:
    key: str
    key_start: int
    value_start: int
    value_end: int
    comma_after: int | None


def _mask_jsonc_comments(text: str) -> str:
    chars = list(text)
    i = 0
    in_string = False
    while i < len(text):
        char = text[i]
        if in_string:
            if char == "\\":
                i += 2
                continue
            if char == '"':
                in_string = False
            i += 1
            continue
        if char == '"':
            in_string = True
            i += 1
            continue
        if text.startswith("//", i):
            end = text.find("\n", i + 2)
            if end == -1:
                end = len(text)
            for j in range(i, end):
                if chars[j] != "\r":
                    chars[j] = " "
            i = end
            continue
        if text.startswith("/*", i):
            end = text.find("*/", i + 2)
            if end == -1:
                raise ConfigError("unterminated JSONC block comment")
            end += 2
            for j in range(i, end):
                if chars[j] not in "\r\n":
                    chars[j] = " "
            i = end
            continue
        i += 1
    if in_string:
        raise ConfigError("unterminated JSON string")
    return "".join(chars)


def _mask_trailing_jsonc_commas(text: str) -> str:
    chars = list(text)
    i = 0
    in_string = False
    while i < len(text):
        char = text[i]
        if in_string:
            if char == "\\":
                i += 2
                continue
            if char == '"':
                in_string = False
            i += 1
            continue
        if char == '"':
            in_string = True
        elif char == ",":
            j = i + 1
            while j < len(text) and text[j].isspace():
                j += 1
            if j < len(text) and text[j] in "]}":
                chars[i] = " "
        i += 1
    return "".join(chars)


def _jsonc_clean(text: str) -> str:
    return _mask_trailing_jsonc_commas(_mask_jsonc_comments(text))


def _parse_jsonc(text: str) -> Any:
    return json.loads(_jsonc_clean(text))


def _skip_json_ws(text: str, index: int) -> int:
    while index < len(text) and text[index].isspace():
        index += 1
    return index


def _jsonc_object_members(text: str, opening: int) -> list[_JsonMember]:
    # Keep trailing commas here so insertion and deletion can preserve their
    # exact separator positions. The full-document parser masks them below.
    clean = _mask_jsonc_comments(text)
    # Same offsets as `clean`, for decoding values that hold trailing commas of their own.
    decodable = _mask_trailing_jsonc_commas(clean)
    if opening >= len(clean) or clean[opening] != "{":
        raise ConfigError("expected a JSONC object")
    decoder = json.JSONDecoder()
    members: list[_JsonMember] = []
    index = _skip_json_ws(clean, opening + 1)
    while index < len(clean) and clean[index] != "}":
        key_start = index
        try:
            key, key_end = decoder.raw_decode(clean, index)
        except json.JSONDecodeError as exc:
            raise ConfigError(f"invalid JSONC object key: {exc}") from exc
        if not isinstance(key, str):
            raise ConfigError("JSONC object keys must be strings")
        index = _skip_json_ws(clean, key_end)
        if index >= len(clean) or clean[index] != ":":
            raise ConfigError("expected ':' after JSONC object key")
        value_start = _skip_json_ws(clean, index + 1)
        try:
            _, value_end = decoder.raw_decode(decodable, value_start)
        except json.JSONDecodeError as exc:
            raise ConfigError(f"invalid JSONC object value: {exc}") from exc
        index = _skip_json_ws(clean, value_end)
        comma_after = None
        if index < len(clean) and clean[index] == ",":
            comma_after = index
            index = _skip_json_ws(clean, index + 1)
        elif index < len(clean) and clean[index] == "}":
            pass
        else:
            raise ConfigError("expected ',' or '}' in JSONC object")
        members.append(_JsonMember(key, key_start, value_start, value_end, comma_after))
    if index >= len(clean) or clean[index] != "}":
        raise ConfigError("unterminated JSONC object")
    return members


def _jsonc_find_member(text: str, opening: int, key: str) -> _JsonMember | None:
    matches = [member for member in _jsonc_object_members(text, opening) if member.key == key]
    if len(matches) > 1:
        raise ConfigError(f"duplicate JSONC key {key!r}")
    return matches[0] if matches else None


def _jsonc_root_open(text: str) -> int:
    clean = _jsonc_clean(text)
    index = _skip_json_ws(clean, 0)
    if index >= len(clean) or clean[index] != "{":
        raise ConfigError("config top level must be an object")
    return index


def _jsonc_container_open(text: str, member: _JsonMember) -> int:
    clean = _jsonc_clean(text)
    if member.value_start >= len(clean) or clean[member.value_start] != "{":
        raise ConfigError("server collection must be an object")
    return member.value_start


def _line_indent(text: str, position: int) -> str:
    start = text.rfind("\n", 0, position) + 1
    prefix = text[start:position]
    match = re.match(r"[ \t]*", prefix)
    return match.group(0) if match else ""


def _indent_unit(text: str) -> str:
    for line in text.splitlines():
        match = re.match(r"([ \t]+)\S", line)
        if match:
            prefix = match.group(1)
            return "\t" if "\t" in prefix else " " * max(1, min(len(prefix), 8))
    return "  "


def _format_jsonc_value(value: Any, *, prefix: str, unit: str, newline: str) -> str:
    rendered = json.dumps(value, ensure_ascii=False, indent=unit)
    lines = rendered.splitlines()
    if len(lines) > 1:
        rendered = lines[0] + newline + newline.join(prefix + line for line in lines[1:])
    return rendered


def _jsonc_insert_member(text: str, opening: int, key: str, value: Any, *, unit: str) -> str:
    clean = _jsonc_clean(text)
    members = _jsonc_object_members(text, opening)
    # The closing brace is the matching one at the current parse depth.
    depth = 0
    in_string = False
    closing = None
    index = opening
    while index < len(clean):
        char = clean[index]
        if in_string:
            if char == "\\":
                index += 1  # The escaped character cannot close the string.
            elif char == '"':
                in_string = False
        elif char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                closing = index
                break
        index += 1
    if closing is None:
        raise ConfigError("unterminated JSONC object")
    newline = "\r\n" if "\r\n" in text else "\n"
    object_indent = _line_indent(text, opening)
    child_indent = object_indent + unit
    rendered = _format_jsonc_value(value, prefix=child_indent, unit=unit, newline=newline)
    property_text = f"{child_indent}{json.dumps(key, ensure_ascii=False)}: {rendered}"
    if not members:
        inner = text[opening + 1 : closing].rstrip()
        if inner.strip():
            replacement = inner + newline + property_text + newline + object_indent
        else:
            replacement = newline + property_text + newline + object_indent
        return text[: opening + 1] + replacement + text[closing:]

    last = members[-1]
    if last.comma_after is not None:
        after_comma = _skip_json_ws(clean, last.comma_after + 1)
        if after_comma == closing:
            tail = text[last.comma_after + 1 : closing].rstrip()
            replacement = tail + newline + property_text + newline + object_indent
            return text[: last.comma_after + 1] + replacement + text[closing:]
    tail = text[last.value_end:closing].rstrip()
    replacement = "," + tail + newline + property_text + newline + object_indent
    return text[: last.value_end] + replacement + text[closing:]


def _jsonc_replace_member(text: str, member: _JsonMember, value: Any, *, unit: str) -> str:
    newline = "\r\n" if "\r\n" in text else "\n"
    prefix = _line_indent(text, member.key_start)
    rendered = _format_jsonc_value(value, prefix=prefix, unit=unit, newline=newline)
    return text[: member.value_start] + rendered + text[member.value_end :]


def _jsonc_remove_member(text: str, opening: int, member: _JsonMember) -> str:
    clean = _jsonc_clean(text)
    members = _jsonc_object_members(text, opening)
    if len(members) == 1:
        start = member.key_start
        end = member.value_end
        if member.comma_after is not None:
            end = member.comma_after + 1
        return text[:start] + text[end:]
    position = next(i for i, candidate in enumerate(members) if candidate.key_start == member.key_start)
    if position < len(members) - 1 or member.comma_after is not None:
        end = member.comma_after + 1 if member.comma_after is not None else member.value_end
        return text[: member.key_start] + text[end:]
    previous = members[position - 1]
    if previous.comma_after is None:
        raise ConfigError("could not locate JSONC separator before aiad")
    # Remove the preceding separator and the aiad key/value separately so
    # comments and whitespace between neighboring properties survive.
    ranges = sorted(
        ((previous.comma_after, previous.comma_after + 1), (member.key_start, member.value_end)),
        reverse=True,
    )
    for start, end in ranges:
        text = text[:start] + text[end:]
    return text


def _set_jsonc(definition: _ClientDefinition, text: str, entry: dict[str, Any]) -> str:
    document = parse_document(definition, text)
    if get_entry(definition, document) == entry:
        return text
    root_open = _jsonc_root_open(text)
    root_member = _jsonc_find_member(text, root_open, definition.root_key)
    unit = _indent_unit(text)
    if root_member is None:
        rendered = _jsonc_insert_member(
            text,
            root_open,
            definition.root_key,
            {"aiad": entry},
            unit=unit,
        )
    else:
        container_open = _jsonc_container_open(text, root_member)
        aiad_member = _jsonc_find_member(text, container_open, "aiad")
        if aiad_member is None:
            rendered = _jsonc_insert_member(text, container_open, "aiad", entry, unit=unit)
        else:
            rendered = _jsonc_replace_member(text, aiad_member, entry, unit=unit)
    parsed = parse_document(definition, rendered)
    if get_entry(definition, parsed) != entry:
        raise ConfigError("edited JSONC did not preserve the aiad entry")
    return rendered


def _remove_jsonc(definition: _ClientDefinition, text: str) -> str:
    parse_document(definition, text)
    root_open = _jsonc_root_open(text)
    root_member = _jsonc_find_member(text, root_open, definition.root_key)
    if root_member is None:
        return text
    container_open = _jsonc_container_open(text, root_member)
    aiad_member = _jsonc_find_member(text, container_open, "aiad")
    if aiad_member is None:
        return text
    rendered = _jsonc_remove_member(text, container_open, aiad_member)
    parsed = parse_document(definition, rendered)
    if get_entry(definition, parsed) is not ABSENT:
        raise ConfigError("edited JSONC still contains the aiad entry")
    return rendered


# ---- TOML (Codex) --------------------------------------------------------


@dataclass(frozen=True)
class _TomlSection:
    path: tuple[str, ...]
    start: int
    header_end: int
    end: int


def _split_toml_dotted_key(value: str) -> tuple[str, ...]:
    parts: list[str] = []
    start = 0
    quote: str | None = None
    escaped = False
    for index, char in enumerate(value):
        if quote:
            if quote == '"' and char == "\\" and not escaped:
                escaped = True
                continue
            if char == quote and not escaped:
                quote = None
            escaped = False
        elif char in "\"'":
            quote = char
        elif char == ".":
            parts.append(_decode_toml_key(value[start:index].strip()))
            start = index + 1
    parts.append(_decode_toml_key(value[start:].strip()))
    return tuple(parts)


def _decode_toml_key(raw: str) -> str:
    if raw.startswith('"'):
        try:
            return json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ConfigError(f"invalid TOML quoted key: {raw}") from exc
    if raw.startswith("'") and raw.endswith("'"):
        return raw[1:-1]
    if not raw:
        raise ConfigError("empty TOML table name")
    return raw


def _toml_sections(text: str) -> list[_TomlSection]:
    lines = text.splitlines(keepends=True)
    offsets: list[tuple[int, int, str]] = []
    offset = 0
    header_re = re.compile(r"^\s*\[(?!\[)(.*?)\]\s*(?:#.*)?(?:\r?\n)?$")
    for line in lines:
        match = header_re.match(line)
        if match:
            offsets.append((offset, offset + len(line), match.group(1)))
        offset += len(line)
    sections: list[_TomlSection] = []
    for index, (start, header_end, raw_path) in enumerate(offsets):
        end = offsets[index + 1][0] if index + 1 < len(offsets) else len(text)
        sections.append(_TomlSection(_split_toml_dotted_key(raw_path), start, header_end, end))
    return sections


def _toml_quote(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def _toml_key(value: str) -> str:
    if re.fullmatch(r"[A-Za-z0-9_-]+", value):
        return value
    return _toml_quote(value)


def _toml_value(value: Any) -> str:
    if isinstance(value, str):
        return _toml_quote(value)
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, (int, float)):
        return repr(value)
    if isinstance(value, list):
        return "[" + ", ".join(_toml_value(item) for item in value) + "]"
    if isinstance(value, dict):
        return "{ " + ", ".join(f"{_toml_key(str(key))} = {_toml_value(item)}" for key, item in value.items()) + " }"
    raise ConfigError(f"cannot preserve TOML value of type {type(value).__name__}")


def _format_toml_entry(entry: dict[str, Any]) -> str:
    lines = ["[mcp_servers.aiad]"]
    env = entry.get("env", {})
    if not isinstance(env, dict):
        raise ConfigError("Codex aiad env must be a mapping")
    for key, value in entry.items():
        if key == "env":
            continue
        lines.append(f"{_toml_key(str(key))} = {_toml_value(value)}")
    lines.append("")
    lines.append("[mcp_servers.aiad.env]")
    for key, value in env.items():
        lines.append(f"{_toml_key(str(key))} = {_toml_value(value)}")
    return "\n".join(lines) + "\n"


def _toml_is_target_section(path: tuple[str, ...]) -> bool:
    return len(path) >= 2 and path[:2] == ("mcp_servers", "aiad")


def _remove_toml_sections(text: str) -> tuple[str, bool]:
    sections = _toml_sections(text)
    targets = [section for section in sections if _toml_is_target_section(section.path)]
    if not targets:
        return text, False
    target_starts = {section.start for section in targets}
    ranges = [(section.start, section.end) for section in targets]
    # Remove AIAD table headers and assignments while retaining comments and
    # blank lines; these are the parts that can belong to neighboring config.
    pieces: list[str] = []
    offset = 0
    for line in text.splitlines(keepends=True):
        line_start = offset
        line_end = offset + len(line)
        offset = line_end
        containing = next(((start, end) for start, end in ranges if start <= line_start < end), None)
        if containing is None:
            pieces.append(line)
            continue
        stripped = line.strip()
        if stripped.startswith("#"):
            pieces.append(line)
        elif line_start in target_starts or re.match(r"^\s*\[", line):
            continue
        else:
            continue
    return "".join(pieces), True


def _toml_inline_root_assignment(text: str) -> tuple[int, int] | None:
    before_tables = text.split("[", 1)[0]
    match = re.search(r"(?m)^\s*(?:mcp_servers|\"mcp_servers\"|'mcp_servers')\s*=\s*\{", before_tables)
    if not match:
        return None
    opening = text.find("{", match.start(), match.end())
    closing = _matching_delimiter(text, opening, "{", "}")
    if closing is None:
        raise ConfigError("unterminated TOML inline mcp_servers table")
    return opening, closing


def _matching_delimiter(text: str, opening: int, left: str, right: str) -> int | None:
    depth = 0
    quote: str | None = None
    triple = False
    escaped = False
    comment = False
    index = opening
    while index < len(text):
        char = text[index]
        if comment:
            if char in "\r\n":
                comment = False
            index += 1
            continue
        if quote:
            if quote == '"' and char == "\\" and not escaped:
                escaped = True
                index += 1
                continue
            if triple and text.startswith(quote * 3, index) and not escaped:
                quote = None
                triple = False
                index += 3
                continue
            if not triple and char == quote and not escaped:
                quote = None
            escaped = False
            index += 1
            continue
        if char == "#":
            comment = True
        elif char in "\"'":
            if char == '"' and text.startswith('"""', index):
                quote = '"'
                triple = True
                index += 3
                continue
            quote = char
        elif char == left:
            depth += 1
        elif char == right:
            depth -= 1
            if depth == 0:
                return index
        index += 1
    return None


def _split_toml_inline_members(text: str, opening: int, closing: int) -> list[tuple[str, int, int, int, int]]:
    """Return (key, key_start, value_start, value_end, comma_after) members."""
    members: list[tuple[str, int, int, int, int]] = []
    index = opening + 1
    while index < closing:
        while index < closing and (text[index].isspace() or text[index] == ","):
            index += 1
        if index >= closing:
            break
        key_start = index
        quote: str | None = None
        escaped = False
        while index < closing:
            char = text[index]
            if quote:
                if quote == '"' and char == "\\" and not escaped:
                    escaped = True
                elif char == quote and not escaped:
                    quote = None
                else:
                    escaped = False
            elif char in "\"'":
                quote = char
            elif char == "=":
                break
            index += 1
        if index >= closing:
            raise ConfigError("invalid TOML inline table key")
        raw_key = text[key_start:index].strip()
        key = _decode_toml_key(raw_key)
        value_start = index + 1
        while value_start < closing and text[value_start].isspace():
            value_start += 1
        value_end = value_start
        stack: list[str] = []
        quote = None
        escaped = False
        while value_end < closing:
            char = text[value_end]
            if quote:
                if quote == '"' and char == "\\" and not escaped:
                    escaped = True
                elif char == quote and not escaped:
                    quote = None
                else:
                    escaped = False
            elif char in "\"'":
                quote = char
            elif char in "[{":
                stack.append(char)
            elif char in "]}":
                if stack:
                    stack.pop()
            elif char == "," and not stack:
                break
            value_end += 1
        content_end = value_end
        while content_end > value_start and text[content_end - 1].isspace():
            content_end -= 1
        comma_after = value_end if value_end < closing and text[value_end] == "," else -1
        members.append((key, key_start, value_start, content_end, comma_after))
        index = value_end + (1 if comma_after >= 0 else 0)
    return members


def _toml_inline_entry(entry: dict[str, Any]) -> str:
    # Inline mcp_servers tables cannot be extended with TOML table headers;
    # use an inline nested env table to preserve the existing root layout.
    return "{ " + ", ".join(f"{_toml_key(str(key))} = {_toml_value(value)}" for key, value in entry.items()) + " }"


def _edit_toml_inline(text: str, entry: dict[str, Any] | None) -> str:
    bounds = _toml_inline_root_assignment(text)
    if bounds is None:
        raise ConfigError("could not locate inline mcp_servers table")
    opening, closing = bounds
    members = _split_toml_inline_members(text, opening, closing)
    aiad = [member for member in members if member[0] == "aiad"]
    if len(aiad) > 1:
        raise ConfigError("duplicate aiad entries in inline mcp_servers table")
    if entry is None:
        if not aiad:
            return text
        _, key_start, _, value_end, comma_after = aiad[0]
        if comma_after >= 0:
            end = comma_after + 1
            return text[:key_start] + text[end:]
        position = members.index(aiad[0])
        if position == 0:
            return text[:key_start] + text[value_end:closing] + text[closing:]
        previous = members[position - 1]
        if previous[4] < 0:
            raise ConfigError("could not locate inline TOML separator before aiad")
        ranges = sorted(((previous[4], previous[4] + 1), (key_start, value_end)), reverse=True)
        for start, end in ranges:
            text = text[:start] + text[end:]
        return text
    rendered = _toml_inline_entry(entry)
    if aiad:
        _, _, value_start, value_end, _ = aiad[0]
        return text[:value_start] + rendered + text[value_end:]
    if members:
        last = members[-1]
        if last[4] >= 0:
            return text[: closing] + f" aiad = {rendered}," + text[closing:]
        return text[: last[3]] + f", aiad = {rendered}" + text[last[3] :]
    return text[: closing] + f"aiad = {rendered}" + text[closing:]


def _set_toml(definition: _ClientDefinition, text: str, entry: dict[str, Any]) -> str:
    document = parse_document(definition, text)
    entry_value = get_entry(definition, document)
    sections = _toml_sections(text)
    inline_root = _toml_inline_root_assignment(text)
    has_mcp_sections = any(section.path and section.path[0] == "mcp_servers" for section in sections)
    if inline_root is not None and not has_mcp_sections:
        rendered = _edit_toml_inline(text, entry)
        parsed = parse_document(definition, rendered)
        if get_entry(definition, parsed) != entry:
            raise ConfigError("edited inline TOML did not preserve the aiad entry")
        return rendered
    without_aiad, _ = _remove_toml_sections(text)
    addition = _format_toml_entry(entry).replace("\n", "\r\n") if "\r\n" in text else _format_toml_entry(entry)
    if without_aiad and not without_aiad.endswith(("\n", "\r")):
        without_aiad += "\n"
    if without_aiad.strip():
        if not without_aiad.endswith("\n\n"):
            without_aiad += "\n"
    rendered = without_aiad + addition
    parsed = parse_document(definition, rendered)
    if get_entry(definition, parsed) != entry:
        raise ConfigError("edited TOML did not preserve the aiad entry")
    return rendered


def _remove_toml(definition: _ClientDefinition, text: str) -> str:
    document = parse_document(definition, text)
    entry = get_entry(definition, document)
    if entry is ABSENT:
        return text
    sections = _toml_sections(text)
    if any(_toml_is_target_section(section.path) for section in sections):
        rendered, _ = _remove_toml_sections(text)
    else:
        root_bounds = _toml_inline_root_assignment(text)
        if root_bounds is None:
            raise ConfigError("aiad exists in TOML but its text location was not found")
        rendered = _edit_toml_inline(text, None)
    parsed = parse_document(definition, rendered)
    if get_entry(definition, parsed) is not ABSENT:
        raise ConfigError("edited TOML still contains the aiad entry")
    return rendered


# ---- YAML (Goose) --------------------------------------------------------


def _yaml_entry_text(entry: dict[str, Any], indent: str, newline: str) -> str:
    def scalar(value: Any) -> str:
        return json.dumps(value, ensure_ascii=False, separators=(",", ": "))

    lines = [
        f"{indent}aiad:",
        f"{indent}  enabled: true",
        f"{indent}  type: stdio",
        f"{indent}  name: {scalar(entry['name'])}",
        f"{indent}  cmd: {scalar(entry['cmd'])}",
        f"{indent}  args: {scalar(entry['args'])}",
        f"{indent}  envs: {scalar(entry['envs'])}",
        f"{indent}  timeout: {entry['timeout']}",
    ]
    return newline.join(lines) + newline


def _yaml_line_without_eol(line: str) -> str:
    return line.rstrip("\r\n")


def _yaml_top_extensions(lines: list[str]) -> tuple[int, str] | None:
    pattern = re.compile(r"^(?P<indent> *)(?:extensions|\"extensions\"|'extensions')\s*:(?P<rest>.*)$")
    for index, line in enumerate(lines):
        match = pattern.match(_yaml_line_without_eol(line))
        if match and not match.group("indent"):
            return index, match.group("rest")
    return None


def _yaml_block_bounds(lines: list[str], start: int) -> tuple[int, int]:
    end = len(lines)
    for index in range(start + 1, len(lines)):
        body = _yaml_line_without_eol(lines[index])
        stripped = body.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped == "...":
            return start, index
        indent = len(body) - len(body.lstrip(" "))
        if indent == 0:
            return start, index
    return start, end


def _yaml_entry_bounds(lines: list[str], block_start: int, block_end: int, child_indent: int) -> tuple[int, int] | None:
    pattern = re.compile(r"^(?P<indent> *)(?:aiad|\"aiad\"|'aiad')\s*:")
    for index in range(block_start + 1, block_end):
        body = _yaml_line_without_eol(lines[index])
        match = pattern.match(body)
        if match and len(match.group("indent")) == child_indent:
            end = block_end
            for next_index in range(index + 1, block_end):
                next_body = _yaml_line_without_eol(lines[next_index])
                stripped = next_body.strip()
                if not stripped or stripped.startswith("#"):
                    continue
                indent = len(next_body) - len(next_body.lstrip(" "))
                if indent <= child_indent:
                    end = next_index
                    break
            return index, end
    return None


def _yaml_set_block(definition: _ClientDefinition, text: str, entry: dict[str, Any]) -> str:
    document = parse_document(definition, text)
    lines = text.splitlines(keepends=True)
    newline = "\r\n" if "\r\n" in text else "\n"
    ext = _yaml_top_extensions(lines)
    if ext is None:
        snippet = "extensions:" + newline + _yaml_entry_text(entry, "  ", newline)
        if not text:
            rendered = snippet
        elif text.endswith(("\n", "\r")):
            rendered = text + snippet
        else:
            rendered = text + newline + snippet
    else:
        ext_index, rest = ext
        rest_value = rest.split("#", 1)[0].strip()
        if rest_value:
            if rest_value.startswith("{"):
                raise ConfigError("Goose extensions use a flow YAML map; safe text editing is not supported")
            raise ConfigError("Goose extensions must be a block mapping to edit safely")
        block_start, block_end = _yaml_block_bounds(lines, ext_index)
        ext_indent = len(re.match(r"^ *", _yaml_line_without_eol(lines[ext_index])).group(0))
        child_indent = None
        for index in range(block_start + 1, block_end):
            body = _yaml_line_without_eol(lines[index])
            stripped = body.strip()
            if not stripped or stripped.startswith("#"):
                continue
            indent = len(body) - len(body.lstrip(" "))
            if indent > ext_indent:
                child_indent = indent if child_indent is None else min(child_indent, indent)
        if child_indent is None:
            child_indent = ext_indent + 2
        existing = _yaml_entry_bounds(lines, block_start, block_end, child_indent)
        snippet = _yaml_entry_text(entry, " " * child_indent, newline)
        if existing:
            first, last = existing
            comments_and_blanks = [
                line
                for line in lines[first:last]
                if not _yaml_line_without_eol(line).strip()
                or _yaml_line_without_eol(line).lstrip().startswith("#")
            ]
            lines[first:last] = [snippet, *comments_and_blanks]
            rendered = "".join(lines)
        else:
            insertion = block_end
            if insertion > 0 and lines[insertion - 1] and not lines[insertion - 1].endswith(("\n", "\r")):
                lines[insertion - 1] += newline
            lines.insert(insertion, snippet)
            rendered = "".join(lines)
    parsed = parse_document(definition, rendered)
    if get_entry(definition, parsed) != entry:
        raise ConfigError("edited YAML did not preserve the aiad entry")
    return rendered


def _yaml_remove_block(definition: _ClientDefinition, text: str) -> str:
    document = parse_document(definition, text)
    if get_entry(definition, document) is ABSENT:
        return text
    lines = text.splitlines(keepends=True)
    ext = _yaml_top_extensions(lines)
    if ext is None:
        raise ConfigError("Goose aiad entry exists but its YAML text location was not found")
    ext_index, rest = ext
    if rest.split("#", 1)[0].strip():
        raise ConfigError("Goose extensions must be a block mapping to remove safely")
    block_start, block_end = _yaml_block_bounds(lines, ext_index)
    ext_indent = len(re.match(r"^ *", _yaml_line_without_eol(lines[ext_index])).group(0))
    child_indent = None
    for index in range(block_start + 1, block_end):
        body = _yaml_line_without_eol(lines[index])
        stripped = body.strip()
        if not stripped or stripped.startswith("#"):
            continue
        indent = len(body) - len(body.lstrip(" "))
        if indent > ext_indent:
            child_indent = indent if child_indent is None else min(child_indent, indent)
    child_indent = child_indent if child_indent is not None else ext_indent + 2
    existing = _yaml_entry_bounds(lines, block_start, block_end, child_indent)
    if existing is None:
        raise ConfigError("Goose aiad entry exists but its YAML text location was not found")
    first, last = existing
    comments_and_blanks = [
        line
        for line in lines[first:last]
        if not _yaml_line_without_eol(line).strip()
        or _yaml_line_without_eol(line).lstrip().startswith("#")
    ]
    lines[first:last] = comments_and_blanks
    rendered = "".join(lines)
    parsed = parse_document(definition, rendered)
    if get_entry(definition, parsed) is not ABSENT:
        raise ConfigError("edited YAML still contains the aiad entry")
    return rendered


def _set_yaml(definition: _ClientDefinition, text: str, entry: dict[str, Any]) -> str:
    return _yaml_set_block(definition, text, entry)


def _remove_yaml(definition: _ClientDefinition, text: str) -> str:
    return _yaml_remove_block(definition, text)

