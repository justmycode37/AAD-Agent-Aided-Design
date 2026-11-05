"""Build123d-aware modeling helpers used by ``from aad import *`` scripts."""

from __future__ import annotations

import ast
import inspect
import json
from copy import deepcopy
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any

from ._runtime import ModelInputError
from .engine.types import Entity, FeatureInfo

_metadata: dict[str, Any] = {}


@dataclass
class _FeatureDeclaration:
    name: str
    intent: str | None
    line_start: int | None
    line_end: int | None = None
    deps: list[str] = field(default_factory=list)


@dataclass
class _OwnedShape:
    shape: Any
    feature: str | None


@dataclass
class _BuilderState:
    builder: Any
    faces: list[_OwnedShape] = field(default_factory=list)
    edges: list[_OwnedShape] = field(default_factory=list)
    vertices: list[_OwnedShape] = field(default_factory=list)
    last_shape: Any | None = None


class _ModelState:
    def __init__(self) -> None:
        self.declarations: list[_FeatureDeclaration] = []
        self.names: set[str] = set()
        self.builders: dict[int, _BuilderState] = {}
        self.active_feature: _FeatureDeclaration | None = None
        self.active_builder: Any | None = None
        self.named_part_called = False
        self.named_part_name: str | None = None
        self.named_builder: Any | None = None
        self.source_line_count = 0
        self.block_ends: dict[int, int] = {}

    def state_for(self, builder: Any, before: Any | None = None) -> _BuilderState:
        key = id(builder)
        state = self.builders.get(key)
        if state is None:
            state = _BuilderState(builder=builder)
            self.builders[key] = state
            if before is not None:
                for kind, attr in _TOPOLOGIES.items():
                    setattr(state, attr, [_OwnedShape(shape, "main") for shape in _topologies(before, kind)])
                state.last_shape = before
        return state


_state = _ModelState()
_TOPOLOGIES = {"face": "faces", "edge": "edges", "vertex": "vertices"}


def configure_modeling(source: str = "") -> None:
    """Reset feature and metadata state before a worker executes model.py."""
    global _state, _metadata
    _state = _ModelState()
    _state.source_line_count = max(1, source.count("\n") + 1) if source else 0
    if source:
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, ast.With):
                for item in node.items:
                    call = item.context_expr
                    if isinstance(call, ast.Call) and (
                        isinstance(call.func, ast.Name) and call.func.id == "feature"
                        or isinstance(call.func, ast.Attribute) and call.func.attr == "feature"
                    ):
                        _state.block_ends[call.lineno] = node.end_lineno
                        _state.block_ends[node.lineno] = node.end_lineno
    _metadata = {}


def meta(**values: Any) -> dict[str, Any]:
    """Record JSON-compatible model metadata and return a copy of the result."""
    for key, value in values.items():
        if not key or key.startswith("__aad_internal_"):
            raise ModelInputError(f"metadata key {key!r} is reserved or empty")
        try:
            json.dumps(value, allow_nan=False)
        except (TypeError, ValueError) as exc:
            raise ModelInputError(f"metadata field {key!r} must contain finite JSON values") from exc
        if key == "units" and value not in ("mm", "in"):
            raise ModelInputError("model units must be 'mm' or 'in'")
        if key in {"name", "material", "process"} and (
            not isinstance(value, str) or not value.strip()
        ):
            raise ModelInputError(f"metadata field {key!r} must be non-empty text")
        if key in {"units", "material", "process"} and key in _metadata and value != _metadata[key]:
            raise ModelInputError(f"conflicting {key} declarations in meta(): {_metadata[key]!r} and {value!r}")
    _metadata.update(deepcopy(values))
    return deepcopy(_metadata)


def context_metadata(payload: dict[str, Any]) -> dict[str, Any]:
    """Reconcile physical metadata without rescaling or relabelling coordinates."""
    metadata = current_meta()
    for key in ("units", "material", "process"):
        declarations = {
            "request": payload.get(key),
            "model": metadata.get(key),
            "spec": payload.get("spec", {}).get(key),
        }
        declared = [(source, value) for source, value in declarations.items() if value is not None]
        for source, value in declared:
            if not isinstance(value, str) or not value.strip() or (key == "units" and value not in ("mm", "in")):
                raise ModelInputError(f"{source} {key} declaration is invalid: {value!r}")
        if len({value for _, value in declared}) > 1:
            detail = ", ".join(f"{source}={value!r}" for source, value in declared)
            raise ModelInputError(f"conflicting {key} declarations: {detail}")
        if declared:
            metadata[key] = declared[0][1]
    return metadata


