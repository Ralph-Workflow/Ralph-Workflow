"""The policy-remediation ANALYSIS phase: review what remediation wrote.

The deterministic validator (:mod:`ralph.project_policy.validators`) can only
check FORM -- headings present, no placeholder tokens, the first token of each
``RALPH-COMMAND`` on the approved-tool allowlist. It cannot tell whether a
``RALPH-FACT`` is TRUE, whether ``make verify`` is a target that actually exists,
or whether a gate script calls a tool nobody installed. A project can reach a
structurally perfect policy that is factually a lie.

This phase closes that gap with an agent. It is deliberately weak: its drain class
(``analysis``) grants ``artifact.submit`` and ``process.exec_bounded`` but NO
workspace-write MCP tool, so it can probe the declared gates and report a decision,
and it has no sanctioned way to edit the work it is reviewing.

That is a strong hint, not a proof. ``process.exec_bounded`` runs compound shell
commands, and a shell can redirect -- so "cannot write" is not enforceable at the
tool surface. The property that actually holds is enforced elsewhere: the driver
re-runs the deterministic validator before caching READY
(:func:`ralph.project_policy.pipeline_driver._finish`), so an analysis agent cannot
approve its way past a failing validator no matter what it touches.

Decision handoff
----------------

:func:`ralph.pipeline.effect_executor.execute_agent_effect` returns a bare
``PipelineEvent`` enum with no payload, so the decision cannot ride back on the
return value. It is read from the artifact JSON the agent submits through MCP --
exactly how :mod:`ralph.phases.analysis` reads the in-graph analysis decisions.

The stale-artifact rule is the sharpest edge in the whole pipeline: the decision
file is DELETED before every invocation, so a ``completed`` left behind by a
previous iteration can never be re-read as this iteration's decision. Without
that delete, an analysis agent that crashed on iteration 2 would silently inherit
iteration 1's approval.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Protocol, cast

from ralph.phases.artifacts import (
    PhaseArtifactError,
    load_phase_artifact,
    unwrap_phase_artifact_content,
)
from ralph.project_policy import markers
from ralph.project_policy.analysis_decision import AnalysisDecision
from ralph.project_policy.pipeline_graph import (
    DECISION_FAILED,
    PHASE_ANALYSIS,
    phase_definition,
)
from ralph.prompts.template_engine import render_template
from ralph.prompts.template_registry import (
    load_partial_templates,
    packaged_template_root,
)

if TYPE_CHECKING:
    from ralph.workspace.protocol import Workspace

#: Packaged Jinja template holding the analysis prompt body.
PROMPT_TEMPLATE_NAME: str = "policy_remediation_analysis.jinja"

#: Where the analysis agent's task definition is materialized. Symmetric with the
#: remediation prompt; see :mod:`ralph.project_policy.remediation`.
ANALYSIS_PROMPT_REL_PATH: str = ".agent/tmp/policy_remediation_analysis_prompt.md"

#: The artifact the analysis agent submits, as declared in
#: ``ralph/policy/defaults/artifacts.toml``.
ANALYSIS_ARTIFACT_TYPE: str = "policy_remediation_analysis_decision"
ANALYSIS_ARTIFACT_REL_PATH: str = f".agent/artifacts/{ANALYSIS_ARTIFACT_TYPE}.md"

EmitFn = Callable[[str], None]


class InvokePolicyAgent(Protocol):
    """Callable contract for invoking one phase's agent chain.

    The production implementation (``ralph.project_policy.cli_integration``)
    builds an ``InvokeAgentEffect`` for the phase's drain and runs it through
    :func:`ralph.pipeline.effect_executor.execute_agent_effect`. Returns True
    when the agent reported success.
    """

    def __call__(self, *, phase: str, prompt_path: str) -> bool: ...


def _noop_emit(message: str) -> None:
    """Default emit callback used when no display is injected."""


def _string_list(value: object) -> list[str]:
    """Coerce an artifact field to a list of non-empty strings.

    The artifact is agent-supplied markdown, so every field is untrusted: the value
    may be absent, a scalar, or a list of anything at all. Anything that is not a
    list of non-empty strings degrades to an empty list rather than raising -- a
    malformed feedback field must not crash the policy pipeline.
    """
    if not isinstance(value, list):
        return []
    items = cast("list[object]", value)
    return [text for item in items if (text := str(item).strip())]


def _render_prompt(workspace: Workspace) -> str:
    """Build the analysis prompt text.

    The agent is handed the canonical directory and the gate-script policy path
    rather than the policy bodies themselves: it has ``workspace.read``, and
    making it go and look is the point -- an agent told what a file says will
    happily confirm it.
    """
    variables = {
        "canonical_dir": markers.CANONICAL_DIR,
        "gate_script_policy_path": f"{markers.CANONICAL_DIR}gate-script-policy.md",
        "artifact_type": ANALYSIS_ARTIFACT_TYPE,
        "policy_files": ", ".join(markers.CORE_POLICY_FILES),
    }
    del workspace
    partials = load_partial_templates((packaged_template_root(),))
    template = (packaged_template_root() / PROMPT_TEMPLATE_NAME).read_text(
        encoding="utf-8"
    )
    return render_template(template, variables, partials)


def _write_prompt(workspace: Workspace, prompt_text: str) -> str:
    """Materialize the analysis prompt under .agent/tmp via the workspace seam."""
    parent_dir = "/".join(ANALYSIS_PROMPT_REL_PATH.split("/")[:-1])
    if parent_dir:
        workspace.mkdirs(parent_dir)
    workspace.write(ANALYSIS_PROMPT_REL_PATH, prompt_text)
    return ANALYSIS_PROMPT_REL_PATH


def clear_stale_decision(workspace: Workspace) -> None:
    """Delete any decision artifact left behind by a previous iteration.

    MUST run before every analysis invocation. Without it, an agent that fails to
    submit leaves the previous iteration's artifact in place, and the driver
    would read that stale verdict as the current one -- turning a crashed review
    into an inherited approval.
    """
    if workspace.exists(ANALYSIS_ARTIFACT_REL_PATH):
        workspace.delete(ANALYSIS_ARTIFACT_REL_PATH)


def read_analysis_decision(workspace: Workspace) -> AnalysisDecision:
    """Read the analysis decision back from the markdown artifact.

    Fails closed in every degenerate case -- artifact absent, unreadable,
    malformed, wrong type, or carrying a status outside the decision vocabulary
    -- by returning ``failed``, which routes back to remediation and consumes one
    iteration of the loop budget. The one status this function will never invent
    is ``completed``.
    """
    if not workspace.exists(ANALYSIS_ARTIFACT_REL_PATH):
        return AnalysisDecision(
            status=DECISION_FAILED,
            summary="analysis agent submitted no decision artifact",
        )
    try:
        artifact = load_phase_artifact(workspace, ANALYSIS_ARTIFACT_REL_PATH)
        content = unwrap_phase_artifact_content(
            artifact, expected_type=ANALYSIS_ARTIFACT_TYPE
        )
    except PhaseArtifactError as exc:
        return AnalysisDecision(
            status=DECISION_FAILED,
            summary=f"analysis decision artifact is unusable: {exc}",
        )

    status = str(content.get("status", "")).strip().lower()
    decisions = _analysis_vocabulary()
    if status not in decisions:
        return AnalysisDecision(
            status=DECISION_FAILED,
            summary=(
                f"analysis agent returned an unrecognized decision {status!r}; "
                "treating it as failed"
            ),
        )
    return AnalysisDecision(
        status=status,
        summary=str(content.get("summary", "")).strip(),
        what_came_up_short=_string_list(content.get("what_came_up_short")),
        how_to_fix=_string_list(content.get("how_to_fix")),
    )


def _analysis_vocabulary() -> frozenset[str]:
    """Return the legal decision strings, straight from the routing table.

    Derived from the graph rather than restated, so a decision the router cannot
    route can never be accepted here.
    """
    return frozenset(phase_definition(PHASE_ANALYSIS).decisions)


def run_analysis_phase(
    workspace: Workspace,
    *,
    invoke_agent: InvokePolicyAgent,
    emit: EmitFn = _noop_emit,
) -> AnalysisDecision:
    """Run one analysis phase and return its decision.

    Clears any stale decision, materializes the prompt, invokes the agent chain,
    and reads the decision back from the artifact. An agent that reports failure,
    or that reports success without submitting a usable artifact, yields
    ``failed`` -- which routes back to remediation rather than forward to done.
    """
    clear_stale_decision(workspace)
    prompt_path = _write_prompt(workspace, _render_prompt(workspace))

    success = bool(invoke_agent(phase=PHASE_ANALYSIS, prompt_path=prompt_path))
    decision = read_analysis_decision(workspace)
    if not success and decision.is_completed():
        # The agent chain failed, yet a 'completed' artifact is on disk. Trust the
        # chain, not the artifact: a partially-written or pre-existing approval
        # must never launder a failed review into a pass.
        return AnalysisDecision(
            status=DECISION_FAILED,
            summary=(
                "analysis agent chain failed after submitting a completed "
                "decision; discarding the decision"
            ),
        )
    emit(f"project-policy-readiness: analysis decision: {decision.status}")
    return decision


__all__ = [
    "ANALYSIS_ARTIFACT_REL_PATH",
    "ANALYSIS_ARTIFACT_TYPE",
    "ANALYSIS_PROMPT_REL_PATH",
    "AnalysisDecision",
    "InvokePolicyAgent",
    "clear_stale_decision",
    "read_analysis_decision",
    "run_analysis_phase",
]
