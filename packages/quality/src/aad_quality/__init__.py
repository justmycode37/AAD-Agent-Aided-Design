"""Quality interfaces are lazy so library lookup does not import a CAD kernel."""
from __future__ import annotations

def spec_checks(ctx):
    from .spec import spec_checks as evaluate
    return evaluate(ctx)

def subsystem_review(spec, checks=None, *, is_assembly=False):
    from .spec import subsystem_review as review
    return review(spec, checks, is_assembly=is_assembly)

def process_checks(ctx):
    from .process import process_checks as evaluate
    return evaluate(ctx)

def library(action: str, **kw):
    from .catalog import library as lookup
    return lookup(action, **kw)
