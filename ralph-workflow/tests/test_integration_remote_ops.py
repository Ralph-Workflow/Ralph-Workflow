"""Remote-tier injection seam for the integration pipeline (PA-002).

The remote tier is driven through the injected
:class:`~ralph.pipeline.integration_remote_actions.RemoteOps` protocol,
so a fault-injecting double can simulate an unreachable remote, a
non-fast-forward rejection, and a remote that advanced underneath the
caller -- with no network, no subprocess, and no clock.

Every test here is a black-box drive of a PUBLIC publication entry
point (:func:`reconcile_remote_publication` /
:func:`push_target_after_landing`) or of the protocol surface itself.
The contract under test:

* the production adapter really implements the protocol (the client is
  ONE implementation of the remote tier);
* an injected double receives the CONFIGURED remote/target and is the
  only remote the publication path talks to;
* the production adapter is fail-open: a failing remote read becomes
  ``REFRESH_UNREACHABLE`` and a failing push an ``UNREACHABLE``
  result -- never a proven negative;
* a remote outage is an UNKNOWN environmental reading: the publication
  obligation is DEFERRED, never terminally blocked, terminal success
  stays ineligible, and the local landing is never reverted;
* a LOCAL timeout is reported as a timeout, never as a remote outage --
  the remote is not blamed for a local failure.

Portfolio decision: KEEP. The nearest existing coverage
(``tests/test_auto_integrate_remote_sync_convergence.py``) drives the
same entry points through an ad-hoc ``remote_client`` double; this file
adds the distinct fault sensitivity the protocol seam introduces
(protocol conformance of the production adapter, and the UNKNOWN /
no-blame contract asserted through the injected seam) without
duplicating the convergence scenarios.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ralph.config.models import UnifiedConfig
from ralph.git import remote_push as remote_push_module
from ralph.git.remote_push import PushResult, PushStatus
from ralph.pipeline import auto_integrate_remote_push as push_mod
from ralph.pipeline import auto_integrate_remote_sync as remote_sync
from ralph.pipeline._publication_state_machine import PublicationDecision
from ralph.pipeline._terminal_authority import is_terminal_success_eligible
from ralph.pipeline.auto_integrate_remote_sync import (
    push_target_after_landing,
    reconcile_remote_publication,
)
from ralph.pipeline.auto_integrate_sync import REFRESH_UNREACHABLE
from ralph.pipeline.integration_remote_actions import RemoteOps, default_remote_ops
from ralph.pipeline.rebase_state import RebaseState

TARGET = "release"
REMOTE = "tacos"
LOCAL_SHA = "local-sha"
REMOTE_SHA = "remote-sha"

#: A repository path that does not exist. The tests drive the injected
#: seam, so the production adapter is never asked to reach a real
#: repository -- this path only has to be inert.
REPO_ROOT = Path("/ralph-remote-ops-no-such-repository")


def _config() -> UnifiedConfig:
    """Configured (non-default) remote and target."""
    return UnifiedConfig.model_validate(
        {
            "general": {
                "auto_integrate_remote_enabled": True,
                "auto_integrate_remote": REMOTE,
            }
        }
    )


class _FakeRemoteOps:
    """Scriptable remote-ops double: no network, no subprocess, no clock."""

    def __init__(
        self,
        *,
        refresh_outcomes: list[str],
        remote_shas: list[str | None],
        push_statuses: list[PushStatus] | None = None,
        push_error: BaseException | None = None,
    ) -> None:
        self._refresh_outcomes = list(refresh_outcomes)
        self._remote_shas = list(remote_shas)
        self._push_statuses = (
            list(push_statuses) if push_statuses is not None else [PushStatus.PUSHED]
        )
        self._push_error = push_error
        self.refresh_calls: list[tuple[str, str]] = []
        self.observe_calls: list[tuple[str, str]] = []
        self.push_calls: list[tuple[str, str]] = []

    def refresh_target(
        self,
        repo_root: Path,
        target: str,
        *,
        remote: str,
        timeout_seconds: float,
    ) -> str:
        """Report the scripted freshness outcome for the next observation."""
        self.refresh_calls.append((target, remote))
        index = min(len(self.refresh_calls) - 1, len(self._refresh_outcomes) - 1)
        return self._refresh_outcomes[index]

    def observe_remote_sha(self, repo_root: Path, target: str, *, remote: str) -> str | None:
        """Report the scripted remote-tracking SHA for the next read."""
        self.observe_calls.append((target, remote))
        index = min(len(self.observe_calls) - 1, len(self._remote_shas) - 1)
        return self._remote_shas[index]

    def push_branch(
        self,
        repo_root: Path,
        branch: str,
        *,
        remote: str,
        timeout_seconds: float,
    ) -> PushResult:
        """Report the scripted push outcome, or raise the scripted failure."""
        self.push_calls.append((branch, remote))
        if self._push_error is not None:
            raise self._push_error
        index = min(len(self.push_calls) - 1, len(self._push_statuses) - 1)
        status = self._push_statuses[index]
        return PushResult(status, remote, branch, status.value)


@pytest.fixture(autouse=True)
def _isolate_git_reads(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stub the local git reads so no test touches a real repository."""

    def fake_branch_sha(_root: Path, _ref: str) -> str:
        return LOCAL_SHA

    def fake_is_ancestor(_root: Path, ancestor: str, descendant: str) -> bool:
        # The local target is strictly ahead of every remote SHA.
        return ancestor != LOCAL_SHA and descendant == LOCAL_SHA

    def fake_rebase_in_progress(_root: Path) -> bool:
        return False

    monkeypatch.setattr(remote_sync, "branch_sha", fake_branch_sha)
    monkeypatch.setattr(remote_sync, "is_ancestor", fake_is_ancestor)
    monkeypatch.setattr(push_mod, "branch_sha", fake_branch_sha)
    monkeypatch.setattr(push_mod, "rebase_in_progress", fake_rebase_in_progress)


