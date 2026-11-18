from __future__ import annotations

import sys

from ralph.testing._audit_integration_no_legacy_sentinels import (
    _RETIRED_HYPHEN_STEM,
    _RETIRED_REVIEW_RULE,
    _RETIRED_STEM,
)
from ralph.testing.audit_integration_no_legacy import (
    _LEGACY_DEAD_OUTCOME_MODULE_NAMES,
    _OPERATOR_PARKING_LITERAL_RULE,
    _OPERATOR_PARKING_STATE_RULE,
    _RETIRED_THREE_SHA_MESSAGE,
    _RETIRED_THREE_SHA_RULE,
    LEGACY_OUTCOME_MODULE,
    audit_modules,
)


def selftest() -> int:
    clean_modules = {
        "ralph/pipeline/clean_sealed.py": (
            "from ralph.pipeline.resolution_outcome import StopResolutionOutcome, ResolvedOutcome\n"
            "def resolve_work() -> StopResolutionOutcome:\n"
            "    return ResolvedOutcome()\n"
        ),
        "ralph/pipeline/clean_review.py": (
            "phase = 'development_commit_review'\ndef commit_review(): pass\n"
        ),
    }
    clean_violations = audit_modules(clean_modules, ignore_transitional=True)
    if clean_violations:
        sys.stdout.write(
            f"SELFTEST FAILED: clean modules produced violations: {clean_violations}\n"
        )
        return 1

    planted_cases: list[tuple[str, str, str]] = [
        (
            "ralph/pipeline/planted_classifier.py",
            "def test(): return classify_conflict_attempt()\n",
            "legacy conflict-attempt classifier reappeared",
        ),
        (
            "ralph/pipeline/planted_failure_classifier.py",
            "def classify_agent_failure(): pass\n",
            "integration-side failure classification",
        ),
        (
            "ralph/pipeline/planted_private_resolver.py",
            "def test(): return _resolve_rebase_in_progress()\n",
            "private resolver caller outside entry",
        ),
        (
            "ralph/pipeline/planted_cooldown_write.py",
            "def test():\n    record.cooldown_until_ms = 5000\n",
            "local cooldown write outside shared ledger",
        ),
        (
            "ralph/pipeline/planted_cap.py",
            "total_resolution_cap_seconds = 300\n",
            "removed resolution cap identifier reappeared",
        ),
        (
            "ralph/pipeline/planted_escalate.py",
            "def test(): return escalate_gitlink_on_sight()\n",
            "legacy gitlink escalation hook reappeared",
        ),
        (
            "ralph/pipeline/planted_outcome_class.py",
            "from ralph.pipeline.conflict_resolution.resolution_outcome import ResolutionOutcome\n",
            "legacy resolution outcome vocabulary reappeared",
        ),
        (
            "ralph/pipeline/planted_tuple_ret.py",
            "StopResolutionOutcome = None\ndef resolve_outcome() -> tuple[bool, str]:\n    return (True, '')\n",
            "resolution outcome typed as (bool, str) tuple",
        ),
        (
            "ralph/pipeline/planted_unpack.py",
            "success, reason = attempt_stop_resolution()\n",
            "tuple-shaped resolution result unpack",
        ),
        (
            "ralph/pipeline/planted_subclass.py",
            "class RebaseResolutionResult(tuple):\n    pass\n",
            "resolution result retains legacy tuple vocabulary",
        ),
        (
            "ralph/pipeline/planted_tuple_protocol.py",
            ("class RebaseResolutionResult:\n    def __len__(self) -> int:\n        return 3\n"),
            "resolution result retains legacy tuple vocabulary",
        ),
        (
            "ralph/pipeline/planted_sealed_tuple_protocol.py",
            ("class ResolvedOutcome:\n    def __iter__(self):\n        return iter((True, ''))\n"),
            "sealed outcome retains legacy tuple vocabulary",
        ),
        (
            "ralph/pipeline/planted_waiting_tuple_len.py",
            ("class WaitingOutcome:\n    def __len__(self) -> int:\n        return 2\n"),
            "sealed outcome retains legacy tuple vocabulary",
        ),
        (
            "ralph/pipeline/planted_succeeded_property.py",
            (
                "class RebaseResolutionResult:\n"
                "    @property\n"
                "    def succeeded(self) -> bool:\n"
                "        return False\n"
            ),
            "resolution result retains legacy tuple vocabulary",
        ),
        (
            "ralph/pipeline/planted_exhausted_failure.py",
            "class ExhaustedResolutionFailure(str):\n    pass\n",
            "deleted legacy resolution symbol reappeared",
        ),
        (
            "ralph/pipeline/planted_controller_for_key.py",
            "def _controller_for_key(root):\n    return root\n",
            "deleted legacy resolution symbol reappeared",
        ),
        (
            "ralph/pipeline/planted_str_cause.py",
            "if str(failure_cause) == 'conflict':\n    pass\n",
            "string matching on failure cause",
        ),
        (
            LEGACY_OUTCOME_MODULE,
            "class ResolutionOutcome: pass\n",
            "legacy resolution outcome module reappeared",
        ),
        (
            f"ralph/pipeline/{next(iter(_LEGACY_DEAD_OUTCOME_MODULE_NAMES))}.py",
            "class Dummy: pass\n",
            "legacy resolution outcome module reappeared",
        ),
        (
            "ralph/pipeline/planted_non_exhaustive_match.py",
            "match outcome:\n    case ResolvedOutcome(): pass\n",
            "non-exhaustive outcome match",
        ),
        (
            "ralph/pipeline/planted_retry_timer.py",
            "def test(): return time_agent_retry()\n",
            "integration-side failure classification",
        ),
        (
            "ralph/pipeline/conflict_resolution/rebase_loop.py",
            "def test(): return _resolve_rebase_with_config()\n",
            "private resolver caller outside entry",
        ),
        (
            "ralph/pipeline/planted_cleanup.py",
            f"def test(): return {_RETIRED_STEM}()\n",
            _RETIRED_REVIEW_RULE,
        ),
        (
            "ralph/pipeline/planted_cleanup_phase.py",
            f"phase = 'development_{_RETIRED_STEM}'\n",
            _RETIRED_REVIEW_RULE,
        ),
        (
            "ralph/pipeline/planted_cleanup_skill.py",
            f"skill = 'submit-{_RETIRED_HYPHEN_STEM}-artifact'\n",
            _RETIRED_REVIEW_RULE,
        ),
        (
            "ralph/pipeline/planted_three_sha_message.py",
            f'msg = "{_RETIRED_THREE_SHA_MESSAGE}"\n',
            _RETIRED_THREE_SHA_RULE,
        ),
        (
            "ralph/pipeline/planted_three_sha_fstring.py",
            f'msg = f"{_RETIRED_THREE_SHA_MESSAGE}"\n',
            _RETIRED_THREE_SHA_RULE,
        ),
        (
            "ralph/pipeline/planted_needs_operator_literal.py",
            'state = "NeedsOperator"\n',
            _OPERATOR_PARKING_LITERAL_RULE,
        ),
        (
            "ralph/pipeline/planted_operator_action_required.py",
            'msg = "operator action required: land the commit"\n',
            _OPERATOR_PARKING_LITERAL_RULE,
        ),
        (
            "ralph/pipeline/planted_landed_proof_failed.py",
            'state = "LandedProofFailed"\n',
            _OPERATOR_PARKING_LITERAL_RULE,
        ),
        (
            "ralph/pipeline/planted_needs_operator_state.py",
            (
                "from enum import StrEnum\n"
                "class LadderState(StrEnum):\n"
                '    NEEDS_OPERATOR = "NeedsOperator"\n'
            ),
            _OPERATOR_PARKING_STATE_RULE,
        ),
    ]

    for rel, source, expected_rule in planted_cases:
        violations = audit_modules({rel: source}, ignore_transitional=True)
        rules = [v.rule for v in violations]
        if expected_rule not in rules:
            sys.stdout.write(
                f"SELFTEST FAILED: {rel} did not produce rule '{expected_rule}', got {rules}\n"
            )
            return 1

    sys.stdout.write(
        f"INTEGRATION NO-LEGACY AUDIT: SELFTEST OK ({len(clean_modules) + len(planted_cases)} cases verified)\n"
    )
    return 0
