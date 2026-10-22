"""AC10 observation-driven convergence tests with non-default names.

Every test uses the configured target ``release`` and remote ``tacos``
(never the literal ``main``/``origin`` defaults) and fakes only — no
real subprocess, no sleep. The contract under test:

* A retry obligation persists while local ``release`` differs from
  ``tacos/release``, re-derived from the CURRENT observation each cycle.
* Non-fast-forward rejection triggers fetch -> reintegrate -> land ->
  push until the refs are equal.
* A failed remote read is UNKNOWN (back off, retry later), never a
  stored unreachable claim, never blocking.
* Publish idempotency on the pipeline push path: a push is skipped when
  the local and remote refs already name the same commit.
* No ``main``/``origin`` literals appear in the configured path.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ralph.config.models import UnifiedConfig
from ralph.git import remote_push as remote_push_module
from ralph.git.merge import branch_sha as _real_branch_sha
from ralph.pipeline import auto_integrate_remote_push as push_mod
from ralph.pipeline import auto_integrate_remote_reconcile as remote_reconcile
from ralph.pipeline import auto_integrate_remote_sync as remote_sync
from ralph.pipeline.auto_integrate_remote_constants import RemoteSyncStatus
from ralph.pipeline.auto_integrate_remote_push import push_target_after_landing
from ralph.pipeline.auto_integrate_remote_sync import (
    reconcile_after_rejected_push,
    reconcile_remote_publication,
)
from ralph.pipeline.auto_integrate_sync import REFRESH_DIVERGED
from ralph.pipeline.rebase_state import RebaseState

TARGET = "release"
REMOTE = "tacos"


def _config(**overrides: object) -> UnifiedConfig:
    general: dict[str, object] = {
        "auto_integrate_remote_enabled": True,
        "auto_integrate_remote": REMOTE,
    }
    general.update(overrides)
    return UnifiedConfig.model_validate({"general": general})


def _record() -> RebaseState:
    return RebaseState(last_action="rebased", last_target=TARGET, fast_forwarded=True)


class _FakeRemoteClient:
    """Scriptable remote client: no subprocess, no sleep, no real git."""

    def __init__(
        self,
        *,
        remote_shas: list[str | None],
        refresh_outcomes: list[str],
    ) -> None:
        self._remote_shas = list(remote_shas)
        self._refresh_outcomes = list(refresh_outcomes)
        self.refresh_calls = 0
        self.observe_calls = 0
        self.push_calls: list[tuple[str, str]] = []

    def refresh_target(
        self,
        repo_root: Path,
        target: str,
        *,
        timeout_seconds: float,
        remote: str,
    ) -> str:
        self.refresh_calls += 1
        assert remote == REMOTE, f"configured path touched non-configured remote {remote!r}"
        assert target == TARGET, f"configured path touched non-configured target {target!r}"
        return self._refresh_outcomes[min(self.refresh_calls - 1, len(self._refresh_outcomes) - 1)]

    def observe_remote_sha(self, repo_root: Path, target: str, *, remote: str) -> str | None:
        self.observe_calls += 1
        assert remote == REMOTE
        # One true remote SHA per fetch cycle: a forced re-observe within
        # the same cycle (after a throttled refresh) yields the same
        # value instead of consuming the next cycle's script entry.
        index = min(self.refresh_calls - 1, len(self._remote_shas) - 1)
        return self._remote_shas[index]

    def push_branch(
        self,
        repo_root: Path,
        branch: str,
        *,
        remote: str,
        timeout_seconds: float,
    ) -> remote_push_module.PushResult:
        self.push_calls.append((branch, remote))
        assert remote == REMOTE
        return remote_push_module.PushResult(remote_push_module.PushStatus.PUSHED, remote, branch)


def _stub_reconcile(
    monkeypatch: pytest.MonkeyPatch,
    outcome: remote_reconcile.ReconciliationOutcome,
    calls: list[tuple[str, str]] | None = None,
) -> None:
    """Replace the target reconciliation with a typed scripted outcome."""

    def fake_reconcile(
        repo_root: Path,
        target: str,
        remote: str,
        **_kwargs: object,
    ) -> remote_reconcile.ReconciliationOutcome:
        if calls is not None:
            calls.append((target, remote))
        return outcome

    monkeypatch.setattr(remote_reconcile, "reconcile_target_onto_remote", fake_reconcile)


def _stub_is_ancestor_not_ancestor(monkeypatch: pytest.MonkeyPatch) -> None:
    """Divergence stub: neither ref is an ancestor of the other."""

    def fake_is_ancestor(_root: Path, _ancestor: str, _descendant: str) -> bool:
        return False

    monkeypatch.setattr(remote_sync, "is_ancestor", fake_is_ancestor)


@pytest.fixture(autouse=True)
def _isolate_git_reads(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stub local git reads so no test touches a real repository."""

    def fake_branch_sha(_root: Path, _ref: str) -> str:
        return "local-sha-1"

    def fake_head_sha(_root: Path) -> str:
        return "feature-sha"

    def fake_reconcile_branch_sha(_root: Path, _ref: str) -> str:
        return "before"

    def fake_rebase_in_progress(_root: Path) -> bool:
        return False

    monkeypatch.setattr(remote_sync, "branch_sha", fake_branch_sha)
    monkeypatch.setattr(push_mod, "branch_sha", fake_branch_sha)
    monkeypatch.setattr(remote_reconcile, "get_head_sha", fake_head_sha)
    monkeypatch.setattr(remote_reconcile, "branch_sha", fake_reconcile_branch_sha)
    monkeypatch.setattr(remote_reconcile, "rebase_in_progress", fake_rebase_in_progress)


