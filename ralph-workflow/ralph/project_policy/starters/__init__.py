"""Bundled starter policy loader.

Mirrors the resource-access pattern used by ``ralph.skills._content``: the
starter ``.md`` files are bundled inside the installed package and read via
``importlib.resources.files(__package__)``. The hatch wheel and sdist
include entries (added to :file:`pyproject.toml`) ship the same files
with the wheel so the starter loader works in any environment that has
the wheel installed.

Public API:

* :func:`iter_starter_names` — return every bundled starter name (20
  entries: 10 core + 10 conditional).
* :func:`read_starter` — return the content of one bundled starter.
* :func:`seed_starter_into` — copy a bundled starter into the canonical
  policy directory via the workspace seam, but ONLY when the target is
  absent (never overwrite an existing project-customized file).

Every starter ships with a template banner, REPLACE-ME guidance comments,
and placeholder tokens, so a freshly-seeded starter fails the validator
until the remediation agent resolves every one of them. Completion is the
absence of unresolved markers — there is no completion marker to add.
"""

from __future__ import annotations

from importlib.resources import files
from typing import TYPE_CHECKING

from ralph.project_policy import markers

if TYPE_CHECKING:
    from collections.abc import Iterator

    from ralph.workspace.protocol import Workspace

#: Set of starter filenames bundled with the package. The hatch wheel and
#: sdist include these files via the ``ralph/project_policy/starters/**/*.md``
#: glob in ``pyproject.toml``.
STARTER_NAMES: tuple[str, ...] = (
    "testing-policy.md",
    "typechecking-policy.md",
    "linting-policy.md",
    "dependency-policy.md",
    "verification-policy.md",
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
    """Yield every bundled starter name (10 core + 10 conditional)."""
    yield from STARTER_NAMES


def read_starter(name: str) -> str:
    """Return the content of one bundled starter.

    Args:
        name: Filename of the starter (e.g. ``testing-policy.md``).

    Raises:
        ValueError: When ``name`` is not a bundled starter.
        FileNotFoundError: When the bundled file is missing (packaging bug).
    """
    if name not in STARTER_NAMES:
        msg = f"Unknown starter policy name: {name!r}"
        raise ValueError(msg)
    package_files = files(__package__)
    return (package_files / name).read_text(encoding="utf-8")


def seed_starter_into(workspace: Workspace, name: str) -> bool:
    """Write ``name`` into the canonical directory via the workspace seam.

    Returns True when the file was created, False when it already existed
    (never overwrites). Starter content is shipped without the completion
    marker; seeding never makes a file valid.

    Args:
        workspace: The injected workspace seam.
        name: Filename of the starter to seed.

    Raises:
        ValueError: When ``name`` is not a bundled starter.
    """
    if name not in STARTER_NAMES:
        msg = f"Unknown starter policy name: {name!r}"
        raise ValueError(msg)
    target_path = f"{markers.CANONICAL_DIR}{name}"
    if workspace.exists(target_path):
        return False
    workspace.mkdirs(markers.CANONICAL_DIR.rstrip("/"))
    workspace.write(target_path, read_starter(name))
    return True


__all__ = [
    "STARTER_NAMES",
    "iter_starter_names",
    "read_starter",
    "seed_starter_into",
]
