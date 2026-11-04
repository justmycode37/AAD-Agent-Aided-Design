"""Strict unified-diff application for `aiad_build(patch=...)`. Never fuzzy: a stale patch fails."""

from __future__ import annotations

import re

_HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


class PatchError(ValueError):
    pass


def apply_patch(source: str, patch: str) -> str:
    lines = source.splitlines(keepends=True)
    if lines and not lines[-1].endswith("\n"):
        lines[-1] += "\n"
    result: list[str] = []
    cursor = 0
    patch_lines = patch.splitlines()
    index = 0
    hunks = 0
    while index < len(patch_lines):
        match = _HUNK.match(patch_lines[index])
        if match is None:
            index += 1
            continue
        hunks += 1
        old_start = int(match.group(1))
        start = max(old_start - 1, 0) if match.group(2) != "0" else old_start
        if start < cursor or start > len(lines):
            raise PatchError(f"Hunk {hunks} starts at line {old_start}, which does not fit this revision.")
        result.extend(lines[cursor:start])
        cursor = start
        index += 1
        while index < len(patch_lines) and not patch_lines[index].startswith("@@"):
            line = patch_lines[index]
            if line.startswith("\\"):
                index += 1
                continue
            if line.startswith(("--- ", "+++ ")) and not line.startswith(("---\t", "+++\t")):
                break
            tag, text = (line[0], line[1:]) if line else (" ", "")
            if tag == "+":
                result.append(text + "\n")
            elif tag in (" ", "-"):
                if cursor >= len(lines) or lines[cursor].rstrip("\n") != text:
                    actual = lines[cursor].rstrip("\n") if cursor < len(lines) else "<end of file>"
                    raise PatchError(
                        f"Hunk {hunks} does not match at line {cursor + 1}: expected {text!r}, found {actual!r}."
                    )
                if tag == " ":
                    result.append(lines[cursor])
                cursor += 1
            else:
                raise PatchError(f"Hunk {hunks} has a malformed line: {line!r}.")
            index += 1
    if hunks == 0:
        raise PatchError("The patch has no hunks. Send a unified diff with @@ headers.")
    result.extend(lines[cursor:])
    return "".join(result)