def test_production_remote_ops_implements_the_protocol() -> None:
    """The production adapter is a real implementation of ``RemoteOps``."""
    assert isinstance(default_remote_ops(), RemoteOps)


def test_injected_remote_ops_is_the_only_remote_the_publication_path_talks_to() -> None:
    """Every remote call of the publication path goes through the seam."""
    fake = _FakeRemoteOps(
        refresh_outcomes=["refreshed"],
        remote_shas=[REMOTE_SHA],
        push_statuses=[PushStatus.PUSHED],
    )
    out = reconcile_remote_publication(
        _config(),
        REPO_ROOT,
        TARGET,
        remote_client=fake,
    )
    assert fake.refresh_calls == [(TARGET, REMOTE)]
    assert fake.observe_calls
    assert all(pair == (TARGET, REMOTE) for pair in fake.observe_calls)
    assert fake.push_calls == [(TARGET, REMOTE)]
    assert out.last_remote_sync == remote_sync.REMOTE_PUSHED
    assert out.last_publication_decision == PublicationDecision.PUBLICATION_TERMINAL.value


def test_unreachable_remote_defers_the_publication_without_blocking_it() -> None:
    """A remote outage is an UNKNOWN reading: deferred, never a terminal block."""
    fake = _FakeRemoteOps(
        refresh_outcomes=[REFRESH_UNREACHABLE],
        remote_shas=[None],
    )
    landed = RebaseState(last_target=TARGET, fast_forwarded=True)
    out = reconcile_remote_publication(
        _config(),
        REPO_ROOT,
        TARGET,
        remote_client=fake,
        source_state=landed,
    )
    assert out.last_remote_sync == remote_sync.REMOTE_REMOTE_UNREACHABLE
    assert out.last_publication_decision == PublicationDecision.PUBLISH_DEFERRED_REMOTE.value
    assert out.last_publication_decision != PublicationDecision.PUBLICATION_BLOCKED.value
    assert out.last_publication_decision != PublicationDecision.PUBLICATION_TERMINAL.value
    # The local landing is never reverted by a remote outage.
    assert out.fast_forwarded is True
    # An unknown remote keeps terminal success ineligible.
    assert is_terminal_success_eligible(None, None, out) is False
    assert fake.push_calls == []


