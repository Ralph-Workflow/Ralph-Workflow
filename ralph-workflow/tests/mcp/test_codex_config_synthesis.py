from __future__ import annotations

import math
import tomllib
from pathlib import Path

import pytest
import tomli_w
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from loguru import logger

from ralph.mcp.tools.names import CODEX_NATIVE_FEATURE_OVERRIDES
from ralph.mcp.transport.codex import (
    _CODEX_TOOL_TIMEOUT_SEC,
    CodexConfigError,
    _synthesize_codex_config,
    prepare_codex_home,
)

_ENDPOINT = "http://127.0.0.1:9999/mcp"
_RALPH_OWNED_KEYS = frozenset({"features", "mcp_servers", "model_instructions_file"})


def _synthesize(
    tmp_path: Path,
    base_config: str,
    *,
    master_prompt_file: str | None = None,
    unsafe_mode: bool = False,
) -> str:
    source_home = tmp_path / "source-codex-home"
    source_home.mkdir(exist_ok=True)
    (source_home / "config.toml").write_text(base_config, encoding="utf-8")
    home = prepare_codex_home(
        _ENDPOINT,
        workspace_path=tmp_path,
        existing_home=str(source_home),
        master_prompt_file=master_prompt_file,
        unsafe_mode=unsafe_mode,
    )
    return (Path(home) / "config.toml").read_text(encoding="utf-8")


def _parse(config_text: str) -> dict[str, object]:
    try:
        return dict(tomllib.loads(config_text))
    except ValueError as exc:
        raise AssertionError(
            f"generated config.toml is not valid TOML ({exc}); Codex would refuse to start"
        ) from exc


def _features_of(config_text: str) -> dict[str, object]:
    features = _parse(config_text).get("features")
    assert isinstance(features, dict), "generated config must define a [features] table"
    return dict(features)


def _expected_features() -> dict[str, bool]:
    return {key.split(".", 1)[1]: value == "true" for key, value in CODEX_NATIVE_FEATURE_OVERRIDES}


def test_overrides_replace_colliding_feature_keys_from_the_base_config(tmp_path: Path) -> None:
    config_text = _synthesize(
        tmp_path,
        "[features]\ngoals = true\nmulti_agent = true\nshell_tool = true\nundo = true\n",
    )

    features = _features_of(config_text)
    assert features["goals"] is True, "unrelated operator features must survive"
    for key, value in _expected_features().items():
        assert features[key] is value, f"Ralph override for {key} must win"


def test_overrides_apply_when_the_features_header_carries_trailing_whitespace(
    tmp_path: Path,
) -> None:
    config_text = _synthesize(tmp_path, "[features]   \ngoals = true\n")

    features = _features_of(config_text)
    assert features["goals"] is True
    for key, value in _expected_features().items():
        assert features[key] is value


def test_overrides_apply_when_the_features_header_ends_the_file(tmp_path: Path) -> None:
    config_text = _synthesize(tmp_path, 'model = "gpt-5"\n[features]')

    features = _features_of(config_text)
    for key, value in _expected_features().items():
        assert features[key] is value


def test_overrides_apply_to_a_base_config_with_crlf_line_endings(tmp_path: Path) -> None:
    config_text = _synthesize(tmp_path, "[features]\r\ngoals = true\r\nmulti_agent = true\r\n")

    features = _features_of(config_text)
    assert features["goals"] is True
    for key, value in _expected_features().items():
        assert features[key] is value


def test_master_prompt_does_not_duplicate_an_existing_model_instructions_file(
    tmp_path: Path,
) -> None:
    master_prompt = tmp_path / "MASTER_PROMPT.md"
    master_prompt.write_text("system", encoding="utf-8")

    config_text = _synthesize(
        tmp_path,
        'model_instructions_file = "/operator/own.md"\nmodel = "gpt-5"\n',
        master_prompt_file=str(master_prompt),
    )

    parsed = _parse(config_text)
    assert parsed["model_instructions_file"] == str(master_prompt)
    assert parsed["model"] == "gpt-5", "unrelated top-level keys must survive"


def test_upstream_servers_are_stripped_from_a_crlf_base_config(tmp_path: Path) -> None:
    config_text = _synthesize(
        tmp_path,
        '[mcp_servers.operator]\r\nurl = "http://operator.invalid/mcp"\r\n',
    )

    servers = _parse(config_text).get("mcp_servers")
    assert isinstance(servers, dict)
    assert "operator" not in servers, "operator upstream must be dropped in restricted mode"


