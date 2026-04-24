from __future__ import annotations

from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
ROOT_MAKEFILE_PATH = PACKAGE_ROOT.parent / "Makefile"


def _target_body(name: str) -> list[str]:
    lines = ROOT_MAKEFILE_PATH.read_text(encoding="utf-8").splitlines()
    body: list[str] = []
    in_target = False

    for line in lines:
        if not in_target:
            if line.startswith(f"{name}:"):
                in_target = True
            continue

        if not line.startswith("\t"):
            break

        body.append(line.strip())

    if not body:
        raise AssertionError(f"target {name!r} not found")

    return body


def test_root_makefile_forwards_publish_and_twine_targets() -> None:
    assert _target_body("publish") == ["$(MAKE) -C $(PY_DIR) publish"]
    assert _target_body("twine-upload") == ["$(MAKE) -C $(PY_DIR) twine-upload"]
    assert _target_body("test-pypi") == ["$(MAKE) -C $(PY_DIR) test-pypi"]
    assert _target_body("twine-upload-testpypi") == [
        "$(MAKE) -C $(PY_DIR) twine-upload-testpypi"
    ]
