"""The closed registry of auto-rebase skip reasons, and its drift guard.

The product rule (Axis 2) is that the automatic rebase is ALWAYS the
first attempt of an integration, and that the ONLY ways an attempt may
skip it are a closed, registered set of named reasons:

* ``merge_commits_in_range`` -- a merge commit sits in the replay range;
* ``root_commits_in_range`` -- a root commit sits in the replay range;
* ``all_empty_replay`` -- the replay has nothing to replay;
* ``case_folding_collision`` -- two replay paths collide under case folding;
* ``retry_after_merge_commit`` -- the previous attempt produced a merge commit.

Every skip must NAME its reason from the CURRENT observation and
RECORD it, so the operator can tell a prescribed skip from a fallback
that ran after the rebase failed.

This module is the drift guard for that contract. It fails when

* the registry stops being the product's closed set (a silent expansion);
* a reason is used in production without being registered;
* a registered reason has no production use site (a dead registry entry);
* an entry path stops being rebase-first.

The guard is AST-based and reads the production modules only -- no
repository, no subprocess, no clock.
"""

from __future__ import annotations

import ast
import importlib
import inspect
from collections import deque
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING

import pytest

from ralph.config.models import UnifiedConfig
from ralph.git.branch_relation import BranchRelation
from ralph.git.merge import MergeResult
from ralph.git.rebase.rebase import RebaseResult, RebaseSuccess
from ralph.git.rebase.rebase_conflicts import RebaseConflicts
from ralph.pipeline import auto_integrate_planning as planning
from ralph.pipeline.auto_integrate import auto_integrate_after_commit
from ralph.pipeline.auto_integrate_conflict_budget import ConflictIdentity
from ralph.pipeline.auto_integrate_rebase_merge import (
    SKIP_ALL_EMPTY_REPLAY,
    SKIP_CASE_FOLDING_COLLISION,
    SKIP_MERGE_COMMITS_IN_RANGE,
    SKIP_REASON_REGISTRY,
    SKIP_RETRY_AFTER_MERGE_COMMIT,
    SKIP_ROOT_COMMITS_IN_RANGE,
    is_registered_skip_reason,
    registered_skip_slugs,
    skip_reason_description,
    skip_reason_observation,
    skip_reason_slug,
    skip_reason_text,
)
from ralph.pipeline.auto_integrate_record import (
    RecordReadOutcome,
    RecordReadOutcomeDisposition,
)
from ralph.pipeline.auto_integrate_resolution_state import ContainmentVerdict
from ralph.pipeline.integration_actions import install_executors, reset_executors
from ralph.pipeline.integration_resolution import TreeReadinessAssessment
from ralph.pipeline.rebase_state import RebaseState
from ralph.workspace.scope import WorkspaceScope

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

    from ralph.pipeline._auto_integrate_reclaim import ReclaimResult
    from ralph.pipeline.conflict_resolution.rebase_loop import RebaseStopResolver
    from ralph.pipeline.conflict_resolution.session import ResolutionSession

pytestmark = [pytest.mark.timeout_seconds(10)]

_TARGET = "main"
_FEATURE_SHA = "feature-sha-1234"
_TARGET_SHA = "target-sha-5678"

#: The product's closed set of allowed rebase skips. A sixth reason is a
#: product decision: it must be registered AND reflected here, so an
#: expansion cannot happen silently.
_PRODUCT_ALLOWED_SKIP_SLUGS = frozenset(
    {
        "merge_commits_in_range",
        "root_commits_in_range",
        "all_empty_replay",
        "case_folding_collision",
        "retry_after_merge_commit",
    }
)

#: The production modules that can decide to skip the automatic rebase.
_SKIP_DECISION_MODULES = (
    "ralph.pipeline.auto_integrate_rebase_merge",
    "ralph.pipeline.auto_integrate",
    "ralph.pipeline.auto_integrate_planning",
)

#: Cap on the mutations one trace recorder remembers. The recorder keeps
#: the ORDER of the attempt, and the cap keeps it from growing with the
#: number of mutations an attempt performs.
_MAX_RECORDED_MUTATIONS = 64


