from __future__ import annotations

import contextlib
from collections.abc import Callable, Generator
from dataclasses import dataclass
from pathlib import Path

import pytest
from loguru import logger

from ralph.agents.registry import (
    AgentRegistry,
    agy_alias_help,
    agy_models_error,
    agy_published_models,
    make_agy_models_probe,
    read_agy_models,
    set_agy_models_probe,
)
from ralph.config.models import UnifiedConfig
from ralph.executor.process import TIMEOUT_EXIT_CODE, ProcessExecutionError
from ralph.policy.validation import PolicyValidationError, validate_agent_chains_satisfiable

RALPH_TEST_CHANGE_REASONS = {
    "test_failed_agy_listing_is_reported_as_an_agy_error_with_no_fallback_models": (
        "`agy models` can fail where AGY agents still authenticate (an SSH-started "
        "tmux session uses file-based token storage for the listing), so a failed "
        "listing no longer rejects a well-formed configured agy/<model>; it is still "
        "reported as an AGY error and no built-in model list is ever used"
    ),
    "test_preflight_reports_an_agy_error_not_unknown_agents_when_agy_cannot_list": (
        "the preflight now warns with the AGY error and starts the run instead of "
        "refusing, because refusing stopped runs whose AGY agents work"
    ),
}

_LIVE_AGY_MODELS_STDOUT = "\n".join(
    (
        "gemini-4-neon-high\tGemini 4 Neon (High)",
        "gemini-4-argon-medium\tGemini 4 Argon (Medium)",
        "gemini-4-argon-low\tGemini 4 Argon (Low)",
        "gemini-3.8-flash-low\tGemini 3.8 Flash (Low)",
        "claude-opus-5-5-high\tClaude Opus 5.5 (High)",
    )
)


@dataclass(frozen=True)
class _Completed:
    command: tuple[str, ...]
    returncode: int
    stdout: str
    stderr: str


def _probe(outcome: str | Exception) -> Callable[[], str]:
    def probe() -> str:
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    return probe


@pytest.fixture
def agy_probe() -> Generator[Callable[[str | Exception], None], None, None]:
    def install(outcome: str | Exception) -> None:
        set_agy_models_probe(_probe(outcome))

    try:
        yield install
    finally:
        set_agy_models_probe(None)


def _registry() -> AgentRegistry:
    return AgentRegistry.from_config(UnifiedConfig())


def test_model_listed_by_live_agy_resolves(agy_probe: Callable[[str | Exception], None]) -> None:
    agy_probe(_LIVE_AGY_MODELS_STDOUT)
    config = _registry().get("agy/gemini-4-argon-low")
    assert config is not None
    assert config.model_flag == "--model gemini-4-argon-low"


def test_model_missing_from_live_agy_is_rejected_and_help_lists_live_models(
    agy_probe: Callable[[str | Exception], None],
) -> None:
    agy_probe(_LIVE_AGY_MODELS_STDOUT)
    assert _registry().get("agy/gemini-3.6-flash-low") is None
    help_text = agy_alias_help()
    assert "gemini-4-argon-low" in help_text
    assert "gemini-3.6-flash-low" not in help_text
    assert agy_models_error() is None


@pytest.mark.parametrize(
    ("failure", "reason"),
    [
        (OSError("`agy models` could not run: agy not found"), "agy not found"),
        (OSError("`agy models` exited 1: not signed in"), "not signed in"),
    ],
)
def test_failed_agy_listing_is_reported_as_an_agy_error_with_no_fallback_models(
    agy_probe: Callable[[str | Exception], None], failure: OSError, reason: str
) -> None:
    agy_probe(failure)
    assert agy_published_models() == ()
    configured = _registry().get("agy/gemini-4-argon-low")
    assert configured is not None
    assert configured.model_flag == "--model gemini-4-argon-low"
    assert _registry().get("agy/gemini-4-argon-low:high") is None
    error = agy_models_error()
    assert error is not None
    assert reason in error
    help_text = agy_alias_help()
    assert help_text.startswith("AGY error:")
    assert reason in help_text
    assert "gemini-3.8-flash-low" not in help_text
    assert "Available AGY models" not in help_text


def test_empty_agy_listing_is_an_agy_error(agy_probe: Callable[[str | Exception], None]) -> None:
    agy_probe("\n")
    assert agy_published_models() == ()
    assert agy_models_error() == "`agy models` listed no models"


@dataclass(frozen=True)
class _ChainConfig:
    agents: list[str]


@dataclass(frozen=True)
class _Agents:
    agent_chains: dict[str, _ChainConfig]


@dataclass(frozen=True)
class _Bundle:
    agents: _Agents


