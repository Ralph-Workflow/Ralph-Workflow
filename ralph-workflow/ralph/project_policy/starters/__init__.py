
from __future__ import annotations

from importlib.resources import files
from types import MappingProxyType
from typing import TYPE_CHECKING, Final

from ralph.project_policy import markers

if TYPE_CHECKING:
    from collections.abc import Iterator

    from ralph.workspace.protocol import Workspace

PORTFOLIO_STARTER_NAME: Final[str] = "policy-portfolio.toml"
STARTER_NAMES: Final[tuple[str, ...]] = (
    "execution-kernel.md",
    "testing-policy.md",
    "typechecking-policy.md",
    "linting-policy.md",
    "dependency-policy.md",
    "verification-policy.md",
    "gate-script-policy.md",
    "agent-policy.md",
    "clean-code-policy.md",
    "documentation-policy.md",
    "security-policy.md",
    "architecture-policy.md",
    "design-system-policy.md",
    "ux-policy.md",
    "performance-policy.md",
    "memory-usage-policy.md",
    "accessibility-policy.md",
    "api-compatibility-policy.md",
    "data-storage-policy.md",
    "reliability-observability-policy.md",
    "privacy-policy.md",
    "release-deployment-policy.md",
)


def iter_starter_names() -> Iterator[str]:
    yield from STARTER_NAMES


_STARTER_CONTENT: Final[MappingProxyType[str, str]] = MappingProxyType(
    {
        name: (files(__package__) / name).read_text(encoding="utf-8")
        for name in (*STARTER_NAMES, PORTFOLIO_STARTER_NAME)
    }
)


def read_starter(name: str) -> str:
    try:
        return _STARTER_CONTENT[name]
    except KeyError as exc:
        msg = f"Unknown starter policy name: {name!r}"
        raise ValueError(msg) from exc


def seed_starter_into(workspace: Workspace, name: str) -> bool:
    if name not in (*STARTER_NAMES, PORTFOLIO_STARTER_NAME):
        msg = f"Unknown starter policy name: {name!r}"
        raise ValueError(msg)
    target_path = (
        markers.PORTFOLIO_PATH
        if name == PORTFOLIO_STARTER_NAME
        else f"{markers.CANONICAL_DIR}{name}"
    )
    if workspace.exists(target_path):
        return False
    workspace.mkdirs(markers.CANONICAL_DIR.rstrip("/"))
    workspace.write(target_path, read_starter(name))
    return True


__all__ = [
    "PORTFOLIO_STARTER_NAME",
    "STARTER_NAMES",
    "iter_starter_names",
    "read_starter",
    "seed_starter_into",
]