def _no_sleep(_delay: float) -> None:
    """The wait seam: the back-off is proven, never waited for."""
    return None


def _no_jitter() -> float:
    """A constant jitter source, so the back-off schedule is deterministic."""
    return 0.0


# ---------------------------------------------------------------------------
# The registry itself
# ---------------------------------------------------------------------------


def test_the_registry_is_exactly_the_product_allowed_skip_set() -> None:
    """The closed set is the product's five reasons -- no more, no fewer."""
    assert registered_skip_slugs() == _PRODUCT_ALLOWED_SKIP_SLUGS


def test_every_registered_reason_names_its_observation_and_description() -> None:
    """Each entry says WHICH observation justifies it and what it means."""
    for slug in registered_skip_slugs():
        assert skip_reason_observation(slug).strip(), f"{slug} names no observation"
        assert skip_reason_description(slug).strip(), f"{slug} names no description"


def test_an_unregistered_reason_is_rejected() -> None:
    """The registry is closed at runtime, not only by convention."""
    with pytest.raises(ValueError, match="unregistered auto-rebase skip reason"):
        skip_reason_text("not_a_registered_reason")


def test_the_recorded_reason_text_names_its_slug() -> None:
    """Every recorded reason starts with the slug, so it can be read back."""
    for slug in registered_skip_slugs():
        text = skip_reason_text(slug)
        assert text.startswith(f"{slug}: ")
        assert skip_reason_slug(text) == slug
        assert is_registered_skip_reason(text)


def test_an_unnamed_reason_is_not_mistaken_for_a_registered_one() -> None:
    """An ordinary skip reason never claims a registered slug."""
    assert skip_reason_slug("detached HEAD") is None
    assert not is_registered_skip_reason("detached HEAD")


# ---------------------------------------------------------------------------
# The drift guard
# ---------------------------------------------------------------------------


def _module_source(module_name: str) -> str:
    """Return the source of a production module."""
    return inspect.getsource(importlib.import_module(module_name))


def _module_constants(module_name: str) -> dict[str, str]:
    """Return the module-level string constants of a production module."""
    constants: dict[str, str] = {}
    tree = ast.parse(_module_source(module_name))
    for node in tree.body:
        if isinstance(node, ast.Assign):
            targets = list(node.targets)
            value = node.value
        elif isinstance(node, ast.AnnAssign):
            targets = [node.target]
            value = node.value
        else:
            continue
        if not (isinstance(value, ast.Constant) and isinstance(value.value, str)):
            continue
        for target in targets:
            if isinstance(target, ast.Name):
                constants[target.id] = value.value
    return constants


def _call_name(node: ast.Call) -> str:
    """Return the called name of a call expression."""
    func = node.func
    if isinstance(func, ast.Attribute):
        return func.attr
    if isinstance(func, ast.Name):
        return func.id
    return ""


def _is_reason_constant(name: str) -> bool:
    """Whether a module-level constant name reads as a skip reason."""
    upper = name.upper()
    return "REASON" in upper or "SKIP" in upper


def test_every_reason_constant_is_registered() -> None:
    """A new reason constant that is not registered fails here.

    This is the load-bearing "complete skip set" proof: production may
    not introduce a reason the closed registry does not know.
    """
    slugs = registered_skip_slugs()
    descriptions = {skip_reason_description(slug) for slug in slugs}
    for module_name in _SKIP_DECISION_MODULES:
        for name, value in _module_constants(module_name).items():
            if not _is_reason_constant(name):
                continue
            assert value in slugs or value in descriptions, (
                f"{module_name}: the reason constant {name!r} holds {value!r}, which is "
                "neither a registered skip slug nor a registered skip description"
            )


def test_every_production_skip_reason_names_a_registered_slug() -> None:
    """Every ``skip_reason_text`` call names a registered slug constant."""
    slugs = registered_skip_slugs()
    for module_name in _SKIP_DECISION_MODULES:
        constants = _module_constants(module_name)
        tree = ast.parse(_module_source(module_name))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or _call_name(node) != "skip_reason_text":
                continue
            assert node.args, f"{module_name}:{node.lineno} names no skip reason"
            first = node.args[0]
            assert isinstance(first, ast.Name), (
                f"{module_name}:{node.lineno} must name a registered slug constant, "
                f"not {ast.dump(first)}"
            )
            value = constants.get(first.id)
            assert value in slugs, (
                f"{module_name}:{node.lineno} names the unregistered skip reason {value!r}"
            )


