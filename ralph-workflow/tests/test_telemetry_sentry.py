"""Tests for ralph.telemetry._sentry."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ralph.telemetry._sentry import _scrub_event, _scrub_obj, init_sentry

if TYPE_CHECKING:
    import pytest


def test_init_sentry_calls_sentry_init(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: list[dict[str, object]] = []

    def capture_init(**kwargs: object) -> None:
        captured.append(dict(kwargs))

    def noop_set_user(arg: object) -> None:
        pass

    def noop_set_tag(key: object, val: object) -> None:
        pass

    monkeypatch.setattr("sentry_sdk.init", capture_init)
    monkeypatch.setattr("sentry_sdk.set_user", noop_set_user)
    monkeypatch.setattr("sentry_sdk.set_tag", noop_set_tag)

    init_sentry("a" * 32, "b" * 64)

    assert len(captured) == 1
    kwargs = captured[0]
    assert kwargs.get("send_default_pii") is False
    assert "dsn" in kwargs
    assert "sentry.io" in str(kwargs["dsn"])
    assert "before_send" in kwargs
    assert "before_send_transaction" in kwargs
    assert kwargs.get("traces_sample_rate") == 1.0
    assert kwargs.get("profiles_sample_rate") == 1.0


def test_init_sentry_sets_user_id(monkeypatch: pytest.MonkeyPatch) -> None:
    user_calls: list[object] = []

    def noop_init(**kwargs: object) -> None:
        pass

    def capture_set_user(arg: object) -> None:
        user_calls.append(arg)

    def noop_set_tag(key: object, val: object) -> None:
        pass

    monkeypatch.setattr("sentry_sdk.init", noop_init)
    monkeypatch.setattr("sentry_sdk.set_user", capture_set_user)
    monkeypatch.setattr("sentry_sdk.set_tag", noop_set_tag)

    uid = "x" * 32
    init_sentry(uid, "y" * 64)

    assert len(user_calls) == 1
    arg = user_calls[0]
    assert isinstance(arg, dict)
    assert arg == {"id": uid}
    assert len(arg) == 1


def test_init_sentry_sets_session_tag(monkeypatch: pytest.MonkeyPatch) -> None:
    tag_calls: list[tuple[str, str]] = []

    def noop_init(**kwargs: object) -> None:
        pass

    def noop_set_user(arg: object) -> None:
        pass

    def capture_set_tag(k: str, v: str) -> None:
        tag_calls.append((k, v))

    monkeypatch.setattr("sentry_sdk.init", noop_init)
    monkeypatch.setattr("sentry_sdk.set_user", noop_set_user)
    monkeypatch.setattr("sentry_sdk.set_tag", capture_set_tag)

    sid = "z" * 64
    init_sentry("a" * 32, sid)

    assert ("session_id", sid) in tag_calls


def test_scrub_obj_replaces_home_prefix_in_string(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_home = "/fake/home/testuser"
    monkeypatch.setattr("ralph.telemetry._sentry._HOME_PREFIX", fake_home)

    data: dict[str, object] = {"path": f"{fake_home}/projects"}
    _scrub_obj(data)

    assert data["path"] == "~/projects"


def test_scrub_obj_handles_nested_dicts(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_home = "/fake/home/testuser"
    monkeypatch.setattr("ralph.telemetry._sentry._HOME_PREFIX", fake_home)

    data: dict[str, object] = {"outer": {"inner": f"{fake_home}/x"}}
    _scrub_obj(data)

    outer = data["outer"]
    assert isinstance(outer, dict)
    assert outer["inner"] == "~/x"


def test_scrub_obj_handles_lists(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_home = "/fake/home/testuser"
    monkeypatch.setattr("ralph.telemetry._sentry._HOME_PREFIX", fake_home)

    lst: list[object] = [f"{fake_home}/a", f"{fake_home}/b"]
    _scrub_obj(lst)

    assert lst[0] == "~/a"
    assert lst[1] == "~/b"


def test_scrub_obj_ignores_non_string_values(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_home = "/fake/home/testuser"
    monkeypatch.setattr("ralph.telemetry._sentry._HOME_PREFIX", fake_home)

    data: dict[str, object] = {"n": 42, "b": True, "x": None}
    _scrub_obj(data)

    assert data["n"] == 42
    assert data["b"] is True
    assert data["x"] is None


def test_scrub_event_removes_server_name() -> None:
    event: dict[str, object] = {"server_name": "myhost.local", "message": "test"}
    result = _scrub_event(event, {})
    assert isinstance(result, dict)
    assert "server_name" not in result
    assert result.get("message") == "test"
