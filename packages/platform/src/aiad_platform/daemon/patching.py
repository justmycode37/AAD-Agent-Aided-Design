"""Strict unified-diff application for `aiad_build(patch=...)`. Never fuzzy: a stale patch fails."""

from __future__ import annotations

import re

_HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


class PatchError(ValueError):
    pass


def apply_patch(source: str, patch: str) -> str:
    lines = source.splitlines(keepends=True)
    result: list[str] = []
    cursor = 0
    patch_lines = patch.splitlines()
    index = 0
    hunks = 0
    while index < len(patch_lines):
        match = _HUNK.match(patch_lines[index])
        if match is None:
            if hunks and patch_lines[index].strip():
                raise PatchError(f"Unexpected text after hunk {hunks}: {patch_lines[index]!r}.")
            index += 1
            continue
        hunks += 1
        old_start = int(match.group(1))
        old_count = int(match.group(2) or 1)
        new_count = int(match.group(4) or 1)
        start = max(old_start - 1, 0) if old_count else old_start
        if start < cursor or start > len(lines):
            raise PatchError(f"Hunk {hunks} starts at line {old_start}, which does not fit this revision.")
        result.extend(lines[cursor:start])
        cursor = start
        new_start = int(match.group(3))
        expected_new_start = max(new_start - 1, 0) if new_count else new_start
        if expected_new_start != len(result):
            raise PatchError(f"Hunk {hunks} has inconsistent new line coordinates.")
        index += 1
        removed = added = 0
        while removed < old_count or added < new_count:
            if index >= len(patch_lines):
                raise PatchError(f"Hunk {hunks} is truncated.")
            line = patch_lines[index]
            tag, text = (line[0], line[1:]) if line else ("", "")
            no_newline = index + 1 < len(patch_lines) and patch_lines[index + 1] == "\\ No newline at end of file"
            value = text if no_newline else text + "\n"
            if tag == "+":
                result.append(value)
                added += 1
            elif tag in (" ", "-"):
                if cursor >= len(lines) or lines[cursor] != value:
                    actual = lines[cursor].rstrip("\n") if cursor < len(lines) else "<end of file>"
                    raise PatchError(
                        f"Hunk {hunks} does not match at line {cursor + 1}: expected {text!r}, found {actual!r}."
                    )
                if tag == " ":
                    result.append(lines[cursor])
                    added += 1
                cursor += 1
                removed += 1
            else:
                raise PatchError(f"Hunk {hunks} has a malformed line: {line!r}.")
            if removed > old_count or added > new_count:
                raise PatchError(f"Hunk {hunks} exceeds its declared line counts.")
            index += 2 if no_newline else 1
        if index < len(patch_lines) and patch_lines[index].startswith((" ", "+", "-", "\\")):
            raise PatchError(f"Hunk {hunks} has extra lines beyond its declared counts.")
    if hunks == 0:
        raise PatchError("The patch has no hunks. Send a unified diff with @@ headers.")
    result.extend(lines[cursor:])
    return "".join(result)