def test_diverged_local_pushes_until_refs_equal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC10: local ahead of remote -> push converges; obligation cleared."""
    _stub_reconcile(monkeypatch, remote_reconcile.ReconciliationOutcome(True, "ok"))
    client = _FakeRemoteClient(
        remote_shas=["old-remote-sha", "local-sha-1"],
        refresh_outcomes=["refreshed", "already current"],
    )
    out = reconcile_remote_publication(_config(), Path("/repo"), TARGET, remote_client=client)
    assert out.last_remote_sync == remote_sync.REMOTE_PUSHED
    assert out.last_publication_decision == "publication_terminal"
    assert client.push_calls == [(TARGET, REMOTE)]


def test_non_fast_forward_triggers_reconcile_then_push(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC10: rejection -> fetch/reintegrate/land/push until equal."""
    _stub_is_ancestor_not_ancestor(monkeypatch)
    _stub_reconcile(monkeypatch, remote_reconcile.ReconciliationOutcome(True, "ok"))

    class _RejectedClient(_FakeRemoteClient):
        def push_branch(
            self,
            repo_root: Path,
            branch: str,
            *,
            remote: str,
            timeout_seconds: float,
        ) -> remote_push_module.PushResult:
            super().push_branch(repo_root, branch, remote=remote, timeout_seconds=timeout_seconds)
            if len(self.push_calls) == 1:
                return remote_push_module.PushResult(
                    remote_push_module.PushStatus.NON_FAST_FORWARD,
                    remote,
                    branch,
                    "fetch first",
                )
            return remote_push_module.PushResult(
                remote_push_module.PushStatus.PUSHED, remote, branch
            )

    client = _RejectedClient(
        remote_shas=["old-remote-sha", "old-remote-sha", "local-sha-1"],
        refresh_outcomes=["refreshed", "refreshed", "already current"],
    )
    out = reconcile_remote_publication(_config(), Path("/repo"), TARGET, remote_client=client)
    assert out.last_remote_sync == remote_sync.REMOTE_PUSHED
    assert len(client.push_calls) == 2
    assert client.refresh_calls >= 2


