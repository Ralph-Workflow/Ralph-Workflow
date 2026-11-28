from __future__ import annotations

from ralph.project_policy.guard_downgrades import downgraded_guards

_DIFF = """diff --git a/docs/ralph-workflow-policy/verification-policy.md b/docs/ralph-workflow-policy/verification-policy.md
--- a/docs/ralph-workflow-policy/verification-policy.md
+++ b/docs/ralph-workflow-policy/verification-policy.md
@@ -10,2 +10,2 @@
-| Run every gate | Gate-script owner | a check that cannot run blocks the run | deterministic: make verify | when retired |
+| Run every gate | Gate-script owner | a check that cannot run blocks the run | advisory: make verify is a full-gate suite; review: weekly | when retired |
-| Truthful evidence | Verification owner | every claim is backed | deterministic: make verify | when retired |
+| Truthful evidence | Verification owner | every claim is backed by a record | deterministic: make verify | when retired |
"""


def test_a_guard_moved_from_deterministic_to_advisory_is_reported() -> None:
    assert downgraded_guards(_DIFF) == (
        "docs/ralph-workflow-policy/verification-policy.md: Run every gate: "
        "deterministic enforcement replaced by advisory",
    )


def test_a_reworded_guard_that_stays_deterministic_is_not_reported() -> None:
    reworded_only = "\n".join(line for line in _DIFF.splitlines() if "Run every gate" not in line)

    assert downgraded_guards(reworded_only) == ()