def named_shape() -> Any | None:
    """Return the declared part's output, if any (including output placement)."""
    return _builder_shape(_state.named_builder)


def current_part_name() -> str | None:
    return _state.named_part_name


def current_meta() -> dict[str, Any]:
    """Return a copy of model metadata recorded by :func:`meta`."""
    return deepcopy(_metadata)


def part(name: str, *placements: Any, **kwargs: Any) -> Any:
    """Open the model's single named :class:`build123d.BuildPart` context."""
    if not isinstance(name, str) or not name.strip():
        raise ModelInputError("part name must be a non-empty string")
    if _state.named_part_called:
        raise ModelInputError("only one named part() context is supported; assemblies are not implemented")
    _state.named_part_called = True
    _state.named_part_name = name
    return _NamedPart(name, placements, kwargs)


class _NamedPart:
    def __init__(self, name: str, placements: tuple[Any, ...], kwargs: dict[str, Any]) -> None:
        self.name = name
        self.placements = placements
        self.kwargs = kwargs
        self.builder: Any | None = None

    def __enter__(self) -> Any:
        import build123d

        self.builder = build123d.BuildPart(*self.placements, **self.kwargs)
        # Nested builders publish only within their original Python scope.
        # The named-part wrapper must expose its caller's construction frame.
        self.builder._python_frame = inspect.currentframe().f_back
        self.builder.label = self.name
        _state.named_builder = self.builder
        entered = self.builder.__enter__()
        return entered

    def __exit__(self, exception_type: Any, exception_value: Any, traceback: Any) -> Any:
        if self.builder is None:
            return False
        return self.builder.__exit__(exception_type, exception_value, traceback)


def feature(name: str, intent: str | None = None) -> Any:
    """Track geometry changes made in a named, unique BuildPart feature block."""
    if not isinstance(name, str) or not name.strip():
        raise ModelInputError("feature name must be a non-empty string")
    if name in {"main", "unknown"}:
        raise ModelInputError(f"feature name {name!r} is reserved for implicit or untracked topology")
    if intent is not None and not isinstance(intent, str):
        raise ModelInputError("feature intent must be text or None")
    if name in _state.names:
        raise ModelInputError(f"feature name {name!r} is already declared; feature names must be unique")
    if _state.active_feature is not None:
        raise ModelInputError("feature() blocks cannot be nested")
    return _Feature(name, intent)


class _Feature:
    def __init__(self, name: str, intent: str | None) -> None:
        self.declaration = _FeatureDeclaration(name=name, intent=intent, line_start=None)
        self.builder: Any | None = None

    def __enter__(self) -> _Feature:
        if self.declaration.name in _state.names:
            raise ModelInputError(f"feature name {self.declaration.name!r} is already declared; feature names must be unique")
        if _state.active_feature is not None:
            raise ModelInputError("feature() blocks cannot be nested")
        self.builder = _current_builder()
        if self.builder is None:
            raise ModelInputError("feature() must be used inside a build123d BuildPart or part() context")
        self.declaration.line_start = _caller_line()
        self.declaration.line_end = _state.block_ends.get(self.declaration.line_start)
        _state.state_for(self.builder, _builder_shape(self.builder))
        _state.names.add(self.declaration.name)
        _state.declarations.append(self.declaration)
        _state.active_feature = self.declaration
        _state.active_builder = self.builder
        return self

    def __exit__(self, exception_type: Any, exception_value: Any, traceback: Any) -> bool:
        try:
            if exception_value is not None:
                exception_value._aad_feature = self.declaration.name
            elif self.builder is not None:
                _synchronize_builder(self.builder, feature_name=self.declaration.name)
        finally:
            _state.active_feature = None
            _state.active_builder = None
        return False


def record_operation(builder: Any, before: Any | None, after: Any | None) -> None:
    """Accept one build123d builder operation and update conservative ancestry."""
    if after is None or _same_shape(before, after):
        return
    state = _state.state_for(builder, before)
    previous_shape = before if before is not None else state.last_shape
    _reconcile(state, previous_shape, after, _current_feature_name(builder))
    state.last_shape = after


def finalize_modeling(shape: Any) -> tuple[list[FeatureInfo], dict[str, Any]]:
    """Return final topology ownership and metadata for the worker context."""
    selected_state = _state_for_shape(shape)
    if selected_state is None:
        return _untracked_features(shape), current_meta()

    _synchronize_builder(selected_state.builder, feature_name=None)
    return _feature_infos(shape, selected_state), current_meta()


