from __future__ import annotations

from ralph.config.mcp_loader import load_mcp_config
from ralph.mcp.protocol._session_drain import SessionDrain
from ralph.mcp.protocol.session import session_has_capability
from ralph.mcp.session_plan import default_prompt_capability_identifiers
from ralph.mcp.tools.bridge._registry import tool_specs
from ralph.mcp.tools.bridge._specs_artifacts import artifact_specs
from ralph.mcp.tools.bridge._specs_git_exec import git_exec_specs
from ralph.mcp.tools.names import (
    COORDINATE_TOOL,
    DECLARE_COMPLETE_TOOL,
    DISCARD_MD_DRAFT_TOOL,
    EDIT_MD_ARTIFACT_TOOL,
    EXEC_TOOL,
    FINALIZE_MD_ARTIFACT_TOOL,
    GET_MD_DRAFT_TOOL,
    GIT_DIFF_TOOL,
    GIT_LOG_TOOL,
    GIT_SHOW_TOOL,
    GIT_STATUS_TOOL,
    RAW_EXEC_TOOL,
    READ_ENV_TOOL,
    REPORT_PROGRESS_TOOL,
    STAGE_MD_ARTIFACT_TOOL,
    SUBMIT_MD_ARTIFACT_TOOL,
    UNSAFE_EXEC_TOOL,
    VERIFY_MD_ARTIFACT_TOOL,
)

_HANDLER_CAPABILITY: dict[object, str] = {
    GIT_STATUS_TOOL: "GitStatusRead",
    GIT_DIFF_TOOL: "GitDiffRead",
    GIT_LOG_TOOL: "GitStatusRead",
    GIT_SHOW_TOOL: "GitStatusRead",
    EXEC_TOOL: "ProcessExecBounded",
    UNSAFE_EXEC_TOOL: "ProcessExecUnbounded",
    RAW_EXEC_TOOL: "ProcessExecUnbounded",
    SUBMIT_MD_ARTIFACT_TOOL: "artifact.submit",
    STAGE_MD_ARTIFACT_TOOL: "artifact.submit",
    EDIT_MD_ARTIFACT_TOOL: "artifact.submit",
    GET_MD_DRAFT_TOOL: "artifact.plan_read",
    DISCARD_MD_DRAFT_TOOL: "artifact.submit",
    FINALIZE_MD_ARTIFACT_TOOL: "artifact.submit",
    VERIFY_MD_ARTIFACT_TOOL: "artifact.plan_read",
    REPORT_PROGRESS_TOOL: "run.report_progress",
    DECLARE_COMPLETE_TOOL: "artifact.submit",
    COORDINATE_TOOL: "artifact.plan_write",
    READ_ENV_TOOL: "env.read",
}


def _specs_by_name() -> dict[object, str]:
    out: dict[object, str] = {}
    for spec in (*git_exec_specs(), *artifact_specs()):
        out[spec.metadata.definition.name] = spec.metadata.required_capability
    return out


def test_spec_capability_satisfies_handler_capability() -> None:
    specs = _specs_by_name()
    for tool, handler_cap in _HANDLER_CAPABILITY.items():
        assert tool in specs, f"spec for {tool!r} not found"
        spec_cap = specs[tool]
        assert session_has_capability({spec_cap}, handler_cap), (
            f"{tool!r}: spec advertises {spec_cap!r} but handler enforces "
            f"{handler_cap!r}; a session granted only the advertised capability "
            f"would pass the dispatch gate then be denied by the handler."
        )


def test_real_granted_capability_form_satisfies_git_diff_gate() -> None:
    specs = _specs_by_name()
    git_diff_spec_cap = specs[GIT_DIFF_TOOL]
    assert session_has_capability({"git.diff_read"}, git_diff_spec_cap)
    assert session_has_capability({"git.diff_read"}, "GitDiffRead")


def test_every_handler_capability_is_covered() -> None:
    specs = _specs_by_name()
    gated = {name for name, cap in specs.items() if cap}
    missing = gated - set(_HANDLER_CAPABILITY)
    assert not missing, f"gated tools missing a handler-capability assertion: {missing}"


def _all_granted_capabilities() -> set[str]:
    granted: set[str] = set()
    for drain in SessionDrain:
        granted |= set(default_prompt_capability_identifiers(drain))
    return granted


def test_every_registered_tool_capability_is_satisfiable_by_some_drain() -> None:
    granted = _all_granted_capabilities()
    specs = tool_specs(load_mcp_config(config_path=None))
    unsatisfiable = [
        (str(spec.metadata.definition.name), spec.metadata.required_capability)
        for spec in specs
        if not session_has_capability(granted, spec.metadata.required_capability)
    ]
    assert not unsatisfiable, (
        "These tools advertise a required_capability that NO drain can satisfy "
        f"(un-callable / orphaned capability): {unsatisfiable}"
    )
