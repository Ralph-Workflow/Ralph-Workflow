from __future__ import annotations

from importlib import import_module

import pytest
from loguru import logger

INLINE_SECRET = "inline-secret-value"
ENV_SECRET = "env-secret-value"
ENV_NAME = "RALPH_TEST_WEBSEARCH_API_KEY"


def _import_secrets_module():
    try:
        return import_module("ralph.mcp.websearch.secrets")
    except ModuleNotFoundError as exc:  # pragma: no cover - exercised in RED phase
        raise AssertionError("ralph.mcp.websearch.secrets should exist") from exc


def test_resolve_secret_prefers_inline_value_when_only_inline_is_set() -> None:
    secrets = _import_secrets_module()

    resolved = secrets.resolve_secret(INLINE_SECRET, None)

    assert resolved == INLINE_SECRET


def test_resolve_secret_reads_environment_value(monkeypatch: pytest.MonkeyPatch) -> None:
    secrets = _import_secrets_module()
    monkeypatch.setenv(ENV_NAME, ENV_SECRET)

    resolved = secrets.resolve_secret(None, ENV_NAME)

    assert resolved == ENV_SECRET


def test_resolve_secret_rejects_missing_configuration() -> None:
    secrets = _import_secrets_module()

    with pytest.raises(ValueError, match="exactly one"):
        secrets.resolve_secret(None, None)


def test_resolve_secret_rejects_both_inline_and_env() -> None:
    secrets = _import_secrets_module()

    with pytest.raises(ValueError, match="exactly one"):
        secrets.resolve_secret(INLINE_SECRET, ENV_NAME)


def test_resolve_secret_rejects_missing_environment_value(monkeypatch: pytest.MonkeyPatch) -> None:
    secrets = _import_secrets_module()
    monkeypatch.delenv(ENV_NAME, raising=False)

    with pytest.raises(secrets.WebSearchError, match=ENV_NAME):
        secrets.resolve_secret(None, ENV_NAME)


def test_resolve_secret_does_not_log_secret_value(monkeypatch: pytest.MonkeyPatch) -> None:
    secrets = _import_secrets_module()
    monkeypatch.setenv(ENV_NAME, ENV_SECRET)
    records: list[str] = []
    sink_id = logger.add(lambda message: records.append(str(message)), format="{message}")

    try:
        resolved = secrets.resolve_secret(None, ENV_NAME)
        logger.info("resolved websearch secret for runtime guard")
    finally:
        logger.remove(sink_id)

    assert resolved == ENV_SECRET
    joined = "\n".join(records)
    assert ENV_SECRET not in joined
    assert INLINE_SECRET not in joined