def test_a_realistic_operator_config_still_produces_loadable_toml(tmp_path: Path) -> None:
    master_prompt = tmp_path / "MASTER_PROMPT.md"
    master_prompt.write_text("system", encoding="utf-8")
    base_config = (
        'model = "gpt-5.6-sol"\n'
        'model_provider = "omnirouter"\n'
        "\n"
        '[projects."/tmp/work"]\n'
        'trust_level = "trusted"\n'
        "\n"
        "[features]\n"
        "goals = true\n"
        "unified_exec = true\n"
        "multi_agent = true\n"
        "plugins = true\n"
        "\n"
        "[features.multi_agent_v2]\n"
        "max_concurrent_threads_per_session = 16\n"
        "\n"
        "[mcp_servers.operator]\n"
        'url = "http://operator.invalid/mcp"\n'
    )

    config_text = _synthesize(tmp_path, base_config, master_prompt_file=str(master_prompt))

    parsed = _parse(config_text)
    assert parsed["model_provider"] == "omnirouter", "operator provider must survive"
    features = _features_of(config_text)
    assert features["goals"] is True
    for key, value in _expected_features().items():
        assert features[key] is value


_TOML_TEXT = st.text(
    alphabet=st.characters(min_codepoint=32, max_codepoint=126),
    max_size=12,
)
_TOML_KEYS = st.one_of(
    st.sampled_from(
        [
            "features",
            "mcp_servers",
            "model_instructions_file",
            "multi_agent",
            "shell_tool",
            "undo",
            "apps",
            "ralph",
            "model",
            "model_provider",
        ]
    ),
    _TOML_TEXT,
)
_TOML_SCALARS = st.one_of(
    st.booleans(),
    st.integers(min_value=-(2**53), max_value=2**53),
    st.floats(allow_nan=False, allow_infinity=False, width=64),
    _TOML_TEXT,
)
_TOML_LEAF_CONTAINERS = st.one_of(
    _TOML_SCALARS,
    st.lists(_TOML_SCALARS, max_size=3),
    st.dictionaries(_TOML_KEYS, _TOML_SCALARS, max_size=3),
)
_TOML_VALUES = st.one_of(
    _TOML_SCALARS,
    st.lists(_TOML_LEAF_CONTAINERS, max_size=3),
    st.dictionaries(_TOML_KEYS, _TOML_LEAF_CONTAINERS, max_size=3),
)
_OPERATOR_CONFIGS = st.dictionaries(_TOML_KEYS, _TOML_VALUES, max_size=6)


def _toml_value_depth(value: object) -> int:
    if isinstance(value, list):
        return 1 + max((_toml_value_depth(item) for item in value), default=0)
    if isinstance(value, dict):
        return 1 + max((_toml_value_depth(item) for item in value.values()), default=0)
    return 0


@pytest.mark.timeout_seconds(2)
@given(value=_TOML_VALUES)
@settings(max_examples=10, deadline=None, database=None)
def test_operator_config_strategy_regression_avoids_deep_toml_values(value: object) -> None:
    assert _toml_value_depth(value) <= 2


@pytest.mark.timeout_seconds(2)
@given(base=_OPERATOR_CONFIGS, unsafe_mode=st.booleans())
@settings(
    max_examples=10,
    deadline=None,
    database=None,
)
def test_any_operator_config_merges_into_a_config_codex_can_load(
    base: dict[str, object],
    unsafe_mode: bool,
) -> None:
    master_prompt = "diagnostics/MASTER_PROMPT.md"

    config_text, _upstreams = _synthesize_codex_config(
        tomli_w.dumps(base),
        source_config=Path("diagnostics/source-config.toml"),
        config_path=Path("diagnostics/generated-config.toml"),
        endpoint=_ENDPOINT,
        master_prompt_file=master_prompt,
        unsafe_mode=unsafe_mode,
    )

    parsed = _parse(config_text)
    features = _features_of(config_text)
    for key, value in _expected_features().items():
        assert features[key] is value, f"Ralph override for {key} must win"
    operator_servers = base.get("mcp_servers")
    surviving = (
        {name: value for name, value in operator_servers.items() if name != "ralph"}
        if unsafe_mode and isinstance(operator_servers, dict)
        else {}
    )
    assert parsed["mcp_servers"] == {
        **surviving,
        "ralph": {
            "url": _ENDPOINT,
            "enabled": True,
            "tool_timeout_sec": _CODEX_TOOL_TIMEOUT_SEC,
        },
    }
    assert parsed["model_instructions_file"] == master_prompt
    for key, value in base.items():
        if key in _RALPH_OWNED_KEYS:
            continue
        assert parsed[key] == value, f"operator setting {key!r} must survive untouched"


