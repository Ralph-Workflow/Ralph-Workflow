from __future__ import annotations

import hashlib
import json
import threading
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from ralph.cli.commands import run as run_module
from ralph.git.operations import get_head_sha, has_uncommitted_changes
from ralph.skills import get_skill_content, upstream
from ralph.skills.upstream import HttpClient, HttpResponse, UpdateOutcome
from tests.test_skills_upstream_update import _SKILL_FILES, _client_for

if TYPE_CHECKING:
    from collections.abc import Callable

pytestmark = [pytest.mark.timeout_seconds(15)]

RETIRED_SKILL_NAME = "brainstorming"


@dataclass(frozen=True)
class _Sandbox:
    home: Path
    config: Path
    cache: Path


@dataclass
class _UpdaterProbe:
    calls: list[dict[str, object]] = field(default_factory=list)
    event: threading.Event = field(default_factory=threading.Event)


class _StubSkillManager:
    def check_skills_for_updates(self) -> bool:
        return False


def _is_current_baseline_skill(name: str) -> bool:
    try:
        get_skill_content(name)
    except ValueError:
        return False
    return True


def _write_managed_skill(skill_dir: Path, name: str, content: str, digest: str) -> None:
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(content, encoding="utf-8")
    (skill_dir / ".ralph-managed.json").write_text(
        json.dumps(
            {
                "managed_by": "ralph-workflow",
                "skill": name,
                "installed_content_sha256": digest,
            }
        ),
        encoding="utf-8",
    )


def _build_dependencies(
    warnings: list[str],
    *,
    install_calls: list[Path] | None = None,
    need_install: bool = False,
    warning_hook: Callable[[str], None] | None = None,
) -> run_module.SetupDependencies:

    def _emit(message: str) -> None:
        warnings.append(message)
        if warning_hook is not None:
            warning_hook(message)

    def _retention_sweep(
        root: Path,
        *,
        keep_run_id: str | None,
        retention_max_age_seconds: float | None,
    ) -> None:
        _ = (root, keep_run_id, retention_max_age_seconds)

    def _install(workspace_root: Path) -> tuple[object, list[str]]:
        if install_calls is not None:
            install_calls.append(workspace_root)
        return (object(), [])

    return run_module.SetupDependencies(
        emit_warning=_emit,
        print_user_global_update_hint=lambda: None,
        print_project_skill_conflict_hint=lambda failures: None,
        run_retention_sweep=_retention_sweep,
        skill_manager_factory=_StubSkillManager,
        project_skills_need_install=lambda root: need_install,
        install_project_skills=_install,
    )


@pytest.fixture(autouse=True)
def sandbox(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> _Sandbox:
    home = tmp_path / "home"
    config = tmp_path / "config"
    cache = tmp_path / "cache"
    for directory in (home, config, cache):
        directory.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config))
    monkeypatch.setenv("XDG_CACHE_HOME", str(cache))
    monkeypatch.delenv("RALPH_DISABLE_SKILL_UPDATES", raising=False)
    return _Sandbox(home=home, config=config, cache=cache)


@pytest.fixture
def updater_probe(monkeypatch: pytest.MonkeyPatch) -> _UpdaterProbe:
    probe = _UpdaterProbe()
    monkeypatch.delenv("RALPH_DISABLE_SKILL_UPDATES", raising=False)

    def _fake(
        *,
        enabled: bool,
        now: datetime,
        http: Callable[[str, float], object],
    ) -> UpdateOutcome:
        probe.calls.append({"enabled": enabled, "now": now, "http": http})
        probe.event.set()
        return UpdateOutcome(
            status="up-to-date",
            messages=("the universal skills are up to date (v-test)",),
        )

    monkeypatch.setattr("ralph.skills.upstream.maybe_update_universal_skills", _fake)
    return probe


