from __future__ import annotations

import json
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Final
from unittest.mock import patch

import pytest

from ralph.cli.commands import run as run_module
from ralph.skills import upstream

RALPH_TEST_CHANGE_REASONS: Final[dict[str, str]] = {
    "test_the_run_start_path_returns_while_the_upstream_check_is_still_outstanding probe.calls": (
        "the production registry contains two release-tagged upstreams and checks every entry; "
        "the replacement remains exact ordered equality against every configured release API"
    ),
}

if TYPE_CHECKING:
    from ralph.skills.upstream import HttpClient, HttpResponse

pytestmark = [pytest.mark.timeout_seconds(15)]

RALPH_TEST_CHANGE_REASONS: dict[str, str] = {
    "test_the_run_start_path_returns_while_the_upstream_check_is_still_outstandin": (
        "probe calls assert now expects both superpowers and antislop release api urls"
    ),
}

_RELEASE_TAG = "v-test-1"
_EVENT_WAIT_SECONDS = 10.0
_TRANSPORT_WAIT_SECONDS = 10.0
_UNIVERSAL_UPDATE_MESSAGE_PREFIX = "Universal skill update:"


@dataclass
class _UpstreamProbe:
    started: threading.Event = field(default_factory=threading.Event)
    release: threading.Event = field(default_factory=threading.Event)
    responded: threading.Event = field(default_factory=threading.Event)
    reported: threading.Event = field(default_factory=threading.Event)
    calls: list[str] = field(default_factory=list)
    messages: list[str] = field(default_factory=list)
    worker: threading.Thread | None = None


class _StubSkillManager:
    def check_skills_for_updates(self) -> bool:
        return False


@contextmanager
def _sandbox(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.delenv("RALPH_DISABLE_SKILL_UPDATES", raising=False)
    with patch("pathlib.Path.home", return_value=home):
        yield home


def _seed_installed_state() -> None:
    state_path = upstream.universal_state_path()
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(
        json.dumps(
            {
                "source": upstream.UPSTREAM_REPO,
                "installed_tag": _RELEASE_TAG,
                "skills": {
                    name: {"source_tag": _RELEASE_TAG, "installed_sha256": "0" * 64}
                    for name in upstream.UNIVERSAL_SKILL_NAMES
                },
            }
        ),
        encoding="utf-8",
    )


def _blocking_client(probe: _UpstreamProbe) -> HttpClient:
    def _get(url: str, timeout: float) -> HttpResponse:
        probe.calls.append(url)
        probe.worker = threading.current_thread()
        probe.started.set()
        probe.release.wait(timeout=_TRANSPORT_WAIT_SECONDS)
        probe.responded.set()
        return (200, json.dumps({"tag_name": _RELEASE_TAG}).encode("utf-8"))

    return _get


def _dependencies(
    probe: _UpstreamProbe,
    retention_sweeps: list[Path],
) -> run_module.SetupDependencies:

    def _emit(message: str) -> None:
        probe.messages.append(message)
        if message.startswith(_UNIVERSAL_UPDATE_MESSAGE_PREFIX):
            probe.reported.set()

    def _retention(
        root: Path,
        *,
        keep_run_id: str | None,
        retention_max_age_seconds: float | None,
    ) -> None:
        del keep_run_id, retention_max_age_seconds
        retention_sweeps.append(root)

    def _install(workspace_root: Path) -> tuple[object, list[str]]:
        del workspace_root
        return (object(), [])

    return run_module.SetupDependencies(
        emit_warning=_emit,
        print_user_global_update_hint=lambda: None,
        print_project_skill_conflict_hint=lambda failures: None,
        run_retention_sweep=_retention,
        skill_manager_factory=_StubSkillManager,
        project_skills_need_install=lambda root: False,
        install_project_skills=_install,
    )


@contextmanager
def _blocked_upstream_check(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[_UpstreamProbe]:
    probe = _UpstreamProbe()
    with _sandbox(tmp_path, monkeypatch):
        _seed_installed_state()
        monkeypatch.setattr(
            "ralph.skills.upstream.default_http_client",
            lambda: _blocking_client(probe),
        )
        try:
            yield probe
        finally:
            probe.release.set()
            worker = probe.worker
            if worker is not None and worker is not threading.current_thread():
                worker.join(timeout=_EVENT_WAIT_SECONDS)


def _run_start(tmp_path: Path, probe: _UpstreamProbe) -> list[Path]:
    retention_sweeps: list[Path] = []
    run_module.sync_shipped_skills(
        tmp_path,
        keep_run_id=None,
        retention_max_age_seconds=None,
        dependencies=_dependencies(probe, retention_sweeps),
    )
    return retention_sweeps


def test_the_run_start_path_returns_while_the_upstream_check_is_still_outstanding(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with _blocked_upstream_check(tmp_path, monkeypatch) as probe:
        retention_sweeps = _run_start(tmp_path, probe)

        assert probe.started.wait(timeout=_EVENT_WAIT_SECONDS), (
            "the upstream check never reached the transport"
        )
        assert not probe.responded.is_set(), (
            "the run-start path waited for the upstream check to finish"
        )
        assert retention_sweeps == [tmp_path], (
            f"the run-start path must finish its own work; got {retention_sweeps!r}"
        )
        assert not probe.reported.is_set(), (
            f"the outstanding upstream check must not report yet; got {probe.messages!r}"
        )

        probe.release.set()

        assert probe.reported.wait(timeout=_EVENT_WAIT_SECONDS), (
            f"the deferred upstream check never reported; got {probe.messages!r}"
        )
        assert probe.calls == [source.api_url for source in upstream.UNIVERSAL_UPSTREAMS], (
            "the upstream check must query every configured release API and nothing else; "
            f"got {probe.calls!r}"
        )
        assert any("up to date" in message for message in probe.messages), (
            f"the deferred upstream check must succeed; got {probe.messages!r}"
        )


def test_the_upstream_check_runs_off_the_run_start_thread(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with _blocked_upstream_check(tmp_path, monkeypatch) as probe:
        _run_start(tmp_path, probe)

        assert probe.started.wait(timeout=_EVENT_WAIT_SECONDS), (
            "the upstream check never reached the transport"
        )
        worker = probe.worker
        assert worker is not None, "the upstream check never ran"
        assert worker is not threading.main_thread(), (
            "the upstream check must not run on the run-start thread"
        )
        assert worker.daemon is True, (
            "the upstream check must run on a daemon thread so it cannot delay the run"
        )