def test_no_rebase_skip_reason_is_an_unnamed_literal() -> None:
    """A ``RebaseNoOp`` marker never carries a hand-written reason string.

    The marker IS the record of why the rebase did not run, so it has
    to come from the registry -- otherwise the skip is unnamed.
    """
    for module_name in _SKIP_DECISION_MODULES:
        tree = ast.parse(_module_source(module_name))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or _call_name(node) != "RebaseNoOp":
                continue
            for argument in node.args:
                assert not (
                    isinstance(argument, ast.Constant) and isinstance(argument.value, str)
                ), (
                    f"{module_name}:{node.lineno} names a rebase-skip reason with an "
                    "unnamed literal; every skip must carry a registered slug"
                )


def test_every_registered_reason_has_a_production_use_site() -> None:
    """No registry entry is dead: each slug is used outside the registry."""
    referenced: set[str] = set()
    slugs = registered_skip_slugs()
    for module_name in _SKIP_DECISION_MODULES:
        constants = _module_constants(module_name)
        tree = ast.parse(_module_source(module_name))
        registry_nodes: set[int] = set()
        for node in tree.body:
            targets = []
            if isinstance(node, ast.Assign):
                targets = list(node.targets)
            elif isinstance(node, ast.AnnAssign):
                targets = [node.target]
            if any(
                isinstance(target, ast.Name) and target.id == "SKIP_REASON_REGISTRY"
                for target in targets
            ):
                registry_nodes = {id(child) for child in ast.walk(node)}
        for node in ast.walk(tree):
            if id(node) in registry_nodes:
                continue
            if not isinstance(node, ast.Name):
                continue
            value = constants.get(node.id)
            if value in slugs:
                referenced.add(value)
    assert referenced == slugs


def test_no_entry_point_exposes_a_merge_only_switch() -> None:
    """A bare "force the merge" parameter is what let a skip go unnamed.

    The only merge-only switch left is ``prefer_merge``, and it maps to
    the registered ``retry_after_merge_commit`` reason inside
    :func:`run_rebase_or_merge`.
    """
    for module_name, function_name in (
        ("ralph.pipeline.auto_integrate", "_integrate_once"),
        ("ralph.pipeline.auto_integrate", "_integrate_once_owned"),
        ("ralph.pipeline.auto_integrate_rebase_merge", "run_rebase_or_merge"),
    ):
        function = getattr(importlib.import_module(module_name), function_name)
        parameters = inspect.signature(function).parameters
        assert "force_endpoint_merge" not in parameters, (
            f"{module_name}.{function_name} still exposes force_endpoint_merge: a bare "
            "boolean can skip the rebase without naming a registered reason"
        )


def test_no_entry_path_forces_the_endpoint_merge() -> None:
    """No integration call asks for a merge-only attempt by literal.

    A merge is reached through a REGISTERED skip reason or through the
    fallback that runs when the rebase cannot land -- never by asking
    the integration entry point to skip the rebase outright.
    """
    for module_name in _SKIP_DECISION_MODULES:
        tree = ast.parse(_module_source(module_name))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if _call_name(node) not in ("_integrate_once", "_integrate_once_owned"):
                continue
            for keyword in node.keywords:
                if keyword.arg not in ("prefer_merge", "force_endpoint_merge"):
                    continue
                value = keyword.value
                assert not (isinstance(value, ast.Constant) and value.value is True), (
                    f"{module_name}:{node.lineno} forces a merge-only attempt; a merge "
                    "must be reached through a registered skip reason"
                )


