"""Parse specifications without silently dropping duplicate YAML keys."""

from __future__ import annotations

from typing import Any

import yaml


class _UniqueLoader(yaml.SafeLoader):
    def construct_mapping(self, node: yaml.MappingNode, deep: bool = False) -> dict[Any, Any]:
        self.flatten_mapping(node)
        result: dict[Any, Any] = {}
        for key_node, value_node in node.value:
            key = self.construct_object(key_node, deep=deep)
            try:
                if key in result:
                    raise ValueError(f"duplicate YAML key {key!r} at line {key_node.start_mark.line + 1}")
                result[key] = self.construct_object(value_node, deep=deep)
            except TypeError as exc:
                raise ValueError("YAML mapping keys must be scalar values") from exc
        return result


def parse_spec(text: str | None) -> dict[str, Any]:
    if not text:
        return {}
    try:
        value = yaml.load(text, Loader=_UniqueLoader)
    except yaml.YAMLError as exc:
        raise ValueError(str(exc)) from exc
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError("the document must be a mapping")
    return value