@pytest.fixture
def transport_probe(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    transports: list[str] = []

    def _recorder() -> Callable[[str, float], object]:
        def _get(url: str, timeout: float) -> object:
            transports.append(url)
            raise AssertionError("the updater must not reach the network")

        return _get

    monkeypatch.setattr("ralph.skills.upstream.default_http_client", _recorder)
    return transports


def _run_seam(
    tmp_path: Path,
    dependencies: run_module.SetupDependencies,
    *,
    workspace_root: Path | None = None,
) -> None:
    run_module.sync_shipped_skills(
        workspace_root=workspace_root or tmp_path,
        keep_run_id=None,
        retention_max_age_seconds=None,
        dependencies=dependencies,
    )


def test_run_start_invokes_universal_skill_update_exactly_once_when_enabled(
    tmp_path: Path,
    updater_probe: _UpdaterProbe,
) -> None:
    warnings: list[str] = []

    _run_seam(tmp_path, _build_dependencies(warnings))

    assert updater_probe.event.wait(timeout=5), "the universal-skill updater never ran"
    assert len(updater_probe.calls) == 1, (
        f"the updater must run exactly once per run; got {updater_probe.calls!r}"
    )
    assert updater_probe.calls[0]["enabled"] is True


def test_run_start_swallows_a_raising_universal_skill_update(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    probe = _UpdaterProbe()

    def _raising(
        *,
        enabled: bool,
        now: datetime,
        http: Callable[[str, float], object],
    ) -> UpdateOutcome:
        probe.calls.append({"enabled": enabled, "now": now, "http": http})
        probe.event.set()
        raise RuntimeError("simulated upstream failure")

    monkeypatch.setattr("ralph.skills.upstream.maybe_update_universal_skills", _raising)
    warnings: list[str] = []

    _run_seam(tmp_path, _build_dependencies(warnings))

    assert probe.event.wait(timeout=5), "the raising updater never ran"
    assert len(probe.calls) == 1


def test_run_start_reports_a_preserved_universal_skill(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    probe = _UpdaterProbe()
    reported = threading.Event()

    def _fake(
        *,
        enabled: bool,
        now: datetime,
        http: Callable[[str, float], object],
    ) -> UpdateOutcome:
        probe.calls.append({"enabled": enabled, "now": now, "http": http})
        probe.event.set()
        return UpdateOutcome(
            status="preserved",
            preserved=("systematic-debugging",),
            messages=(),
        )

    monkeypatch.setattr("ralph.skills.upstream.maybe_update_universal_skills", _fake)
    warnings: list[str] = []

    def _hook(message: str) -> None:
        if "systematic-debugging" in message:
            reported.set()

    _run_seam(tmp_path, _build_dependencies(warnings, warning_hook=_hook))

    assert probe.event.wait(timeout=5), "the universal-skill updater never ran"
    assert reported.wait(timeout=5), (
        f"a preserved universal skill must be reported; got {warnings!r}"
    )


def test_run_start_migrates_retired_user_global_skills(
    tmp_path: Path,
    sandbox: _Sandbox,
    updater_probe: _UpdaterProbe,
) -> None:
    assert not _is_current_baseline_skill(RETIRED_SKILL_NAME), (
        f"{RETIRED_SKILL_NAME!r} must be a RETIRED skill name for this case"
    )
    skills_root = sandbox.home / ".claude" / "skills"
    retired_dir = skills_root / RETIRED_SKILL_NAME
    retired_content = "# retired skill\n"
    _write_managed_skill(
        retired_dir,
        RETIRED_SKILL_NAME,
        retired_content,
        hashlib.sha256(retired_content.encode("utf-8")).hexdigest(),
    )
    edited_dir = skills_root / "edited-skill"
    _write_managed_skill(edited_dir, "edited-skill", "# edited\n", "0" * 64)
    unmanaged_dir = skills_root / "unmanaged-skill"
    unmanaged_dir.mkdir(parents=True)
    (unmanaged_dir / "SKILL.md").write_text("# mine\n", encoding="utf-8")

    warnings: list[str] = []
    _run_seam(tmp_path, _build_dependencies(warnings))

    assert not retired_dir.exists(), (
        "a retired Ralph-managed skill must be pruned from the user-global root"
    )
    assert edited_dir.exists(), "a user-edited managed skill must survive the migration"
    assert unmanaged_dir.exists(), "a user-owned skill directory must survive the migration"
    assert any("edited-skill" in warning for warning in warnings), (
        f"the preserved entry must be reported; got {warnings!r}"
    )


@pytest.mark.parametrize("value", ["1", "true", "yes", "on"])
def test_run_start_makes_no_updater_call_when_the_env_kill_switch_is_set(
    tmp_path: Path,
    sandbox: _Sandbox,
    updater_probe: _UpdaterProbe,
    transport_probe: list[str],
    monkeypatch: pytest.MonkeyPatch,
    value: str,
) -> None:
    monkeypatch.setenv("RALPH_DISABLE_SKILL_UPDATES", value)
    warnings: list[str] = []

    _run_seam(tmp_path, _build_dependencies(warnings))

    assert not updater_probe.event.wait(timeout=0.25), (
        "the updater must not be called when the kill switch is set"
    )
    assert updater_probe.calls == [], (
        f"the updater must not be called when the kill switch is set; got {updater_probe.calls!r}"
    )
    assert transport_probe == [], (
        f"the kill switch must stop every transport call; got {transport_probe!r}"
    )


@pytest.mark.parametrize("value", ["false", "0", "no", ""])
def test_run_start_calls_the_updater_when_the_env_kill_switch_is_not_truthy(
    tmp_path: Path,
    sandbox: _Sandbox,
    updater_probe: _UpdaterProbe,
    transport_probe: list[str],
    monkeypatch: pytest.MonkeyPatch,
    value: str,
) -> None:
    monkeypatch.setenv("RALPH_DISABLE_SKILL_UPDATES", value)
    warnings: list[str] = []

    _run_seam(tmp_path, _build_dependencies(warnings))

    assert updater_probe.event.wait(timeout=5), (
        f"the updater must run for the non-truthy kill-switch value {value!r}"
    )
    assert len(updater_probe.calls) == 1, (
        f"the updater must run exactly once; got {updater_probe.calls!r}"
    )
    assert updater_probe.calls[0]["enabled"] is True
    assert transport_probe == [], (
        f"the stubbed updater must not reach the transport; got {transport_probe!r}"
    )


def test_run_start_makes_no_updater_call_when_the_config_kill_switch_is_set(
    tmp_path: Path,
    sandbox: _Sandbox,
    updater_probe: _UpdaterProbe,
    transport_probe: list[str],
) -> None:
    (sandbox.config / "ralph-workflow.toml").write_text(
        "[skills]\nuniversal_updates_enabled = false\n",
        encoding="utf-8",
    )
    warnings: list[str] = []

    _run_seam(tmp_path, _build_dependencies(warnings))

    assert not updater_probe.event.wait(timeout=0.25), (
        "the updater must not be called when the config disables updates"
    )
    assert updater_probe.calls == [], (
        f"the updater must not be called when the config disables updates; "
        f"got {updater_probe.calls!r}"
    )
    assert transport_probe == [], (
        f"the kill switch must stop every transport call; got {transport_probe!r}"
    )


def test_run_start_makes_no_updater_call_when_both_kill_switches_are_set(
    tmp_path: Path,
    sandbox: _Sandbox,
    updater_probe: _UpdaterProbe,
    transport_probe: list[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("RALPH_DISABLE_SKILL_UPDATES", "1")
    (sandbox.config / "ralph-workflow.toml").write_text(
        "[skills]\nuniversal_updates_enabled = false\n",
        encoding="utf-8",
    )
    warnings: list[str] = []

    _run_seam(tmp_path, _build_dependencies(warnings))

    assert not updater_probe.event.wait(timeout=0.25), (
        "the updater must not be called when both kill switches are set"
    )
    assert updater_probe.calls == [], (
        f"the updater must not be called when both kill switches are set; "
        f"got {updater_probe.calls!r}"
    )
    assert transport_probe == [], (
        f"the kill switch must stop every transport call; got {transport_probe!r}"
    )


def test_run_start_survives_a_failing_user_global_migration(
    tmp_path: Path,
    updater_probe: _UpdaterProbe,
    monkeypatch: pytest.MonkeyPatch,
) -> None:

    def _raising_home() -> Path:
        raise RuntimeError("simulated home resolution failure")

    monkeypatch.setattr("pathlib.Path.home", _raising_home)
    warnings: list[str] = []
    install_calls: list[Path] = []

    _run_seam(
        tmp_path,
        _build_dependencies(warnings, install_calls=install_calls, need_install=True),
    )

    assert install_calls, "the project-scope install must still run after a migration failure"
    assert any("migration" in warning for warning in warnings), (
        f"the migration failure must be reported; got {warnings!r}"
    )


def _assert_every_companion_is_readable(files: dict[str, dict[str, bytes]]) -> None:
    for root in (upstream.canonical_skills_root(), *upstream.sibling_skills_roots()):
        for skill, companions in files.items():
            for relative, data in companions.items():
                assert (root / skill / relative).read_bytes() == data, (
                    f"{root}: the companion {relative} of {skill} must be readable"
                )


def _expected_upstream_calls(tag: str) -> list[str]:
    calls: list[str] = []
    for source in upstream.UNIVERSAL_UPSTREAMS:
        calls.append(source.api_url)
        calls.append(source.archive_url_template.format(tag=tag))
    return calls


def _make_the_next_attempt_due() -> None:
    cache = upstream.attempt_cache_path()
    cache.parent.mkdir(parents=True, exist_ok=True)
    stamp = (datetime.now(UTC) - timedelta(hours=25)).isoformat()
    cache.write_text(json.dumps({"last_attempt": stamp}), encoding="utf-8")


def test_a_later_run_installs_a_newer_release_without_dirtying_the_users_git_tree(
    tmp_path: Path,
    tmp_git_repo: Path,
    sandbox: _Sandbox,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first_client, first_calls = _client_for("v-test-1", _SKILL_FILES)
    update_done = threading.Event()
    real_update = upstream.maybe_update_universal_skills

    def _tracked_update(*, enabled: bool, now: datetime, http: HttpClient) -> UpdateOutcome:
        try:
            return real_update(enabled=enabled, now=now, http=http)
        finally:
            update_done.set()

    monkeypatch.setattr("ralph.skills.upstream.maybe_update_universal_skills", _tracked_update)
    monkeypatch.setattr("ralph.skills.upstream.default_http_client", lambda: first_client)
    warnings: list[str] = []
    head_before = get_head_sha(tmp_git_repo)

    _run_seam(tmp_path, _build_dependencies(warnings), workspace_root=tmp_git_repo)

    assert update_done.wait(timeout=5), "the universal-skill updater never ran"
    assert first_calls == _expected_upstream_calls("v-test-1")
    _assert_every_companion_is_readable(_SKILL_FILES)
    assert get_head_sha(tmp_git_repo) != head_before, (
        "the seam must commit its own project writes instead of leaving them uncommitted"
    )
    assert has_uncommitted_changes(tmp_git_repo) is False, (
        "the run must leave the user's git tree clean"
    )

    changed = {name: dict(files) for name, files in _SKILL_FILES.items()}
    changed["test-driven-development"]["SKILL.md"] = b"# Test driven development v2\n"
    second_client, second_calls = _client_for("v-test-2", changed)
    monkeypatch.setattr("ralph.skills.upstream.default_http_client", lambda: second_client)
    _make_the_next_attempt_due()
    update_done.clear()

    _run_seam(tmp_path, _build_dependencies(warnings), workspace_root=tmp_git_repo)

    assert update_done.wait(timeout=5), "the universal-skill updater never ran for the new release"
    assert second_calls == _expected_upstream_calls("v-test-2")
    _assert_every_companion_is_readable(changed)
    assert has_uncommitted_changes(tmp_git_repo) is False, (
        "the run that installs the newer release must leave the user's git tree clean"
    )


def _recording_client(event: threading.Event) -> tuple[HttpClient, list[str]]:
    client, calls = _client_for("v-test-1", _SKILL_FILES)

    def _get(url: str, timeout: float) -> HttpResponse:
        event.set()
        return client(url, timeout)

    return _get, calls


def test_the_env_switch_alone_leaves_the_injected_http_client_untouched(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("RALPH_DISABLE_SKILL_UPDATES", "1")
    reached = threading.Event()
    client, calls = _recording_client(reached)
    monkeypatch.setattr("ralph.skills.upstream.default_http_client", lambda: client)
    warnings: list[str] = []

    _run_seam(tmp_path, _build_dependencies(warnings))

    assert not reached.wait(timeout=0.25), (
        "the env switch alone must leave the injected HTTP client untouched"
    )
    assert calls == [], f"the env switch alone must stop every request; got {calls!r}"


def test_the_config_switch_alone_leaves_the_injected_http_client_untouched(
    tmp_path: Path,
    sandbox: _Sandbox,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (sandbox.config / "ralph-workflow.toml").write_text(
        "[skills]\nuniversal_updates_enabled = false\n",
        encoding="utf-8",
    )
    reached = threading.Event()
    client, calls = _recording_client(reached)
    monkeypatch.setattr("ralph.skills.upstream.default_http_client", lambda: client)
    warnings: list[str] = []

    _run_seam(tmp_path, _build_dependencies(warnings))

    assert not reached.wait(timeout=0.25), (
        "the config switch alone must leave the injected HTTP client untouched"
    )
    assert calls == [], f"the config switch alone must stop every request; got {calls!r}"


def test_the_injected_http_client_is_reached_when_no_switch_is_set(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reached = threading.Event()
    finished = threading.Event()
    client, calls = _recording_client(reached)
    monkeypatch.setattr("ralph.skills.upstream.default_http_client", lambda: client)
    real_update = upstream.maybe_update_universal_skills

    def _tracked_update(*, enabled: bool, now: datetime, http: HttpClient) -> UpdateOutcome:
        try:
            return real_update(enabled=enabled, now=now, http=http)
        finally:
            finished.set()

    monkeypatch.setattr("ralph.skills.upstream.maybe_update_universal_skills", _tracked_update)
    warnings: list[str] = []

    _run_seam(tmp_path, _build_dependencies(warnings))

    assert reached.wait(timeout=5), "the injected HTTP client must be reached when no switch is set"
    assert finished.wait(timeout=5), "the universal-skill update never finished"
    assert calls == _expected_upstream_calls("v-test-1")