def test_the_crash_recovery_replay_does_not_force_the_endpoint_merge() -> None:
    """The retained-record replay re-enters the ordinary rebase-first path.

    Crash recovery of a retained record is an entry path like any
    other: the replay clears the stale intent and starts the ordinary
    integration again, so the rebase is the first attempt.
    """
    source = inspect.getsource(
        importlib.import_module("ralph.pipeline._owned_landing_reintegration")
    )
    tree = ast.parse(source)
    calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and _call_name(node) == "_integrate_once_owned"
    ]
    assert calls, "the retained-record replay must re-enter the integration"
    for node in calls:
        for keyword in node.keywords:
            if keyword.arg not in ("prefer_merge", "force_endpoint_merge"):
                continue
            value = keyword.value
            assert not (isinstance(value, ast.Constant) and value.value is True), (
                f"the crash-recovery replay must not ask for a merge-only attempt "
                f"(line {node.lineno})"
            )


# ---------------------------------------------------------------------------
# Entry paths: every one of them is rebase-first or names a registered skip
# ---------------------------------------------------------------------------


class _TraceRecorder:
    """Git-mutation double that records the order of an attempt."""

    def __init__(self) -> None:
        # A bounded FIFO: the recorder keeps the ORDER of the attempt, and
        # the cap keeps the recorder from growing with the number of
        # mutations an attempt performs.
        self.mutations: deque[str] = deque(maxlen=_MAX_RECORDED_MUTATIONS)
        self.rebase_result: RebaseResult = RebaseSuccess()
        self.landed = True
        self.land_failures = 0

    def rebase_onto(self, root: Path, target: str) -> RebaseResult:
        self.mutations.append("rebase_onto")
        return self.rebase_result

    def abort_rebase(self, root: Path) -> None:
        self.mutations.append("abort_rebase")

    def continue_rebase(self, root: Path) -> None:
        self.mutations.append("continue_rebase")

    def abort_merge(self, root: Path) -> bool:
        self.mutations.append("abort_merge")
        return True

    def continue_merge(self, root: Path) -> bool:
        self.mutations.append("continue_merge")
        return True

    def abort_cherry_pick(self, root: Path) -> bool:
        self.mutations.append("abort_cherry_pick")
        return True

    def continue_cherry_pick(self, root: Path) -> bool:
        self.mutations.append("continue_cherry_pick")
        return True

    def stage_all(self, root: Path) -> bool:
        self.mutations.append("stage_all")
        return True

    def commit(
        self,
        root: Path,
        *,
        on_failure: Callable[[str], None] | None = None,
    ) -> bool:
        self.mutations.append("commit")
        return True

    def fast_forward_target(
        self,
        root: Path,
        target: str,
        feature_sha: str,
        *,
        reclaim_target_worktree: bool = True,
        on_reclaimed: Callable[[ReclaimResult], None] | None = None,
    ) -> tuple[bool, str]:
        self.mutations.append("fast_forward_target")
        if self.land_failures > 0:
            self.land_failures -= 1
            return False, "target advanced concurrently (not an ancestor of feature)"
        return True, ""

    def land_feature(
        self,
        root: Path,
        target: str,
        feature_sha: str,
        *,
        reclaim_target_worktree: bool = True,
        on_reclaimed: Callable[[ReclaimResult], None] | None = None,
    ) -> tuple[bool, str]:
        return self.fast_forward_target(
            root,
            target,
            feature_sha,
            reclaim_target_worktree=reclaim_target_worktree,
            on_reclaimed=on_reclaimed,
        )

    def create_backup_ref(
        self,
        root: Path,
        pre_feature_sha: str | None,
        owning_worktree: str | None = None,
    ) -> str | None:
        self.mutations.append("create_backup_ref")
        return "refs/ralph-backup/feature"

    def delete_backup_ref(self, root: Path, backup_ref: str | None) -> None:
        self.mutations.append("delete_backup_ref")

    def reattach_head(self, root: Path, branch: str) -> bool:
        self.mutations.append("reattach_head")
        return True

    def resolve_rebase(
        self,
        root: Path,
        target: str,
        resolver: RebaseStopResolver,
        *,
        session: ResolutionSession,
    ) -> bool:
        self.mutations.append("resolve_rebase")
        return True


