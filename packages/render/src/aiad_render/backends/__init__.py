"""Lazy render backend registry. Importing this module never imports VTK."""

from __future__ import annotations

from collections.abc import Callable
from threading import RLock

from .protocol import CameraSpec, RenderBackend, RenderFace, RenderLine, RenderScene

__all__ = [
    "CameraSpec",
    "RenderBackend",
    "RenderFace",
    "RenderLine",
    "RenderScene",
    "get_backend",
    "register_backend",
]

_LOCK = RLock()
_FACTORIES: dict[str, Callable[[], RenderBackend]] = {}
_INSTANCES: dict[str, RenderBackend] = {}


def register_backend(
    name: str,
    factory: Callable[[], RenderBackend],
    *,
    replace: bool = False,
) -> None:
    """Register a backend factory, allowing application-specific renderers."""
    normalized = name.strip().lower()
    if not normalized:
        raise ValueError("backend name must not be empty")
    with _LOCK:
        if normalized in _FACTORIES and not replace:
            raise ValueError(f"render backend {normalized!r} is already registered")
        _FACTORIES[normalized] = factory
        _INSTANCES.pop(normalized, None)


def get_backend(name: str = "vtk") -> RenderBackend:
    """Return a cached backend instance, importing VTK only on first use."""
    normalized = name.strip().lower()
    with _LOCK:
        if normalized in _INSTANCES:
            return _INSTANCES[normalized]
        factory = _FACTORIES.get(normalized)
        if factory is None and normalized == "vtk":
            from .vtk_backend import VtkBackend

            factory = VtkBackend
            _FACTORIES[normalized] = factory
        if factory is None:
            raise ValueError(f"unknown render backend {normalized!r}")
        instance = factory()
        _INSTANCES[normalized] = instance
        return instance
