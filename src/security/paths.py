"""Filesystem containment checks for code-review targets."""
from __future__ import annotations

from pathlib import Path


def resolve_under_root(root: str | Path, candidate: str | Path) -> Path:
    """Return *candidate* only when its resolved path belongs to *root*."""
    resolved_root = Path(root).resolve(strict=True)
    requested = Path(candidate)
    if not requested.is_absolute():
        requested = resolved_root / requested
    resolved_candidate = requested.resolve(strict=True)
    try:
        resolved_candidate.relative_to(resolved_root)
    except ValueError as exc:
        raise ValueError(
            f"Path '{resolved_candidate}' is outside allowed root '{resolved_root}'."
        ) from exc
    return resolved_candidate
