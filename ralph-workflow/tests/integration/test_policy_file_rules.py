"""Repository-structure policy checks that scan the maintained source tree.

The rules, the scanners, and the grandfathered allowlists live in
:mod:`ralph.testing.audit_repo_structure`, which also runs as a ``make verify``
step. This test asserts on the same ``collect_violations()`` result so the
policy has exactly one source of truth.
"""

from __future__ import annotations

import pytest

from ralph.testing.audit_repo_structure import collect_violations

pytestmark = [pytest.mark.timeout_seconds(5), pytest.mark.subprocess_e2e]


def test_repo_structure_policies_hold() -> None:
    violations = collect_violations()

    assert not violations, "repo-structure violations:\n" + "\n".join(sorted(violations))