@pytest.fixture(autouse=True)
def _stub_repository_reads(monkeypatch: pytest.MonkeyPatch) -> None:
    """Replace every git READ the integration seams perform."""
    monkeypatch.setattr(
        "ralph.pipeline.auto_integrate.resolve_integration_target",
        lambda *_args, **_kwargs: _TARGET,
    )
    monkeypatch.setattr(
        "ralph.pipeline.auto_integrate._current_branch_or_detached_marker",
        lambda *_args, **_kwargs: "feature",
    )
    monkeypatch.setattr(
        "ralph.pipeline.auto_integrate._refresh_target",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "ralph.pipeline.auto_integrate.branch_sha",
        lambda *_args, **_kwargs: _TARGET_SHA,
    )
    monkeypatch.setattr(
        "ralph.pipeline.auto_integrate.get_head_sha",
        lambda *_args, **_kwargs: _FEATURE_SHA,
    )
    monkeypatch.setattr(
        "ralph.pipeline.auto_integrate.check_rebase_preconditions",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "ralph.pipeline.auto_integrate.observe_conflict_identity",
        lambda *_args, **_kwargs: ConflictIdentity(
            feature_sha=_FEATURE_SHA,
            target_sha=_TARGET_SHA,
        ),
    )
    monkeypatch.setattr(
        "ralph.pipeline.auto_integrate._recover_before_attempt",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "ralph.pipeline.auto_integrate.post_attempt_verify",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "ralph.pipeline.auto_integrate.contained_land",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "ralph.pipeline.auto_integrate.read_record",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "ralph.pipeline.auto_integrate.read_record_outcome",
        lambda *_args, **_kwargs: RecordReadOutcome(
            disposition=RecordReadOutcomeDisposition.ABSENT,
        ),
    )
    monkeypatch.setattr(
        "ralph.pipeline.auto_integrate._write_record",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "ralph.pipeline.auto_integrate._clear_record",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "ralph.pipeline.auto_integrate._read_post_integration_head_sha",
        lambda *_args, **_kwargs: _FEATURE_SHA,
    )
    monkeypatch.setattr(
        "ralph.pipeline.auto_integrate._reattach_head_if_detached",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "ralph.pipeline.auto_integrate_resolution_state.evaluate_integration_containment",
        lambda root, target, **_kwargs: ContainmentVerdict(
            contained=False,
            target=target,
            feature_sha=_FEATURE_SHA,
            target_sha=_TARGET_SHA,
            evidence="uncontained",
        ),
    )
    monkeypatch.setattr(
        "ralph.pipeline.auto_integrate_rebase_merge.run_git",
        lambda *_args, **_kwargs: SimpleNamespace(
            returncode=1,
            stdout="",
            stderr="unavailable",
        ),
    )
    monkeypatch.setattr(
        "ralph.pipeline.auto_integrate_rebase_merge.clear_untracked_merge_obstructions",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "ralph.pipeline.auto_integrate_rebase_merge.rebase_in_progress",
        lambda *_args, **_kwargs: False,
    )

    @contextmanager
    def _fake_transaction(_root: Path) -> Iterator[bool]:
        yield True

    monkeypatch.setattr(
        "ralph.pipeline.auto_integrate.integration_transaction",
        _fake_transaction,
    )
    monkeypatch.setattr(
        "ralph.pipeline.auto_integrate_transaction.integration_transaction",
        _fake_transaction,
    )
    monkeypatch.setattr(
        "ralph.pipeline.auto_integrate_recovery.integration_transaction",
        _fake_transaction,
    )

    # Planning seams: the tree is integration-ready and the target moved
    # ahead, so the planning catch-up runs.
    monkeypatch.setattr(
        "ralph.pipeline.auto_integrate_planning.resolve_integration_target",
        lambda *_args, **_kwargs: _TARGET,
    )
    monkeypatch.setattr(
        "ralph.pipeline.auto_integrate_planning.branch_relation",
        lambda *_args, **_kwargs: BranchRelation.DIVERGED,
    )
    monkeypatch.setattr(
        "ralph.pipeline.auto_integrate_planning.assess_tree_readiness",
        lambda *_args, **_kwargs: TreeReadinessAssessment(
            development_dispatch_allowed=True,
            integration_ready=True,
        ),
    )
    monkeypatch.setattr(
        "ralph.pipeline.auto_integrate_planning.retained_integration_reason",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "ralph.pipeline.auto_integrate_planning.rebase_in_progress",
        lambda *_args, **_kwargs: False,
    )
    monkeypatch.setattr(
        "ralph.pipeline.auto_integrate_planning._reattach_head_if_detached",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "ralph.pipeline.auto_integrate_recovery_terminal.post_attempt_verify",
        lambda *_args, **_kwargs: None,
    )


