from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from ralph.config.enums import AgentTransport
from ralph.mcp.session_plan import build_session_mcp_plan
from ralph.policy.models import AgentChainConfig, AgentDrainConfig, AgentsPolicy

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture()
def isolated_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    return home


_AGENTS_POLICY = AgentsPolicy(
    agent_chains={
        "development": AgentChainConfig(agents=["claude"], max_retries=3, retry_delay_ms=1000),
        "policy_remediation": AgentChainConfig(
            agents=["claude"], max_retries=2, retry_delay_ms=1000
        ),
        "policy_remediation_analysis": AgentChainConfig(
            agents=["claude"], max_retries=2, retry_delay_ms=1000
        ),
    },
    agent_drains={
        "development": AgentDrainConfig(chain="development", drain_class="development"),
        "policy_remediation": AgentDrainConfig(
            chain="policy_remediation", drain_class="development"
        ),
        "policy_remediation_analysis": AgentDrainConfig(
            chain="policy_remediation_analysis", drain_class="analysis"
        ),
    },
)


def test_policy_remediation_drain_is_denied_the_artifact_and_plan_surface(
    isolated_home: Path,
    tmp_path: Path,
) -> None:
    del isolated_home

    plan = build_session_mcp_plan(
        transport=AgentTransport.CLAUDE,
        drain="policy_remediation",
        workspace_path=tmp_path,
        agents_policy=_AGENTS_POLICY,
    )

    assert "artifact.submit" not in plan.capabilities
    assert "artifact.plan_read" not in plan.capabilities
    assert "artifact.plan_write" not in plan.capabilities


def test_policy_remediation_drain_keeps_the_documentation_write_surface(
    isolated_home: Path,
    tmp_path: Path,
) -> None:
    del isolated_home

    plan = build_session_mcp_plan(
        transport=AgentTransport.CLAUDE,
        drain="policy_remediation",
        workspace_path=tmp_path,
        agents_policy=_AGENTS_POLICY,
    )

    assert "workspace.read" in plan.capabilities
    assert "workspace.write_tracked" in plan.capabilities
    assert "workspace.edit" in plan.capabilities
    assert "process.exec_bounded" in plan.capabilities
    assert "git.status_read" in plan.capabilities


def test_ordinary_development_drain_still_gets_the_artifact_surface(
    isolated_home: Path,
    tmp_path: Path,
) -> None:
    del isolated_home

    plan = build_session_mcp_plan(
        transport=AgentTransport.CLAUDE,
        drain="development",
        workspace_path=tmp_path,
        agents_policy=_AGENTS_POLICY,
    )

    assert "artifact.submit" in plan.capabilities
    assert "artifact.plan_read" in plan.capabilities


def test_policy_analysis_can_submit_and_verify_its_decision(
    isolated_home: Path,
    tmp_path: Path,
) -> None:
    del isolated_home

    plan = build_session_mcp_plan(
        transport=AgentTransport.CLAUDE,
        drain="policy_remediation_analysis",
        workspace_path=tmp_path,
        agents_policy=_AGENTS_POLICY,
    )

    assert "artifact.submit" in plan.capabilities
    assert "artifact.plan_read" in plan.capabilities
