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
    return container.get("aad", ABSENT)


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
        if not isinstance(container, dict) or "aad" not in container:
            return text
        del container["aad"]
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
    container["aad"] = entry
    rendered = _dump_json(document, text)
    parsed = parse_document(definition, rendered)
    if get_entry(definition, parsed) != entry:
        raise ConfigError("edited JSON did not preserve the aad entry")
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
    _jsonc_clean(text)
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
        raise ConfigError("could not locate JSONC separator before aad")
    # Remove the preceding separator and the aad key/value separately so
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
            {"aad": entry},
            unit=unit,
        )
    else:
        container_open = _jsonc_container_open(text, root_member)
        aad_member = _jsonc_find_member(text, container_open, "aad")
        if aad_member is None:
            rendered = _jsonc_insert_member(text, container_open, "aad", entry, unit=unit)
        else:
            rendered = _jsonc_replace_member(text, aad_member, entry, unit=unit)
    parsed = parse_document(definition, rendered)
    if get_entry(definition, parsed) != entry:
        raise ConfigError("edited JSONC did not preserve the aad entry")
    return rendered


def _remove_jsonc(definition: _ClientDefinition, text: str) -> str:
    parse_document(definition, text)
    root_open = _jsonc_root_open(text)
    root_member = _jsonc_find_member(text, root_open, definition.root_key)
    if root_member is None:
        return text
    container_open = _jsonc_container_open(text, root_member)
    aad_member = _jsonc_find_member(text, container_open, "aad")
    if aad_member is None:
        return text
    rendered = _jsonc_remove_member(text, container_open, aad_member)
    parsed = parse_document(definition, rendered)
    if get_entry(definition, parsed) is not ABSENT:
        raise ConfigError("edited JSONC still contains the aad entry")
    return rendered


# ---- TOML (Codex) --------------------------------------------------------


def _set_toml(definition: _ClientDefinition, text: str, entry: dict[str, Any]) -> str:
    import tomlkit

    original = parse_document(definition, text)
    if get_entry(definition, original) == entry:
        return text
    document = tomlkit.parse(text)
    container = document.setdefault(definition.root_key, tomlkit.table())
    if not isinstance(container, dict):
        raise ConfigError("Codex server collection must be a table")
    container["aad"] = entry
    rendered = tomlkit.dumps(document)
    if get_entry(definition, parse_document(definition, rendered)) != entry:
        raise ConfigError("edited TOML did not preserve the aad entry")
    return rendered


def _remove_toml(definition: _ClientDefinition, text: str) -> str:
    import tomlkit

    original = parse_document(definition, text)
    if get_entry(definition, original) is ABSENT:
        return text
    document = tomlkit.parse(text)
    del document[definition.root_key]["aad"]
    rendered = tomlkit.dumps(document)
    if get_entry(definition, parse_document(definition, rendered)) is not ABSENT:
        raise ConfigError("edited TOML still contains the aad entry")
    return rendered


# ---- YAML (Goose) --------------------------------------------------------


def _yaml_entry_text(entry: dict[str, Any], indent: str, newline: str) -> str:
    def scalar(value: Any) -> str:
        return json.dumps(value, ensure_ascii=False, separators=(",", ": "))

    lines = [f"{indent}aad:"]
    lines.extend(f"{indent}  {scalar(key)}: {scalar(value)}" for key, value in entry.items())
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
    pattern = re.compile(r"^(?P<indent> *)(?:aad|\"aad\"|'aad')\s*:")
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
    parse_document(definition, text)
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
        raise ConfigError("edited YAML did not preserve the aad entry")
    return rendered


def _yaml_remove_block(definition: _ClientDefinition, text: str) -> str:
    document = parse_document(definition, text)
    if get_entry(definition, document) is ABSENT:
        return text
    lines = text.splitlines(keepends=True)
    ext = _yaml_top_extensions(lines)
    if ext is None:
        raise ConfigError("Goose aad entry exists but its YAML text location was not found")
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
        raise ConfigError("Goose aad entry exists but its YAML text location was not found")
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
        raise ConfigError("edited YAML still contains the aad entry")
    return rendered


def _set_yaml(definition: _ClientDefinition, text: str, entry: dict[str, Any]) -> str:
    return _yaml_set_block(definition, text, entry)


def _remove_yaml(definition: _ClientDefinition, text: str) -> str:
    return _yaml_remove_block(definition, text)

