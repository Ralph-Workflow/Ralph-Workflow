"""Audit that the FIVE project-scope skill mirrors match the bundled content.

The product criteria (wt-013) makes every project-scope skill mirror a
contract surface: the validator mechanically checks the frontmatter
``status`` enum of development_result and development_analysis_decision
artifacts, and the agent-side SKILL.md mirrors teach agents that contract.
A future skill-content edit that updates the bundled
``ralph/skills/content/<name>.md`` but forgets to re-run the project-scope
skill installer leaves a divergent mirror and silently re-introduces the
removed structured contract (the wt-013 regression).

This audit pins the contract that every mirrored ``<root>/<name>/SKILL.md``
byte-equals ``ralph.skills.content.get_skill_content(name)`` for every
``name in BASELINE_SKILL_NAMES`` and every root in the FIVE canonical
project-scope skill-root prefixes. Without it, future refactors could
silently:

1. skip one of the FIVE roots when refreshing mirrors (a partial sync);
2. update the canonical ``.opencode/skills/<name>/SKILL.md`` but leave
   the four sibling-symlinked roots pointing at a stale target; or
3. update the bundled content but forget to re-run the installer, so the
   mirror content diverges from the source of truth.

Usage:
    python -m ralph.testing.audit_skill_mirror_freshness

Exit 0 = clean, 1 = at least one mirror diverges from the bundled content.
"""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path

from ralph.skills._agent_paths import _SKILL_ROOT_PREFIXES
from ralph.skills._content import BASELINE_SKILL_NAMES, get_skill_content

# The audit module lives at
# ``ralph-workflow/ralph/testing/audit_skill_mirror_freshness.py``.
# ``Path(__file__).resolve().parents[3]`` walks audit_skill_mirror_freshness
# -> testing -> ralph -> ralph-workflow -> repository root (the parent of
# the ``ralph-workflow`` package). The five project-scope skill roots live
# at ``<repo_root>/<prefix>`` where ``<prefix>`` is one of the strings in
# ``_SKILL_ROOT_PREFIXES`` (e.g. ``.opencode/skills/``).
_REPO_ROOT = Path(__file__).resolve().parents[3]


def _mirror_path(skill_root_prefix: str, skill_name: str) -> Path:
    """Resolve ``<repo_root>/<root_prefix><skill_name>`` for one mirror."""
    return _REPO_ROOT / skill_root_prefix.rstrip("/") / skill_name


def _check_mirror(skill_root_prefix: str, skill_name: str) -> list[str]:
    """Return zero or more mismatch diagnostics for one mirror."""
    mirror = _mirror_path(skill_root_prefix, skill_name)
    skill_file = mirror / "SKILL.md"
    if not mirror.is_dir():
        return [f"  {mirror}: mirror directory missing"]
    if not skill_file.is_file():
        return [f"  {skill_file}: SKILL.md missing"]
    expected = get_skill_content(skill_name)
    actual = skill_file.read_text(encoding="utf-8")
    if actual == expected:
        return []
    expected_sha = hashlib.sha256(expected.encode("utf-8")).hexdigest()
    actual_sha = hashlib.sha256(actual.encode("utf-8")).hexdigest()
    return [
        f"  {skill_file}: SKILL.md content diverges from bundled content "
        f"(expected sha256={expected_sha[:12]}, actual sha256={actual_sha[:12]})"
    ]


def _iter_mirror_targets() -> tuple[tuple[str, str], ...]:
    """Yield every (skill_root_prefix, skill_name) pair this audit checks.

    The iteration order is deterministic: roots in the canonical
    ``_SKILL_ROOT_PREFIXES`` insertion order (a frozenset so we sort for
    reproducibility), then ``BASELINE_SKILL_NAMES`` in the metadata.json
    order.
    """
    return tuple(
        (root, skill_name)
        for root in sorted(_SKILL_ROOT_PREFIXES)
        for skill_name in BASELINE_SKILL_NAMES
    )


def main(argv: list[str] | None = None) -> int:
    del argv
    problems: list[str] = []
    targets = _iter_mirror_targets()
    for skill_root_prefix, skill_name in targets:
        problems.extend(_check_mirror(skill_root_prefix, skill_name))

    if problems:
        print(
            f"SKILL-MIRROR-FRESHNESS AUDIT FAILED: {len(problems)} mirror(s) "
            f"diverge from the bundled skill content. Refresh the mirrors by "
            f"running the project-scope skill installer "
            f"(`ralph.skills._installer.install_project_baseline_skills(<repo_root>)`) "
            f"or by re-running `make skills-sync` if available."
        )
        for problem in problems:
            print(problem)
        return 1

    roots_checked = len(_SKILL_ROOT_PREFIXES)
    skills_checked = len(BASELINE_SKILL_NAMES)
    mirrors_checked = roots_checked * skills_checked
    print(
        f"audit_skill_mirror_freshness OK ({mirrors_checked} mirrors checked: "
        f"{roots_checked} roots x {skills_checked} skills). repo_root={_REPO_ROOT}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
