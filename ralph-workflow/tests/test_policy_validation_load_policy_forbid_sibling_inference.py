"""Unit tests for policy validation.

Tests cover:
- Valid agents.toml loading
- Missing drain binding raises PolicyValidationError
- Chain referencing unknown agent raises PolicyValidationError
- Empty chain list raises PolicyValidationError
- All six built-in drains are bound in default agents.toml
"""

from __future__ import annotations

import importlib
from textwrap import dedent
from typing import TYPE_CHECKING

import pytest

from ralph.policy.loader import PolicyValidationError as LoaderPolicyValidationError
from ralph.policy.loader import load_policy

if TYPE_CHECKING:
    from pathlib import Path

ValidationError = importlib.import_module("pydantic").ValidationError
DEFAULT_MAX_WORK_UNITS = 50


class TestLoadPolicyForbidSiblingInference:
    """Tests that load_policy enforces forbid_sibling_drain_inference."""

    def test_load_policy_rejects_missing_drains(self, tmp_path: Path) -> None:
        """load_policy rejects a pipeline where a used drain is not bound in agents.toml.

        When forbid_sibling_drain_inference=True, pipeline-used drains must be explicitly
        bound. A pipeline with a development_analysis phase but no development_analysis
        drain binding is rejected at load time.
        """
        config_dir = tmp_path / ".agent"
        config_dir.mkdir(parents=True)

        agents_toml = dedent(
            """
            forbid_sibling_drain_inference = true

            [agent_chains.planning]
            agents = ["claude"]

            [agent_drains.planning]
            chain = "planning"
            # development_analysis drain intentionally absent
            """
        )
        (config_dir / "agents.toml").write_text(agents_toml)

        pipeline_toml = dedent(
            """
            [phases.planning]
            drain = "planning"
            role = "execution"
            [phases.planning.transitions]
            on_success = "development_analysis"

            [phases.development_analysis]
            drain = "development_analysis"
            role = "execution"
            [phases.development_analysis.transitions]
            on_success = "complete"

            [phases.complete]
            drain = "planning"
            role = "terminal"
            terminal_outcome = "success"
            [phases.complete.transitions]
            on_success = "complete"
            on_loopback = "complete"

            entry_phase = "planning"
            terminal_phase = "complete"
            """
        )
        (config_dir / "pipeline.toml").write_text(pipeline_toml)

        with pytest.raises(
            LoaderPolicyValidationError,
            match="unbound drains",
        ):
            load_policy(config_dir)
