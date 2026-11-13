from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

from ralph.agents.invoke._runtime_resolvers import AgyRuntimeResolver
from ralph.config.enums import AgentTransport
from ralph.config.models import AgentConfig
from ralph.mcp.protocol.env import MCP_ENDPOINT_ENV

if TYPE_CHECKING:
    import pytest


def test_agy_runtime_preserves_auth_and_generated_configs_in_private_home(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    operator_home = tmp_path / "operator-home"
    auth_root = operator_home / ".gemini" / "antigravity-cli"
    auth_root.mkdir(parents=True)
    auth_artifacts = {
        auth_root / "antigravity-oauth-token": b"placeholder-oauth-token",
        auth_root / "settings.json": b'{"theme":"operator"}',
        auth_root / "installation_id": b"placeholder-installation-id",
    }
    for path, contents in auth_artifacts.items():
        path.write_bytes(contents)
    monkeypatch.setenv("HOME", str(operator_home))

    endpoint = "http://127.0.0.1:9999/mcp"
    runtime = AgyRuntimeResolver().resolve(
        AgentConfig(cmd="agy", transport=AgentTransport.AGY),
        extra_env={str(MCP_ENDPOINT_ENV): endpoint},
        workspace_path=tmp_path,
        base_env={"HOME": str(operator_home)},
    )

    try:
        assert runtime.agent_env is not None
        private_home = Path(runtime.agent_env["HOME"])
        assert private_home != operator_home
        assert runtime.agent_env["HOME"] != str(operator_home)

        for source_path, source_bytes in auth_artifacts.items():
            relative_path = source_path.relative_to(operator_home)
            assert (private_home / relative_path).read_bytes() == source_bytes

        for relative_path in (
            Path(".gemini/antigravity-cli/mcp_config.json"),
            Path(".gemini/config/mcp_config.json"),
        ):
            generated_config = json.loads(
                (private_home / relative_path).read_text(encoding="utf-8")
            )
            assert generated_config["mcpServers"]["ralph"] == {"serverUrl": endpoint}

        for source_path, source_bytes in auth_artifacts.items():
            assert source_path.read_bytes() == source_bytes
    finally:
        assert runtime.cleanup is not None
        runtime.cleanup()

    assert not private_home.exists()
    for source_path, source_bytes in auth_artifacts.items():
        assert source_path.read_bytes() == source_bytes


_UNIVERSAL_SKILL_NAMES = (
    "test-driven-development",
    "systematic-debugging",
    "verification-before-completion",
)

_AGY_USER_GLOBAL_SKILL_ROOTS = (
    Path(".gemini/antigravity-cli/skills"),
    Path(".gemini/config/skills"),
)

_SKILL_FILE_NAME = "SKILL.md"

_COMPANION_FILE_RELATIVE_PATH = Path("references/companion-checklist.md")

_AGY_MCP_CONFIG_RELATIVE_PATHS = (
    Path(".gemini/antigravity-cli/mcp_config.json"),
    Path(".gemini/config/mcp_config.json"),
)


def _write_operator_skill_tree(operator_home: Path) -> dict[str, bytes]:
    expected_bytes: dict[str, bytes] = {}
    for root in _AGY_USER_GLOBAL_SKILL_ROOTS:
        for skill_name in _UNIVERSAL_SKILL_NAMES:
            skill_dir = operator_home / root / skill_name
            companion_path = skill_dir / _COMPANION_FILE_RELATIVE_PATH
            companion_path.parent.mkdir(parents=True, exist_ok=True)
            skill_bytes = f"# {skill_name}\n\nSkill body under {root.as_posix()}.\n".encode()
            companion_bytes = (
                f"Companion notes for {skill_name} under {root.as_posix()}.\n".encode()
            )
            (skill_dir / _SKILL_FILE_NAME).write_bytes(skill_bytes)
            companion_path.write_bytes(companion_bytes)
            expected_bytes[(root / skill_name / _SKILL_FILE_NAME).as_posix()] = skill_bytes
            expected_bytes[(root / skill_name / _COMPANION_FILE_RELATIVE_PATH).as_posix()] = (
                companion_bytes
            )
    return expected_bytes


def _skill_tree_bytes(skill_root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(skill_root).as_posix(): path.read_bytes()
        for path in sorted(skill_root.rglob("*"))
        if path.is_file()
    }


def test_agy_runtime_projects_universal_skills_into_private_home(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    operator_home = tmp_path / "operator-home"
    expected_bytes = _write_operator_skill_tree(operator_home)
    operator_skill_roots = tuple(operator_home / root for root in _AGY_USER_GLOBAL_SKILL_ROOTS)
    operator_bytes_before = {root: _skill_tree_bytes(root) for root in operator_skill_roots}
    monkeypatch.setenv("HOME", str(operator_home))

    endpoint = "http://127.0.0.1:9999/mcp"
    runtime = AgyRuntimeResolver().resolve(
        AgentConfig(cmd="agy", transport=AgentTransport.AGY),
        extra_env={str(MCP_ENDPOINT_ENV): endpoint},
        workspace_path=tmp_path,
        base_env={"HOME": str(operator_home)},
    )

    assert runtime.agent_env is not None
    private_home = Path(runtime.agent_env["HOME"])
    try:
        assert private_home != operator_home

        for relative_path in _AGY_MCP_CONFIG_RELATIVE_PATHS:
            operator_config = operator_home / relative_path
            assert not operator_config.exists(), f"operator HOME leaked MCP config: {relative_path}"
            assert not operator_config.is_symlink()

        for relative_path in _AGY_MCP_CONFIG_RELATIVE_PATHS:
            private_config = private_home / relative_path
            assert private_config.parent.is_dir()
            assert not private_config.parent.is_symlink()
            assert private_config.is_file()
            assert not private_config.is_symlink()
            generated_config = json.loads(private_config.read_text(encoding="utf-8"))
            assert generated_config["mcpServers"]["ralph"] == {"serverUrl": endpoint}

        for relative_path, expected in expected_bytes.items():
            projected = private_home / relative_path
            assert projected.is_file(), f"private HOME lacks skill projection: {relative_path}"
            assert projected.read_bytes() == expected

        for root in operator_skill_roots:
            assert _skill_tree_bytes(root) == operator_bytes_before[root]
    finally:
        assert runtime.cleanup is not None
        runtime.cleanup()

    assert not private_home.exists()
    for root in operator_skill_roots:
        assert root.is_dir()
        assert _skill_tree_bytes(root) == operator_bytes_before[root]
