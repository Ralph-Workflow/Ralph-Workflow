from __future__ import annotations

from ralph.language_detector.models import ProjectStack
from ralph.project_policy import markers, remediation, starters
from ralph.project_policy.models import PolicyFinding
from ralph.workspace.memory import MemoryWorkspace
from tests.project_policy.policy_corpus import seed_complete_corpus

_TESTING = f"{markers.CANONICAL_DIR}testing-policy.md"
_FROZEN = "<!-- ralph-policy-schema: freeze v6 -->"


def _upgrade_finding() -> PolicyFinding:
    return PolicyFinding(
        requirement_id="RWP-MARKER:schema-testing-policy.md",
        path=_TESTING,
        missing_evidence=f"missing schema marker {markers.POLICY_SCHEMA_MARKER}",
        required_outcome="upgrade the file to the current schema",
    )


def _freezing_agent(ws: MemoryWorkspace):
    def invoke(*, phase: str, prompt_path: str, progress_check: object = None) -> bool:
        del phase, prompt_path, progress_check
        body = ws.read(_TESTING).split("\n", 1)[1]
        ws.write(_TESTING, f"{_FROZEN}\n{body}")
        return True

    return invoke


def test_a_remediation_agent_cannot_freeze_a_policy_file() -> None:
    ws = MemoryWorkspace()
    seed_complete_corpus(ws)
    before = ws.read(_TESTING).splitlines()[0]

    remediation.run_remediation_phase(
        ws,
        ProjectStack(primary_language="Python"),
        [_upgrade_finding()],
        invoke_agent=_freezing_agent(ws),
    )

    assert ws.read(_TESTING).splitlines()[0] == before


def test_an_operator_freeze_survives_remediation() -> None:
    ws = MemoryWorkspace()
    seed_complete_corpus(ws)
    body = ws.read(_TESTING).split("\n", 1)[1]
    ws.write(_TESTING, f"{_FROZEN}\n{body}")

    remediation.run_remediation_phase(
        ws,
        ProjectStack(primary_language="Python"),
        [_upgrade_finding()],
        invoke_agent=_freezing_agent(ws),
    )

    assert ws.read(_TESTING).splitlines()[0] == _FROZEN


def test_the_remediation_prompt_forbids_freezing() -> None:
    prompt = remediation.render_remediation_prompt([_upgrade_finding()])
    assert "Never write a `freeze` schema marker" in prompt


def test_the_starters_say_the_cap_switch_is_not_ratchet_growth() -> None:
    for name in ("verification-policy.md", "testing-policy.md"):
        text = " ".join(starters.read_starter(name).split())
        assert "is not budget growth" in text, name