def test_non_fast_forward_rejection_defers_the_publication_for_reconciliation() -> None:
    """A remote that advanced rejects the push; the obligation stays open."""
    fake = _FakeRemoteOps(
        refresh_outcomes=["refreshed"],
        remote_shas=[REMOTE_SHA],
        push_statuses=[PushStatus.NON_FAST_FORWARD],
    )
    out = push_target_after_landing(
        _config(),
        REPO_ROOT,
        TARGET,
        RebaseState(last_target=TARGET, fast_forwarded=True),
        remote=REMOTE,
        remote_client=fake,
    )
    assert fake.push_calls == [(TARGET, REMOTE)]
    assert out.last_push_status == PushStatus.NON_FAST_FORWARD.value
    assert out.last_remote_sync == remote_sync.REMOTE_PUSH_REJECTED
    assert out.last_publication_decision == PublicationDecision.PUBLISH_DEFERRED_FRESHNESS.value
    assert out.last_publication_decision != PublicationDecision.PUBLICATION_TERMINAL.value


def test_local_timeout_is_not_reported_as_a_remote_outage() -> None:
    """A local timeout is a timeout -- the remote is never blamed for it."""
    fake = _FakeRemoteOps(
        refresh_outcomes=["refreshed"],
        remote_shas=[REMOTE_SHA],
        push_error=TimeoutError("local push budget exhausted"),
    )
    out = push_target_after_landing(
        _config(),
        REPO_ROOT,
        TARGET,
        RebaseState(last_target=TARGET, fast_forwarded=True),
        remote=REMOTE,
        remote_client=fake,
    )
    assert out.last_push_status == PushStatus.TIMEOUT.value
    assert out.last_remote_sync == remote_sync.REMOTE_TIMEOUT
    assert out.last_remote_sync != remote_sync.REMOTE_REMOTE_UNREACHABLE
    assert out.last_publication_decision == PublicationDecision.PUBLISH_DEFERRED_REMOTE.value
    assert out.last_publication_decision != PublicationDecision.PUBLICATION_BLOCKED.value


def test_remote_advance_between_observation_and_push_skips_the_stale_push() -> None:
    """A remote that advanced to the local tip makes the push obsolete."""
    fake = _FakeRemoteOps(
        refresh_outcomes=["refreshed"],
        remote_shas=[REMOTE_SHA, LOCAL_SHA],
    )
    out = reconcile_remote_publication(
        _config(),
        REPO_ROOT,
        TARGET,
        remote_client=fake,
    )
    assert fake.push_calls == []
    assert out.last_remote_sync == remote_sync.REMOTE_ALREADY_CURRENT
    assert out.last_publication_decision == PublicationDecision.PUBLICATION_TERMINAL.value


def test_injected_remote_keeps_the_publication_path_off_the_raw_fetch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A client that answers every probe keeps the path off the raw fetch."""
    raw_fetches: list[str] = []

    def _spy_fetch(*_args: object, **_kwargs: object) -> str:
        raw_fetches.append("fetch")
        return "refreshed"

    monkeypatch.setattr(remote_sync, "refresh_target_from_remote", _spy_fetch)
    fake = _FakeRemoteOps(
        refresh_outcomes=["refreshed"],
        remote_shas=[REMOTE_SHA],
        push_statuses=[PushStatus.PUSHED],
    )
    out = reconcile_remote_publication(
        _config(),
        REPO_ROOT,
        TARGET,
        remote_client=fake,
    )
    assert raw_fetches == []
    assert out.last_remote_sync == remote_sync.REMOTE_PUSHED


def test_production_adapter_reports_an_unreadable_remote_as_unknown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failing remote read is UNKNOWN, never a proven negative."""

    def _boom(*_args: object, **_kwargs: object) -> str:
        raise OSError("remote unreachable")

    monkeypatch.setattr(remote_sync, "refresh_target_from_remote", _boom)
    outcome = default_remote_ops().refresh_target(
        REPO_ROOT,
        TARGET,
        remote=REMOTE,
        timeout_seconds=1.0,
    )
    assert outcome == REFRESH_UNREACHABLE


def test_production_adapter_reports_a_failed_push_as_unreachable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failing push is an UNKNOWN remote reading, not a proven negative."""

    def _boom(*_args: object, **_kwargs: object) -> PushResult:
        raise OSError("remote unreachable")

    monkeypatch.setattr(remote_push_module, "push_branch_to_single_remote", _boom)
    result = default_remote_ops().push_branch(
        REPO_ROOT,
        TARGET,
        remote=REMOTE,
        timeout_seconds=1.0,
    )
    assert result.status is PushStatus.UNREACHABLE
    assert result.remote == REMOTE
    assert result.branch == TARGET