def live_features(builder: Any, shape: Any | None = None) -> list[FeatureInfo]:
    """Build a live feature view for selectors evaluated during model execution."""
    current_shape = shape if shape is not None else _builder_shape(builder)
    state = _state.state_for(builder, current_shape)
    _synchronize_builder(builder, feature_name=_current_feature_name(builder))
    return _feature_infos(current_shape, state)


def note_dependencies(entities: list[Entity]) -> None:
    """Add resolved feature references to the active feature's dependency list."""
    declaration = _state.active_feature
    if declaration is None:
        return
    for entity in entities:
        name = entity.feature
        if name is not None and name != declaration.name and name not in declaration.deps:
            declaration.deps.append(name)


def face(
    *,
    feature: str | None = None,
    kind: str | None = None,
    where: str | None = None,
    max_min_xyz: Any = None,
    parallel_xyz: Any = None,
    nth: int | None = None,
    expect: int | None = None,
) -> Any:
    """Select one face, or a requested number of faces, from the live part."""
    return _select_live("face", locals())


def edge(
    *,
    feature: str | None = None,
    kind: str | None = None,
    where: str | None = None,
    max_min_xyz: Any = None,
    parallel_xyz: Any = None,
    nth: int | None = None,
    expect: int | None = None,
) -> Any:
    """Select one edge, or a requested number of edges, from the live part."""
    return _select_live("edge", locals())


def vertex(
    *,
    feature: str | None = None,
    kind: str | None = None,
    where: str | None = None,
    max_min_xyz: Any = None,
    parallel_xyz: Any = None,
    nth: int | None = None,
    expect: int | None = None,
) -> Any:
    """Select one vertex, or a requested number of vertices, from the live part."""
    return _select_live("vertex", locals())


def _select_live(function: str, arguments: dict[str, Any]) -> Any:
    from .engine.select import entity_shape, resolve

    builder = _current_builder()
    if builder is None:
        raise ModelInputError(f"{function}() must be used inside a build123d BuildPart or part() context")
    shape = _builder_shape(builder)
    context = SimpleNamespace(shape=shape, features=live_features(builder, shape))
    selector = _selector_text(function, arguments)
    expected = arguments.get("expect")
    entities = resolve(context, selector, expect=expected)
    note_dependencies(entities)
    selected = [entity_shape(context, entity) for entity in entities]
    return selected[0] if len(selected) == 1 else selected


def _selector_text(function: str, arguments: dict[str, Any]) -> str:
    ordered = ("feature", "kind", "where", "max_min_xyz", "parallel_xyz", "nth")
    parts = [f"{name}={arguments[name]!r}" for name in ordered if arguments.get(name) is not None]
    return f"{function}({', '.join(parts)})"


def _current_builder() -> Any | None:
    try:
        import build123d

        return build123d.BuildPart._get_context(log=False)
    except Exception:
        return None


def _builder_shape(builder: Any) -> Any | None:
    if builder is None:
        return None
    try:
        return builder.part
    except Exception:
        return getattr(builder, "_obj", None)


def _current_feature_name(builder: Any) -> str | None:
    if _state.active_builder is builder and _state.active_feature is not None:
        return _state.active_feature.name
    return None


def _synchronize_builder(builder: Any, feature_name: str | None) -> None:
    state = _state.builders.get(id(builder))
    shape = _builder_shape(builder)
    if shape is None:
        return
    if state is None:
        state = _state.state_for(builder, shape)
        return
    if _same_shape(state.last_shape, shape):
        return
    _reconcile(state, state.last_shape, shape, feature_name)
    state.last_shape = shape


def _reconcile(
    state: _BuilderState,
    before: Any | None,
    after: Any,
    current_feature: str | None,
) -> None:
    history = getattr(after, "_history", None)
    for kind, owned_attr in _TOPOLOGIES.items():
        before_shapes = _topologies(before, kind) if before is not None else []
        old_owned: list[_OwnedShape] = getattr(state, owned_attr)
        old_owners = [_owner_for(shape, old_owned, fallback=None) for shape in before_shapes]
        after_shapes = _topologies(after, kind)
        new_owned: list[_OwnedShape] = []
        for result_shape in after_shapes:
            retained = _find_same(result_shape, before_shapes)
            if retained is not None:
                new_owned.append(_OwnedShape(result_shape, old_owners[retained]))
            else:
                # A before/after difference attributes a change to this block,
                # never to a guessed ancestor based on geometric similarity.
                new_owned.append(_OwnedShape(result_shape, current_feature or "main"))
        if current_feature is not None:
            dependencies = []
            for previous, owner in zip(before_shapes, old_owners, strict=True):
                if owner is not None and _changed_by_history(history, previous):
                    dependencies.append(Entity(feature=owner))
            note_dependencies(dependencies)
        setattr(state, owned_attr, new_owned)


