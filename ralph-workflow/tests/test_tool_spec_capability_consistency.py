"""Anti-drift guard: each tool's advertised/gated capability must match the
capability its handler actually enforces.

The dispatch boundary gates a call on ``spec.required_capability``
(``_tool_bridge._is_tool_allowed``); the handler then independently gates on its
own ``require_capability(...)`` constant. If those two name DIFFERENT
capabilities, a session granted exactly what the spec advertises passes the gate
and is then rejected inside the handler (a silent capability drift that two
distinct gates hid because the default profile happened to grant both). This test
pins, via the real ``session_has_capability`` resolver, that a session holding
ONLY the spec-advertised capability satisfies the handler's gate.
"""

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
    EXEC_TOOL,
    GIT_DIFF_TOOL,
    GIT_LOG_TOOL,
    GIT_SHOW_TOOL,
    GIT_STATUS_TOOL,
    RAW_EXEC_TOOL,
    READ_ENV_TOOL,
    REPORT_PROGRESS_TOOL,
    SUBMIT_ARTIFACT_TOOL,
    SUBMIT_MD_ARTIFACT_TOOL,
    UNSAFE_EXEC_TOOL,
    VERIFY_MD_ARTIFACT_TOOL,
)

# Each tool -> the capability string its HANDLER passes to require_capability.
# (Read straight from the handler modules; this is the authority the spec must
# agree with.)
_HANDLER_CAPABILITY: dict[object, str] = {
    GIT_STATUS_TOOL: "GitStatusRead",
    GIT_DIFF_TOOL: "GitDiffRead",
    GIT_LOG_TOOL: "GitStatusRead",
    GIT_SHOW_TOOL: "GitStatusRead",
    EXEC_TOOL: "ProcessExecBounded",
    UNSAFE_EXEC_TOOL: "ProcessExecUnbounded",
    RAW_EXEC_TOOL: "ProcessExecUnbounded",
    SUBMIT_ARTIFACT_TOOL: "artifact.submit",
    SUBMIT_MD_ARTIFACT_TOOL: "artifact.submit",
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
    # Drains are granted the dotted "git.diff_read" (base set), but the git_diff
    # spec+handler gate on "GitDiffRead". Without the McpCapability.GIT_DIFF_READ
    # mapping these never match and git_diff is silently un-callable. Pin that the
    # real granted form satisfies the advertised + enforced capability.
    specs = _specs_by_name()
    git_diff_spec_cap = specs[GIT_DIFF_TOOL]
    assert session_has_capability({"git.diff_read"}, git_diff_spec_cap)
    assert session_has_capability({"git.diff_read"}, "GitDiffRead")


def test_every_handler_capability_is_covered() -> None:
    # If a new gated tool is added to these spec groups, force its handler
    # capability to be registered here so the consistency check cannot be skipped.
    specs = _specs_by_name()
    gated = {
        name
        for name, cap in specs.items()
        # Plan-draft tools (artifact.plan_*) are intentionally covered elsewhere;
        # this guard tracks the exec/git/coordination capability surface.
        if not str(cap).startswith("artifact.plan")
    }
    missing = gated - set(_HANDLER_CAPABILITY)
    assert not missing, f"gated tools missing a handler-capability assertion: {missing}"


def _all_granted_capabilities() -> set[str]:
    """Union of the default capabilities granted across every drain."""
    granted: set[str] = set()
    for drain in SessionDrain:
        granted |= set(default_prompt_capability_identifiers(drain))
    return granted


def test_every_registered_tool_capability_is_satisfiable_by_some_drain() -> None:
    """Full-coverage anti-drift guard over EVERY registered tool: each spec's
    ``required_capability`` must be satisfiable by at least one drain's granted
    capability set.

    This catches the orphaned-capability class generally — e.g. the "GitDiffRead"
    string that normalized to a token no granted capability matched, making
    git_diff silently un-callable for every drain. A typo or an enum/alias gap in
    ANY tool's required_capability fails here.
    """
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
