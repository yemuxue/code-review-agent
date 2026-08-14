from pathlib import Path

import pytest


def test_resolve_under_root_accepts_descendant(tmp_path: Path):
    from src.security.paths import resolve_under_root

    root = tmp_path / "allowed"
    child = root / "repo"
    child.mkdir(parents=True)

    assert resolve_under_root(root, child) == child.resolve()
    assert resolve_under_root(root, "repo") == child.resolve()


def test_resolve_under_root_rejects_escape(tmp_path: Path):
    from src.security.paths import resolve_under_root

    root = tmp_path / "allowed"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()

    with pytest.raises(ValueError, match="outside"):
        resolve_under_root(root, outside)