def _changed_by_history(history: Any, previous: Any) -> bool:
    if history is None:
        return False
    wrapped = getattr(previous, "wrapped", None)
    if wrapped is None:
        return False
    try:
        return bool(history.modified(wrapped) or history.generated(wrapped) or history.is_removed(wrapped))
    except Exception:
        return False


def _owner_for(shape: Any, values: list[_OwnedShape], fallback: str | None) -> str | None:
    for item in values:
        if _same_topology(shape, item.shape):
            return item.feature
    return fallback


def _find_same(shape: Any, values: list[Any]) -> int | None:
    for index, other in enumerate(values):
        if _same_topology(shape, other):
            return index
    return None


def _same_topology(left: Any, right: Any) -> bool:
    try:
        return bool(left.is_same(right))
    except Exception:
        try:
            return bool(left.wrapped.IsSame(right.wrapped))
        except Exception:
            return False


def _same_shape(left: Any, right: Any) -> bool:
    if left is None or right is None:
        return left is right
    if _same_topology(left, right):
        return True
    for kind in _TOPOLOGIES:
        left_shapes, right_shapes = _topologies(left, kind), _topologies(right, kind)
        if len(left_shapes) != len(right_shapes) or any(_find_same(s, right_shapes) is None for s in left_shapes):
            return False
    return True


def _topologies(shape: Any | None, kind: str) -> list[Any]:
    if shape is None:
        return []
    try:
        return list(getattr(shape, {"face": "faces", "edge": "edges", "vertex": "vertices"}[kind])())
    except Exception:
        return []


def _state_for_shape(shape: Any) -> _BuilderState | None:
    states = list(reversed(list(_state.builders.values())))
    named = _state.builders.get(id(_state.named_builder))
    if named is not None:
        states = [named, *(state for state in states if state is not named)]
    for state in states:
        current = _builder_shape(state.builder)
        if current is not None and _same_shape(current, shape):
            return state
    for state in states:
        if state.last_shape is not None and _same_shape(state.last_shape, shape):
            return state
    return None


def _feature_infos(shape: Any, state: _BuilderState) -> list[FeatureInfo]:
    owners = {
        kind: [_owner_for(topology, getattr(state, attr), fallback=None) for topology in _topologies(shape, kind)]
        for kind, attr in _TOPOLOGIES.items()
    }
    return _make_feature_infos(shape, owners)


def _make_feature_infos(shape: Any, owners: dict[str, list[str | None]]) -> list[FeatureInfo]:
    from .engine.select import _geometry

    names: list[str] = [declaration.name for declaration in _state.declarations]
    has_main = any("main" in values for values in owners.values())
    has_unknown = any(None in values for values in owners.values())
    if has_main and "main" not in names:
        names.insert(0, "main")
    if has_unknown and "unknown" not in names:
        names.append("unknown")
    if not names:
        names = ["main"]

    declarations = {declaration.name: declaration for declaration in _state.declarations}
    result: dict[str, FeatureInfo] = {}
    for name in names:
        declaration = declarations.get(name)
        result[name] = FeatureInfo(
            name=name,
            line_start=declaration.line_start if declaration else (1 if name == "main" else None),
            line_end=declaration.line_end if declaration else (_state.source_line_count if name == "main" else None),
            deps=list(declaration.deps) if declaration else [],
            intent=declaration.intent if declaration else None,
        )
    for kind, attr in _TOPOLOGIES.items():
        for index, (topology, owner) in enumerate(zip(_topologies(shape, kind), owners[kind], strict=True)):
            entity = Entity(kind=kind, index=index, feature=owner, geometry=_geometry(topology, kind))
            entity._aad_topology = topology.wrapped
            getattr(result[owner or "unknown"], attr).append(entity)
    return list(result.values())


def _untracked_features(shape: Any) -> list[FeatureInfo]:
    # A separate/copy result has no proven connection to the named builder.
    # Plain build123d models, which have no declarations, retain implicit main.
    owner = None if _state.declarations else "main"
    return _make_feature_infos(
        shape, {kind: [owner] * len(_topologies(shape, kind)) for kind in _TOPOLOGIES}
    )


def _caller_line() -> int | None:
    frame = inspect.currentframe()
    try:
        caller = frame.f_back.f_back if frame and frame.f_back else None
        return caller.f_lineno if caller else None
    finally:
        del frame