def test_preflight_warns_with_the_agy_error_and_still_starts_when_agy_cannot_list(
    agy_probe: Callable[[str | Exception], None],
) -> None:
    agy_probe(OSError("`agy models` exited 1: not signed in"))
    bundle = _Bundle(_Agents({"planning": _ChainConfig(["agy/gemini-4-argon-low"])}))
    warnings: list[str] = []
    sink = logger.add(lambda message: warnings.append(str(message)), level="WARNING")
    try:
        validate_agent_chains_satisfiable(bundle, _registry())
    finally:
        logger.remove(sink)
    joined = "".join(warnings)
    assert "AGY error: `agy models` exited 1: not signed in" in joined
    assert "agy/gemini-4-argon-low" in joined
    assert "gemini-3.8-flash-low" not in joined


def test_preflight_still_rejects_a_malformed_agy_alias_when_agy_cannot_list(
    agy_probe: Callable[[str | Exception], None],
) -> None:
    agy_probe(OSError("`agy models` exited 1: not signed in"))
    bundle = _Bundle(_Agents({"planning": _ChainConfig(["agy/gemini-4-argon-low:high"])}))
    with pytest.raises(PolicyValidationError):
        validate_agent_chains_satisfiable(bundle, _registry())


def test_preflight_accepts_a_chain_model_the_live_listing_publishes(
    agy_probe: Callable[[str | Exception], None],
) -> None:
    agy_probe(_LIVE_AGY_MODELS_STDOUT)
    bundle = _Bundle(_Agents({"development": _ChainConfig(["agy/gemini-4-argon-low"])}))
    validate_agent_chains_satisfiable(bundle, _registry())


def _runner(result: _Completed | Exception) -> Callable[..., _Completed]:
    def run(*_args: object, **_kwargs: object) -> _Completed:
        if isinstance(result, Exception):
            raise result
        return result

    return run


def test_read_agy_models_returns_the_live_listing() -> None:
    run = _runner(_Completed(("agy", "models"), 0, _LIVE_AGY_MODELS_STDOUT, ""))
    assert read_agy_models(run=run) == _LIVE_AGY_MODELS_STDOUT


@pytest.mark.parametrize(
    ("result", "reason"),
    [
        (
            _Completed(("agy", "models"), 2, "", "error: not signed in\n"),
            "exited 2: error: not signed in",
        ),
        (_Completed(("agy", "models"), TIMEOUT_EXIT_CODE, "", ""), "did not answer within"),
        (
            ProcessExecutionError(("agy", "models"), "Failed to execute 'agy': No such file"),
            "could not run: Failed to execute 'agy': No such file",
        ),
    ],
)
def test_read_agy_models_names_why_agy_failed(result: _Completed | Exception, reason: str) -> None:
    with pytest.raises(OSError, match="`agy models`") as exc_info:
        read_agy_models(run=_runner(result))
    assert reason in str(exc_info.value)


class _Reads:
    def __init__(self, outcome: str | Exception) -> None:
        self.outcome = outcome
        self.calls = 0

    def __call__(self) -> str:
        self.calls += 1
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome


@pytest.mark.parametrize(
    "outcome", [_LIVE_AGY_MODELS_STDOUT, OSError("`agy models` exited 1: not signed in")]
)
def test_agy_model_listing_runs_at_most_once_per_cache_window(outcome: str | Exception) -> None:
    reads = _Reads(outcome)
    now = [0.0]
    probe = make_agy_models_probe(read=reads, clock=lambda: now[0], ttl_seconds=900.0)
    for _ in range(50):
        with contextlib.suppress(OSError):
            probe()
        now[0] += 10.0
    assert reads.calls == 1
    now[0] = 901.0
    with contextlib.suppress(OSError):
        probe()
    assert reads.calls == 2


def test_cached_agy_listing_failure_is_reported_again_without_rerunning_agy() -> None:
    reads = _Reads(OSError("`agy models` exited 1: not signed in"))
    probe = make_agy_models_probe(read=reads, clock=lambda: 0.0, ttl_seconds=900.0)
    for _ in range(3):
        with pytest.raises(OSError, match="not signed in"):
            probe()
    assert reads.calls == 1


def test_agy_listing_runs_in_a_private_auth_home_and_never_the_real_one(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    real_home = tmp_path / "home"
    real_agy = real_home / ".gemini" / "antigravity-cli"
    real_agy.mkdir(parents=True)
    (real_agy / "antigravity-oauth-token").write_text("signed-in", encoding="utf-8")
    monkeypatch.setenv("HOME", str(real_home))
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    seen: list[Path] = []

    def run(*_args: object, **kwargs: object) -> _Completed:
        options = kwargs["options"]
        env = getattr(options, "env", None) or {}
        private_home = Path(env["HOME"])
        seen.append(private_home)
        token = private_home / ".gemini" / "antigravity-cli" / "antigravity-oauth-token"
        assert token.read_text(encoding="utf-8") == "signed-in"
        return _Completed(("agy", "models"), 0, _LIVE_AGY_MODELS_STDOUT, "")

    assert read_agy_models(run=run) == _LIVE_AGY_MODELS_STDOUT
    assert seen and seen[0] != real_home
    assert not seen[0].exists()