def _config() -> UnifiedConfig:
    return UnifiedConfig.model_validate(
        {
            "general": {
                "auto_integrate_enabled": True,
                "auto_integrate_target": _TARGET,
                "auto_integrate_remote_enabled": False,
            }
        }
    )


def test_the_planning_catch_up_attempts_the_rebase_first(tmp_path: Path) -> None:
    """The planning catch-up replays onto the target; it never forces a merge."""
    recorder = _TraceRecorder()
    token = install_executors(recorder, recorder)
    try:
        outcome = planning.integrate_before_planning(
            _config(),
            WorkspaceScope(tmp_path),
            RebaseState(),
        )
    finally:
        reset_executors(token)
    assert outcome is not None
    assert "rebase_onto" in recorder.mutations, (
        f"the planning catch-up must attempt the rebase first, got {recorder.mutations!r}"
    )


def test_the_conflict_strategy_escalation_attempts_the_rebase_first(tmp_path: Path) -> None:
    """Escalating the conflict ladder changes the strategy, not the order."""
    recorder = _TraceRecorder()
    token = install_executors(recorder, recorder)
    try:
        outcome = auto_integrate_after_commit(
            _config(),
            WorkspaceScope(tmp_path),
            RebaseState(conflict_strategy_index=2),
            sleep=_no_sleep,
            jitter=_no_jitter,
        )
    finally:
        reset_executors(token)
    assert outcome is not None
    assert "rebase_onto" in recorder.mutations, (
        f"the merge-instead rung must still attempt the rebase first, got {recorder.mutations!r}"
    )


def test_the_retry_after_a_merge_commit_records_the_registered_skip(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A retry straight after a merge commit names the registered reason.

    The skip is the only one the product allows outside the range
    observations, so it has to be visible in the durable state.
    """
    recorder = _TraceRecorder()
    recorder.rebase_result = RebaseConflicts(files=["shared.txt"])
    recorder.land_failures = 1
    monkeypatch.setattr(
        "ralph.pipeline.auto_integrate_rebase_merge.endpoint_merge_with_resolution",
        lambda *_args, **_kwargs: MergeResult(outcome="success"),
    )
    token = install_executors(recorder, recorder)
    try:
        outcome = auto_integrate_after_commit(
            _config(),
            WorkspaceScope(tmp_path),
            RebaseState(),
            sleep=_no_sleep,
            jitter=_no_jitter,
        )
    finally:
        reset_executors(token)
    assert outcome is not None
    assert outcome.last_reason is not None
    assert skip_reason_slug(outcome.last_reason) == SKIP_RETRY_AFTER_MERGE_COMMIT
    assert is_registered_skip_reason(outcome.last_reason)
    # The retry took the named skip: the rebase was not attempted twice.
    assert recorder.mutations.count("rebase_onto") == 1


def test_the_registry_entry_for_the_retry_names_the_previous_merge_commit() -> None:
    """The retry skip says WHICH observation justifies it."""
    assert "merge commit" in skip_reason_observation(SKIP_RETRY_AFTER_MERGE_COMMIT)
    assert skip_reason_observation(SKIP_MERGE_COMMITS_IN_RANGE) != (
        skip_reason_observation(SKIP_RETRY_AFTER_MERGE_COMMIT)
    )


def test_the_registry_covers_every_range_observation() -> None:
    """Each range observation has its own registered reason."""
    assert set(SKIP_REASON_REGISTRY) == registered_skip_slugs()
    assert SKIP_MERGE_COMMITS_IN_RANGE in registered_skip_slugs()
    assert SKIP_ROOT_COMMITS_IN_RANGE in registered_skip_slugs()
    assert SKIP_ALL_EMPTY_REPLAY in registered_skip_slugs()
    assert SKIP_CASE_FOLDING_COLLISION in registered_skip_slugs()