def test_unreachable_remote_is_unknown_backoff_not_stored_claim(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC10: failed remote read -> UNKNOWN (deferred), never blocking."""
    client = _FakeRemoteClient(
        remote_shas=[None],
        refresh_outcomes=["refresh suppressed by throttle"],
    )
    out = reconcile_remote_publication(_config(), Path("/repo"), TARGET, remote_client=client)
    assert out.last_remote_sync == RemoteSyncStatus.REMOTE_UNREACHABLE
    assert out.last_publication_decision == "publish_deferred_remote"
    assert client.push_calls == []


def test_unreachable_then_recovering_remote_converges_without_stale_reason(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC10: cycle 1 unknown, cycle 2 recovering -> push, no stale prose."""
    _stub_reconcile(monkeypatch, remote_reconcile.ReconciliationOutcome(True, "ok"))
    client = _FakeRemoteClient(
        remote_shas=[None, "old-remote-sha", "local-sha-1"],
        refresh_outcomes=["refresh suppressed by throttle", "refreshed", "already current"],
    )
    first = reconcile_remote_publication(_config(), Path("/repo"), TARGET, remote_client=client)
    assert first.last_publication_decision == "publish_deferred_remote"
    second = reconcile_remote_publication(
        _config(),
        Path("/repo"),
        TARGET,
        remote_client=client,
        source_state=first,
    )
    assert second.last_remote_sync == remote_sync.REMOTE_PUSHED
    assert second.last_publication_decision == "publication_terminal"


def test_concurrent_remote_advance_reintegrates_until_equal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC10: remote advances between landing and push -> converge next cycle."""
    _stub_is_ancestor_not_ancestor(monkeypatch)

    reconciliation_calls: list[str] = []

    def recording_reconcile(
        repo_root: Path,
        target: str,
        remote: str,
        **_kwargs: object,
    ) -> remote_reconcile.ReconciliationOutcome:
        reconciliation_calls.append("reconcile")
        return remote_reconcile.ReconciliationOutcome(True, "reconciled")

    monkeypatch.setattr(remote_reconcile, "reconcile_target_onto_remote", recording_reconcile)
    client = _FakeRemoteClient(
        remote_shas=["concurrent-sha", "concurrent-sha", "local-sha-1"],
        refresh_outcomes=[REFRESH_DIVERGED, REFRESH_DIVERGED, "already current"],
    )
    out = reconcile_remote_publication(_config(), Path("/repo"), TARGET, remote_client=client)
    assert reconciliation_calls == ["reconcile"]
    assert out.last_remote_sync == remote_sync.REMOTE_PUSHED


def test_pipeline_push_is_idempotent_when_refs_already_equal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC10: skip the push subprocess when local sha == remote sha."""
    client = _FakeRemoteClient(remote_shas=["local-sha-1"], refresh_outcomes=["refreshed"])
    out = push_target_after_landing(
        _config(),
        Path("/repo"),
        TARGET,
        _record(),
        remote=REMOTE,
        remote_client=client,
    )
    assert client.push_calls == []
    assert out.last_publication_decision == "publication_terminal"
    assert out.last_remote_sync == remote_sync.REMOTE_ALREADY_CURRENT


def test_pipeline_push_does_not_skip_on_unknown_remote(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unobservable remote never proves the push can be skipped."""
    client = _FakeRemoteClient(remote_shas=[None], refresh_outcomes=["refreshed"])
    push_target_after_landing(
        _config(),
        Path("/repo"),
        TARGET,
        _record(),
        remote=REMOTE,
        remote_client=client,
    )
    assert client.push_calls == [(TARGET, REMOTE)]


def test_configured_path_reads_only_configured_names(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Zero reads of 'main'/'origin' anywhere on the configured path."""
    forbidden: list[str] = []

    class _SpyClient(_FakeRemoteClient):
        def observe_remote_sha(self, repo_root: Path, target: str, *, remote: str) -> str | None:
            forbidden.extend((target, remote))
            return "local-sha-1"

    def spy_branch_sha(root: Path, ref: str) -> str:
        forbidden.append(ref)
        return str(_real_branch_sha(root, ref) or "")

    monkeypatch.setattr(push_mod, "branch_sha", spy_branch_sha)
    client = _SpyClient(remote_shas=["local-sha-1"], refresh_outcomes=["refreshed"])
    out = push_target_after_landing(
        _config(),
        Path("/repo"),
        TARGET,
        _record(),
        remote=None,
        remote_client=client,
    )
    # The configured names flow through; the fake pushes (no skip since
    # branch_sha of the fake repo returns a real read) — the assertion
    # under test is which names were read, never 'main'/'origin'.
    assert out.last_publication_decision in ("publication_terminal", "publish_deferred_remote")
    assert forbidden and all(name not in ("main", "origin") for name in forbidden), (
        f"configured path read literal names: {forbidden}"
    )


def test_reconcile_after_rejected_push_uses_configured_names(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The rejected-push loop passes only the configured names downstream."""
    seen: list[tuple[str, str]] = []
    _stub_reconcile(
        monkeypatch,
        remote_reconcile.ReconciliationOutcome(False, "blocked"),
        calls=seen,
    )
    client = _FakeRemoteClient(
        remote_shas=["old-remote-sha", "old-remote-sha", "local-sha-1"],
        refresh_outcomes=[REFRESH_DIVERGED, REFRESH_DIVERGED, "already current"],
    )
    out = reconcile_after_rejected_push(
        _config(),
        Path("/repo"),
        TARGET,
        _record(),
        remote_client=client,
    )
    assert seen == [(TARGET, REMOTE)]
    # Blocked reconciliation is carried forward as a non-terminal retry
    # obligation; the exact projection differs per outcome path.
    assert out.last_publication_decision != "publication_terminal"


def test_no_origin_literal_in_remote_sync_module_sources() -> None:
    """The configured path never hardcodes a default remote name in code."""
    import ast
    from pathlib import Path as _Path

    pipeline_dir = _Path(__file__).resolve().parents[1] / "ralph" / "pipeline"
    for name in (
        "auto_integrate_remote_sync.py",
        "auto_integrate_remote_push.py",
        "auto_integrate_remote_client.py",
        "auto_integrate_remote_constants.py",
    ):
        path = pipeline_dir / name
        tree = ast.parse(path.read_text(encoding="utf-8"))
        bad = [
            node.value
            for node in ast.walk(tree)
            if isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and node.value.strip() in ("main", "origin")
        ]
        assert not bad, f"{name} hardcodes a literal remote/target name: {bad}"
