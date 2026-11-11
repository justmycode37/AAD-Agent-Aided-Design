"""Public AAD modeling imports.

The build123d namespace is resolved lazily so importing ``aad.engine.types``
does not load the CAD kernel. ``from aad import *`` intentionally loads the
kernel because model scripts use its modeling classes and functions.
"""

from __future__ import annotations

from importlib import import_module
from types import ModuleType
from typing import Any

from ._runtime import Param, params
from .engine.select import SelectorError, entity_shape
from .engine.part_cache import cached_part
from .modeling import edge, feature, face, meta, part, vertex

_HELPER_EXPORTS = {
    "Param",
    "SelectorError",
    "edge",
    "entity_shape",
    "feature",
    "face",
    "gear_center_distance",
    "gear_mesh_phase",
    "gear_profile",
    "gear_solid",
    "helix_angle_for_center_distance",
    "meta",
    "params",
    "part",
    "tapered_bevel_standin",
    "vertex",
}
_GEAR_EXPORTS = {
    "gear_center_distance",
    "gear_mesh_phase",
    "gear_profile",
    "gear_solid",
    "helix_angle_for_center_distance",
    "tapered_bevel_standin",
}
_CAD_EXPORTS = {
    "Assembly",
    "CadOperationError",
    "SheetMetalPart",
    "assembly",
    "asset",
    "cached_part",
    "delete_faces",
    "exploded",
    "motion_clearance",
    "overlap_volume",
    "pattern_along",
    "press_pull",
    "sew_solid",
    "sheet_metal",
    "variable_fillet",
}
_HELPER_EXPORTS |= _CAD_EXPORTS
_BUILD123D_MODULE: ModuleType | None = None


def _build123d() -> ModuleType:
    global _BUILD123D_MODULE
    if _BUILD123D_MODULE is None:
        _BUILD123D_MODULE = import_module("build123d")
    return _BUILD123D_MODULE


def __getattr__(name: str) -> Any:
    if name == "__all__":
        # Keep wildcard modeling imports compatible with build123d while
        # deferring the expensive import until a model actually asks for it.
        return sorted({*_HELPER_EXPORTS, *(n for n in dir(_build123d()) if not n.startswith("_"))})
    if name in _HELPER_EXPORTS:
        if name in _GEAR_EXPORTS:
            value = getattr(import_module(".gears", __name__), name)
            globals()[name] = value
            return value
        if name in _CAD_EXPORTS:
            value = getattr(import_module(".cad", __name__), name)
            globals()[name] = value
            return value
        return globals()[name]
    try:
        return getattr(_build123d(), name)
    except AttributeError as exc:
        raise AttributeError(f"module 'aad' has no attribute {name!r}") from exc


def __dir__() -> list[str]:
    return sorted({*globals(), *_HELPER_EXPORTS, *(n for n in dir(_build123d()) if not n.startswith("_"))})