@given(base=_OPERATOR_CONFIGS)
@settings(
    max_examples=10,
    deadline=None,
    database=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
def test_operator_features_survive_unless_ralph_overrides_them(
    base: dict[str, object],
) -> None:
    operator_features = {**_parse(tomli_w.dumps(base)), "goals": True, "shell_tool": False}
    base_with_features: dict[str, object] = {"features": operator_features}

    config_text, _upstreams = _synthesize_codex_config(
        tomli_w.dumps(base_with_features),
        source_config=Path("diagnostics/source-config.toml"),
        config_path=Path("diagnostics/generated-config.toml"),
        endpoint=_ENDPOINT,
        master_prompt_file=None,
        unsafe_mode=False,
    )

    features = _features_of(config_text)
    for key, value in _expected_features().items():
        assert features[key] is value
    for key, value in operator_features.items():
        if key in _expected_features():
            continue
        assert features[key] == value, f"operator feature {key!r} must survive untouched"


def test_codex_config_regression_stale_ralph_entry_does_not_abort_the_run(
    tmp_path: Path,
) -> None:
    config_text = _synthesize(
        tmp_path,
        '[mcp_servers.ralph]\nurl = "http://127.0.0.1:1/mcp"\nenabled = true\n',
    )

    servers = _parse(config_text)["mcp_servers"]
    assert isinstance(servers, dict)
    assert servers["ralph"] == {
        "url": _ENDPOINT,
        "enabled": True,
        "tool_timeout_sec": _CODEX_TOOL_TIMEOUT_SEC,
    }


def test_codex_config_regression_stale_ralph_entry_is_dropped_in_unsafe_mode(
    tmp_path: Path,
) -> None:
    config_text = _synthesize(
        tmp_path,
        "[mcp_servers.ralph]\n"
        'url = "http://127.0.0.1:1/mcp"\n'
        "\n"
        "[mcp_servers.ctx]\n"
        'command = "ctx-server"\n',
        unsafe_mode=True,
    )

    servers = _parse(config_text)["mcp_servers"]
    assert isinstance(servers, dict)
    assert servers["ralph"] == {
        "url": _ENDPOINT,
        "enabled": True,
        "tool_timeout_sec": _CODEX_TOOL_TIMEOUT_SEC,
    }
    assert servers["ctx"] == {"command": "ctx-server"}


def test_codex_config_regression_unparseable_operator_config_fails_the_run(
    tmp_path: Path,
) -> None:
    with pytest.raises(CodexConfigError, match="not loadable TOML"):
        _synthesize(tmp_path, 'model = "gpt-5"\n[features\n')


def test_codex_config_regression_a_nan_does_not_trip_the_round_trip_guard(
    tmp_path: Path,
) -> None:
    errors: list[str] = []
    sink_id = logger.add(lambda message: errors.append(str(message)), level="ERROR")
    try:
        config_text = _synthesize(tmp_path, "temperature = nan\n")
    finally:
        logger.remove(sink_id)

    temperature = _parse(config_text)["temperature"]
    assert isinstance(temperature, float)
    assert math.isnan(temperature)
    assert errors == []


def test_codex_config_regression_a_deeply_nested_config_fails_the_run(
    tmp_path: Path,
) -> None:
    deep = "[" + ".".join(f"t{index}" for index in range(1200)) + "]\nx = 1\n"

    with pytest.raises(CodexConfigError, match="too deeply"):
        _synthesize(tmp_path, deep)


def _fail_symlinks(monkeypatch: pytest.MonkeyPatch) -> None:

    def refuse(*_args: object, **_kwargs: object) -> None:
        raise OSError(1, "Operation not permitted")

    monkeypatch.setattr(Path, "symlink_to", refuse)


def test_codex_home_regression_a_huge_session_database_is_not_copied_per_invocation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_home = tmp_path / "source-codex-home"
    source_home.mkdir()
    (source_home / "config.toml").write_text("", encoding="utf-8")
    (source_home / "auth.json").write_text('{"token": "keep-me"}', encoding="utf-8")
    huge = source_home / "logs_2.sqlite"
    huge.write_bytes(b"\0" * (2 * 1024 * 1024))
    monkeypatch.setattr("ralph.mcp.transport.codex._MIRROR_COPY_BYTE_BUDGET", 1024 * 1024)
    _fail_symlinks(monkeypatch)

    home = Path(
        prepare_codex_home(
            _ENDPOINT,
            workspace_path=tmp_path,
            existing_home=str(source_home),
            master_prompt_file=None,
        )
    )

    assert (home / "auth.json").read_text(encoding="utf-8") == '{"token": "keep-me"}'
    assert not (home / "logs_2.sqlite").exists()
