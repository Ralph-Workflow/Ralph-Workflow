from __future__ import annotations

from pathlib import Path

import pytest

from ralph.git import remote_push as remote_push_module
from ralph.pipeline import auto_integrate_remote_sync as remote_sync
from ralph.pipeline.auto_integrate_remote_sync import (
    push_target_after_landing,
    remote_sync_enabled,
)
from ralph.pipeline.rebase_state import RebaseState


def _config(
    *,
    enabled: bool = True,
    remote: str = "origin",
):
    from ralph.config.models import UnifiedConfig

    return UnifiedConfig.model_validate(
        {
            "general": {
                "auto_integrate_remote_enabled": enabled,
                "auto_integrate_remote": remote,
            },
        },
    )


def _record(freshness_verdict: str | None = "verified") -> RebaseState:
    return RebaseState(
        last_action="rebased",
        last_target="main",
        fast_forwarded=True,
        freshness_verdict=freshness_verdict,
    )


def test_disabled_push_preserves_record(
    monkeypatch: pytest.MonkeyPatch,
) -> None:

    def unexpected(*args: object, **kwargs: object) -> str:
        raise AssertionError("disabled push contacted a remote")

    monkeypatch.setattr(remote_push_module, "push_branch_to_single_remote", unexpected)
    assert (
        push_target_after_landing(_config(enabled=False), Path("/repo"), "main", _record())
        is not None
    )


def test_successful_push_records_pushed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        remote_push_module,
        "push_branch_to_single_remote",
        lambda *a, **kw: "pushed main to origin",
    )
    record = _record()
    out = push_target_after_landing(_config(), Path("/repo"), "main", record)
    assert out.last_remote_sync == remote_sync.REMOTE_PUSHED
    assert out.last_push == "pushed main to origin"


def test_push_failure_records_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        remote_push_module,
        "push_branch_to_single_remote",
        lambda *a, **kw: "push of main to origin failed: non-fast-forward",
    )
    record = _record()
    out = push_target_after_landing(_config(), Path("/repo"), "main", record)
    assert out.last_remote_sync == remote_sync.REMOTE_PUSH_REJECTED
    assert "non-fast-forward" in (out.last_reason or "")


def test_remote_not_configured_records_skip(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        remote_push_module,
        "push_branch_to_single_remote",
        lambda *a, **kw: "remote 'origin' not configured",
    )
    record = _record()
    out = push_target_after_landing(_config(remote="origin"), Path("/repo"), "main", record)
    assert out.last_remote_sync == remote_sync.REMOTE_NO_REMOTE


def test_push_only_targets_target_branch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[tuple[str, str]] = []

    def fake_push(repo_root, branch, *, remote, timeout_seconds):
        captured.append((branch, remote))
        return f"pushed {branch} to {remote}"

    monkeypatch.setattr(remote_push_module, "push_branch_to_single_remote", fake_push)
    push_target_after_landing(_config(), Path("/repo"), "main", _record())
    push_target_after_landing(_config(), Path("/repo"), "release", _record())
    assert captured == [("main", "origin"), ("release", "origin")]


