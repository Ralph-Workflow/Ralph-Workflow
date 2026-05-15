from __future__ import annotations

import tomllib
from pathlib import Path


def test_package_declares_stable_release_classifier() -> None:
    pyproject_path = Path(__file__).resolve().parents[1] / "pyproject.toml"
    pyproject = tomllib.loads(pyproject_path.read_text(encoding="utf-8"))

    classifiers = pyproject["project"]["classifiers"]

    assert "Development Status :: 5 - Production/Stable" in classifiers
    assert "Development Status :: 4 - Beta" not in classifiers
