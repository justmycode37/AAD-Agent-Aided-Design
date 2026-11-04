"""Hidden corpus loader, public projection, and host-only reference controls."""

from .loader import (
    CorpusValidationError,
    load_corpus,
    load_public_projection,
    public_projection,
    validate_corpus,
)

__all__ = [
    "CorpusValidationError",
    "load_corpus",
    "load_public_projection",
    "public_projection",
    "validate_corpus",
]
