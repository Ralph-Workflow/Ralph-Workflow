"""Codex config.toml synthesis must never emit a config Codex refuses to load.

``prepare_codex_home`` splices Ralph's required settings into a copy of the
operator's ``~/.codex/config.toml``. TOML forbids duplicate keys, so a splice
that re-states a key the operator already set produces a file Codex rejects
outright ("Error loading config.toml: ... duplicate key") and the run dies
before the agent emits a single event. These tests pin one invariant: whatever
the base config looks like, the generated file parses and Ralph's overrides win.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

import tomli_w
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from ralph.mcp.tools.names import CODEX_NATIVE_FEATURE_OVERRIDES
from ralph.mcp.transport.codex import prepare_codex_home

_ENDPOINT = "http://127.0.0.1:9999/mcp"
#: Keys Ralph owns outright; the operator's values for these are meant to lose.
_RALPH_OWNED_KEYS = frozenset({"features", "mcp_servers", "model_instructions_file"})


def _synthesize(
    tmp_path: Path,
    base_config: str,
    *,
    master_prompt_file: str | None = None,
    unsafe_mode: bool = False,
) -> str:
    """Write *base_config* as the source Codex home; return the generated config text."""
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
    """Parse the generated config, surfacing the exact defect Codex would report."""
    try:
        return dict(tomllib.loads(config_text))
    except ValueError as exc:  # pragma: no cover - assertion detail
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
    """A base [features] table that already sets an overridden key must not duplicate it."""
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
    """A '[features]   ' header is still the features table; overrides must land in it."""
    config_text = _synthesize(tmp_path, "[features]   \ngoals = true\n")

    features = _features_of(config_text)
    assert features["goals"] is True
    for key, value in _expected_features().items():
        assert features[key] is value


def test_overrides_apply_when_the_features_header_ends_the_file(tmp_path: Path) -> None:
    """A trailing '[features]' with no newline must still receive the overrides."""
    config_text = _synthesize(tmp_path, 'model = "gpt-5"\n[features]')

    features = _features_of(config_text)
    for key, value in _expected_features().items():
        assert features[key] is value


def test_overrides_apply_to_a_base_config_with_crlf_line_endings(tmp_path: Path) -> None:
    """Windows-authored configs must get the same overrides as LF ones."""
    config_text = _synthesize(tmp_path, "[features]\r\ngoals = true\r\nmulti_agent = true\r\n")

    features = _features_of(config_text)
    assert features["goals"] is True
    for key, value in _expected_features().items():
        assert features[key] is value


def test_master_prompt_does_not_duplicate_an_existing_model_instructions_file(
    tmp_path: Path,
) -> None:
    """Ralph's master prompt must replace, not duplicate, an operator's own setting."""
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
    """Tool restriction must not leak operator MCP servers just because of line endings."""
    config_text = _synthesize(
        tmp_path,
        '[mcp_servers.operator]\r\nurl = "http://operator.invalid/mcp"\r\n',
    )

    servers = _parse(config_text).get("mcp_servers")
    assert isinstance(servers, dict)
    assert "operator" not in servers, "operator upstream must be dropped in restricted mode"


def test_a_realistic_operator_config_still_produces_loadable_toml(tmp_path: Path) -> None:
    """Regression net for the shipped Codex feature set an operator is likely to enable."""
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


# --- The standing guarantee -------------------------------------------------
#
# The example-based tests above pin the collisions that have actually bitten us.
# The property below is the reason no *new* collision can: it asserts the merge
# invariant against arbitrary operator configs rather than against a list of
# shapes someone remembered to write down.

_TOML_TEXT = st.text(
    alphabet=st.characters(min_codepoint=32, max_codepoint=126),
    max_size=12,
)
#: Free text alone would essentially never generate "features" or "multi_agent",
#: so the collisions this suite exists to rule out would go unexercised. Keys are
#: drawn from Ralph's own names as often as from arbitrary text.
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
_TOML_VALUES = st.recursive(
    _TOML_SCALARS,
    lambda children: st.one_of(
        st.lists(children, max_size=3),
        st.dictionaries(_TOML_KEYS, children, max_size=3),
    ),
    max_leaves=5,
)
_OPERATOR_CONFIGS = st.dictionaries(_TOML_KEYS, _TOML_VALUES, max_size=6)


@given(base=_OPERATOR_CONFIGS, unsafe_mode=st.booleans())
@settings(
    max_examples=25,
    deadline=None,
    database=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
def test_any_operator_config_merges_into_a_config_codex_can_load(
    tmp_path: Path,
    base: dict[str, object],
    unsafe_mode: bool,
) -> None:
    """For *any* valid operator config: the result loads, Ralph wins, the operator survives."""
    master_prompt = str(tmp_path / "MASTER_PROMPT.md")

    config_text = _synthesize(
        tmp_path,
        tomli_w.dumps(base),
        master_prompt_file=master_prompt,
        unsafe_mode=unsafe_mode,
    )

    parsed = _parse(config_text)
    features = _features_of(config_text)
    for key, value in _expected_features().items():
        assert features[key] is value, f"Ralph override for {key} must win"
    servers = parsed["mcp_servers"]
    assert isinstance(servers, dict)
    assert servers["ralph"] == {"url": _ENDPOINT, "enabled": True}
    assert parsed["model_instructions_file"] == master_prompt
    for key, value in base.items():
        if key in _RALPH_OWNED_KEYS:
            continue
        assert parsed[key] == value, f"operator setting {key!r} must survive untouched"


@given(base=_OPERATOR_CONFIGS)
@settings(
    max_examples=25,
    deadline=None,
    database=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
def test_operator_features_survive_unless_ralph_overrides_them(
    tmp_path: Path,
    base: dict[str, object],
) -> None:
    """Ralph replaces only the feature keys it names; the rest of [features] is the operator's."""
    operator_features = {**_parse(tomli_w.dumps(base)), "goals": True, "shell_tool": False}
    base_with_features: dict[str, object] = {"features": operator_features}

    config_text = _synthesize(tmp_path, tomli_w.dumps(base_with_features))

    features = _features_of(config_text)
    for key, value in _expected_features().items():
        assert features[key] is value
    for key, value in operator_features.items():
        if key in _expected_features():
            continue
        assert features[key] == value, f"operator feature {key!r} must survive untouched"
