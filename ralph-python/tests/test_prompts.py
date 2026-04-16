from __future__ import annotations

import re
from typing import cast

import pytest

from ralph.prompts.template_engine import TemplateRenderingError, render_template
from ralph.prompts.types import (
    Capability,
    CapabilitySet,
    PolicyFlag,
    PolicyFlagSet,
    capability_template_variables,
)


def test_render_template_replaces_variables() -> None:
    rendered = render_template(
        "Hello, {{ NAME }}!",
        {"NAME": "Ada"},
        {},
    )

    assert rendered == "Hello, Ada!"


def test_render_template_renders_partials_with_variables() -> None:
    rendered = render_template(
        "Intro {% include 'greeting.j2' %} Outro",
        {"NAME": "Ada"},
        {"greeting": "Hello, {{ NAME }}"},
    )

    assert rendered == "Intro Hello, Ada Outro"


def test_render_template_applies_default_filter_for_missing_variable() -> None:
    rendered = render_template(
        "Plan: {{ PLAN|default('(no plan available)') }}",
        {},
        {},
    )

    assert rendered == "Plan: (no plan available)"


def test_render_template_raises_for_missing_variable_without_default() -> None:
    with pytest.raises(TemplateRenderingError, match="NAME"):
        render_template("Hello, {{ NAME }}!", {}, {})


def test_render_template_raises_for_missing_partial() -> None:
    with pytest.raises(TemplateRenderingError, match=re.escape("missing.txt")):
        render_template("Before {% include 'missing.txt' %} After", {}, {})


def test_render_template_rejects_legacy_partial_shorthand() -> None:
    with pytest.raises(TemplateRenderingError):
        render_template("Before {{> missing}} After", {}, {})


def test_render_template_rejects_legacy_default_shorthand() -> None:
    with pytest.raises(TemplateRenderingError):
        render_template('Plan: {{ PLAN|default="fallback" }}', {}, {})


def test_capability_template_variables_expose_enabled_flags_and_tools() -> None:
    capabilities = CapabilitySet()
    capabilities.insert(cast("Capability", Capability.WORKSPACE_READ))
    capabilities.insert(cast("Capability", Capability.WORKSPACE_WRITE_TRACKED))
    capabilities.insert(cast("Capability", Capability.GIT_STATUS_READ))
    capabilities.insert(cast("Capability", Capability.GIT_DIFF_READ))
    capabilities.insert(cast("Capability", Capability.PROCESS_EXEC_BOUNDED))
    capabilities.insert(cast("Capability", Capability.ARTIFACT_SUBMIT))
    capabilities.insert(cast("Capability", Capability.RUN_REPORT_PROGRESS))

    policy_flags = PolicyFlagSet()
    policy_flags.insert(cast("PolicyFlag", PolicyFlag.ALLOW_SHELL))

    variables = capability_template_variables(capabilities, policy_flags)

    assert variables["HAS_WORKSPACE_WRITE"] == "true"
    assert variables["HAS_PROCESS_EXEC"] == "true"
    assert variables["HAS_GIT_WRITE"] == ""
    assert variables["POLICY_ALLOW_SHELL"] == "true"
    assert variables["POLICY_NO_EDIT"] == ""
    assert variables["WRITE_FILE_TOOL_NAME"] == "write_file"
    assert variables["EXEC_TOOL_NAME"] == "exec"
    assert variables["DECLARE_COMPLETE_TOOL_NAME"] == "declare_complete"
    assert variables["GIT_DIFF_TOOL_NAME"] == "git_diff"
    assert variables["MCP_TOOLS_LIST"] == (
        "read_file, list_directory, list_directory_recursive, search_files, "
        "git_status, git_log, git_show, git_diff, write_file, exec, "
        "ralph_submit_artifact, declare_complete, coordinate, "
        "ralph_submit_plan_section, ralph_finalize_plan, ralph_get_plan_draft, "
        "ralph_discard_plan_draft, report_progress"
    )


def test_capability_template_variables_leave_disabled_tools_empty() -> None:
    variables = capability_template_variables(CapabilitySet(), PolicyFlagSet())

    assert variables["HAS_MCP_WRITE"] == ""
    assert variables["HAS_MCP_EXEC"] == ""
    assert variables["HAS_MCP_GIT"] == ""
    assert variables["WRITE_FILE_TOOL_NAME"] == ""
    assert variables["EXEC_TOOL_NAME"] == ""
    assert variables["GIT_STATUS_TOOL_NAME"] == ""
    assert variables["MCP_TOOLS_LIST"] == ""
    assert variables["CAPABILITY_SUMMARY"] == "Capabilities:\n  (none)\n\nPolicy Flags:\n  (none)"