def test_no_force_refspec_is_added(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured_args: list[str] = []

    def fake_push(repo_root, branch, *, remote, timeout_seconds):
        captured_args.append(branch)
        return f"pushed {branch} to {remote}"

    monkeypatch.setattr(remote_push_module, "push_branch_to_single_remote", fake_push)
    push_target_after_landing(_config(), Path("/repo"), "main", _record())
    assert captured_args == ["main"]
    import inspect

    sig = inspect.signature(remote_push_module.push_branch_to_single_remote)
    assert "force" not in sig.parameters
    assert "force_with_lease" not in sig.parameters


def test_legacy_push_enabled_key_still_triggers_remote_push(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from ralph.config.models import UnifiedConfig

    config = UnifiedConfig.model_validate(
        {
            "general": {
                "auto_integrate_remote_enabled": True,
                "auto_integrate_remote": "origin",
            },
        },
    )
    calls: list[str] = []

    def fake_push(repo_root, branch, *, remote, timeout_seconds):
        calls.append(branch)
        return f"pushed {branch} to {remote}"

    monkeypatch.setattr(remote_push_module, "push_branch_to_single_remote", fake_push)
    record = _record()
    out = push_target_after_landing(config, Path("/repo"), "main", record)
    assert "main" in calls
    assert out.last_remote_sync == remote_sync.REMOTE_PUSHED


def test_legacy_push_does_not_fan_out_to_other_remotes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured_remotes: list[str] = []

    def fake_push(repo_root, branch, *, remote, timeout_seconds):
        captured_remotes.append(remote)
        return f"pushed {branch} to {remote}"

    monkeypatch.setattr(remote_push_module, "push_branch_to_single_remote", fake_push)
    from ralph.config.models import UnifiedConfig

    config = UnifiedConfig.model_validate(
        {
            "general": {
                "auto_integrate_remote_enabled": True,
                "auto_integrate_remote": "upstream",
            },
        },
    )
    record = _record()
    push_target_after_landing(config, Path("/repo"), "main", record)
    assert captured_remotes == ["upstream"]


def test_remote_sync_disabled_no_remote_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:

    def unexpected(*args: object, **kwargs: object) -> str:
        raise AssertionError("disabled push called remote_push")

    monkeypatch.setattr(remote_push_module, "push_branch_to_single_remote", unexpected)
    config = _config(enabled=False)
    record = _record()
    out = push_target_after_landing(config, Path("/repo"), "main", record)
    assert out.last_push is None or out.last_push == record.last_push


def test_remote_sync_helper_returns_correct_constants() -> None:
    assert remote_sync.REMOTE_PUSHED == "pushed"
    assert remote_sync.REMOTE_PUSH_REJECTED == "push rejected"
    assert remote_sync.REMOTE_NO_REMOTE == "no remote"
    assert remote_sync.REMOTE_PULL_FAILED == "pull failed"


def test_remote_sync_enabled_helper_checks_flag() -> None:
    cfg_on = _config(enabled=True)
    cfg_off = _config(enabled=False)
    assert remote_sync_enabled(cfg_on) is True
    assert remote_sync_enabled(cfg_off) is False
    assert remote_sync_enabled(None) is False


def test_push_result_classifies_creation_and_failures_without_summary_parsing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(remote_push_module, "_list_remotes", lambda _root: ["origin"])
    monkeypatch.setattr(remote_push_module, "branch_sha", lambda _root, _name: None)
    monkeypatch.setattr(remote_push_module, "_remote_ref_sha", lambda _root, _r, _b: None)
    monkeypatch.setattr(
        remote_push_module,
        "_push_to_remote",
        lambda *_a, **_kw: (False, "remote: Permission denied (publickey)"),
    )

    result = remote_push_module.push_branch_to_single_remote(
        Path("/repo"), "main", remote="origin", timeout_seconds=5.0
    )

    assert result.status == remote_push_module.PushStatus.AUTH_FAILED
    assert result.remote == "origin"
    assert result.branch == "main"


def test_remote_target_name_default_is_origin() -> None:
    cfg = _config(enabled=True, remote="upstream")
    assert remote_sync.remote_target_name(cfg) == "upstream"
    cfg_default = _config(enabled=True)
    cfg_default_dict = cfg_default.model_dump()
    cfg_default_dict["general"]["auto_integrate_remote"] = "origin"
    from ralph.config.models import UnifiedConfig

    cfg2 = UnifiedConfig.model_validate(cfg_default_dict)
    assert remote_sync.remote_target_name(cfg2) == "origin"


@pytest.mark.parametrize(
    ("status", "outcome"),
    [
        (remote_push_module.PushStatus.TIMEOUT, remote_sync.REMOTE_TIMEOUT),
        (remote_push_module.PushStatus.AUTH_FAILED, remote_sync.REMOTE_AUTH_FAILED),
        (remote_push_module.PushStatus.HOOK_REJECTED, remote_sync.REMOTE_REJECTED_BY_HOOK),
        (remote_push_module.PushStatus.UNREACHABLE, remote_sync.REMOTE_REMOTE_UNREACHABLE),
    ],
)
def test_push_failure_preserves_typed_status_without_non_fast_forward(
    monkeypatch: pytest.MonkeyPatch,
    status: remote_push_module.PushStatus,
    outcome: str,
) -> None:
    monkeypatch.setattr(
        remote_push_module,
        "push_branch_to_single_remote",
        lambda *_a, **_kw: remote_push_module.PushResult(status, "origin", "main", status.value),
    )
    result = push_target_after_landing(_config(), Path("/repo"), "main", _record())
    assert result.last_push_status == status.value
    assert result.last_remote_sync == outcome
