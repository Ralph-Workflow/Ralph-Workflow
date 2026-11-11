"""Unit tests for deterministic Stage 1 commit review tripwires.

The unit cases drive the tripwire evaluator with synthetic diff entries. The
black-box proofs at the end of this module drive the REAL commit-review phase
against a REAL temporary git repository, so the post-edit re-check is proven
against the sealed proposal tree instead of a synthetic diff.
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from ralph.phases import PhaseContext
from ralph.phases._commit_review_tripwires import (
    DEFAULT_BINARY_SIZE_LIMIT,
    DEFAULT_MAX_DELETED_FILES,
    DEFAULT_MAX_DELETED_FRACTION,
    DiffEntry,
    Finding,
    TripwireReport,
    TripwireThresholds,
    evaluate_tripwires,
    is_recognized_secret_path,
    resolve_immutable_tracked_count,
)
from ralph.phases.commit_review import (
    CommitReviewApproved,
    CommitReviewRejected,
    handle_commit_review_phase,
)
from ralph.pipeline.effects import InvokeAgentEffect
from ralph.workspace.fs import FsWorkspace


def test_thresholds_defaults() -> None:
    """TripwireThresholds provides documented defaults and frozen immutability."""
    thresholds = TripwireThresholds()
    assert thresholds.max_deleted_files == DEFAULT_MAX_DELETED_FILES
    assert thresholds.max_deleted_fraction == DEFAULT_MAX_DELETED_FRACTION
    assert thresholds.binary_size_limit == DEFAULT_BINARY_SIZE_LIMIT

    with pytest.raises(FrozenInstanceError):
        thresholds.__setattr__("max_deleted_files", 100)


def test_thresholds_custom() -> None:
    """TripwireThresholds accepts custom values positionally and via kwargs."""
    t1 = TripwireThresholds(10, 0.1, 500_000)
    assert t1.max_deleted_files == 10
    assert t1.max_deleted_fraction == 0.1
    assert t1.binary_size_limit == 500_000

    t2 = TripwireThresholds(
        max_deleted_files=20,
        max_deleted_fraction=0.5,
        binary_size_limit=2_000_000,
    )
    assert t2.max_deleted_files == 20
    assert t2.max_deleted_fraction == 0.5
    assert t2.binary_size_limit == 2_000_000


def test_finding_and_report_properties() -> None:
    """Finding and TripwireReport report correct properties."""
    clean_report = TripwireReport()
    assert clean_report.is_clean is True
    assert clean_report.passed is True
    assert clean_report.has_hard_tripwires is False
    assert clean_report.has_soft_tripwires is False

    hard_finding = Finding(category="mass_deletion", message="too many deletes", count=60)
    soft_finding = Finding(category="out_of_scope", message="outside allowed", path="other/file.py")

    hard_only = TripwireReport(hard=(hard_finding,))
    assert hard_only.is_clean is False
    assert hard_only.passed is False
    assert hard_only.has_hard_tripwires is True
    assert hard_only.has_soft_tripwires is False

    soft_only = TripwireReport(soft=(soft_finding,))
    assert soft_only.is_clean is False
    assert soft_only.passed is True
    assert soft_only.has_hard_tripwires is False
    assert soft_only.has_soft_tripwires is True

    both = TripwireReport(hard=(hard_finding,), soft=(soft_finding,))
    assert both.is_clean is False
    assert both.passed is False
    assert both.has_hard_tripwires is True
    assert both.has_soft_tripwires is True


def test_empty_diff_evaluates_clean() -> None:
    """An empty diff produces no tripwire findings."""
    report = evaluate_tripwires(
        diff_entries=[],
        tracked_count=100,
        thresholds=TripwireThresholds(),
    )
    assert report.is_clean is True
    assert report.passed is True
    assert len(report.hard) == 0
    assert len(report.soft) == 0


def test_legitimate_small_deletions_and_edits_pass() -> None:
    """Small modifications and deletions below thresholds pass cleanly."""
    diff = [
        DiffEntry(path="src/app.py", status="M"),
        DiffEntry(path="src/feature.py", status="A"),
        DiffEntry(path="tests/test_old.py", status="D"),
        DiffEntry(path="pyproject.toml", status="M"),
    ]
    report = evaluate_tripwires(
        diff_entries=diff,
        tracked_count=100,
        thresholds=TripwireThresholds(),
    )
    assert report.is_clean is True
    assert report.passed is True


def test_mass_deletion_count_threshold() -> None:
    """Deleted file count exceeding threshold triggers a hard tripwire."""
    thresholds = TripwireThresholds(max_deleted_files=2, max_deleted_fraction=0.8)
    diff = [DiffEntry(path=f"src/file_{i}.py", status="D") for i in range(3)]
    report = evaluate_tripwires(
        diff_entries=diff,
        tracked_count=100,
        thresholds=thresholds,
    )
    assert report.passed is False
    assert report.has_hard_tripwires is True
    assert len(report.hard) == 1
    finding = report.hard[0]
    assert finding.category == "mass_deletion"
    assert finding.count == 3
    assert "exceeds limit of 2" in finding.message


def test_mass_deletion_fraction_threshold() -> None:
    """Deleted file fraction exceeding threshold triggers a hard tripwire."""
    thresholds = TripwireThresholds(max_deleted_files=50, max_deleted_fraction=0.2)
    # 3 out of 10 is 30% > 20%
    diff = [DiffEntry(path=f"src/file_{i}.py", status="D") for i in range(3)]
    report = evaluate_tripwires(
        diff_entries=diff,
        tracked_count=10,
        thresholds=thresholds,
    )
    assert report.passed is False
    assert report.has_hard_tripwires is True
    assert len(report.hard) == 1
    finding = report.hard[0]
    assert finding.category == "mass_deletion"
    assert finding.count == 3
    assert "30.0%" in finding.message


def test_whole_repo_deletion_triggers_both_mass_deletion_checks() -> None:
    """Whole repo deletion triggers mass deletion by count and fraction."""
    thresholds = TripwireThresholds(max_deleted_files=10, max_deleted_fraction=0.25)
    diff = [DiffEntry(path=f"file_{i}.py", status="D") for i in range(20)]
    report = evaluate_tripwires(
        diff_entries=diff,
        tracked_count=20,
        thresholds=thresholds,
    )
    assert report.passed is False
    mass_findings = [f for f in report.hard if f.category == "mass_deletion"]
    assert len(mass_findings) == 2


def test_zero_tracked_count_avoids_division_by_zero() -> None:
    """A tracked count of zero does not divide by zero."""
    diff = [DiffEntry(path="file.py", status="D")]
    report = evaluate_tripwires(
        diff_entries=diff,
        tracked_count=0,
        thresholds=TripwireThresholds(max_deleted_files=10),
    )
    assert report.is_clean is True


def test_protected_git_content_deleted_and_modified() -> None:
    """Modifying or deleting .git content triggers hard protected tripwires."""
    # Deletion
    del_diff = [DiffEntry(path=".git/config", status="D")]
    del_report = evaluate_tripwires(del_diff, 10, TripwireThresholds())
    assert del_report.passed is False
    assert del_report.hard[0].category == "protected_deletion"
    assert ".git/config" in del_report.hard[0].message

    # Modification / addition
    mod_diff = [DiffEntry(path=".git/HEAD", status="M")]
    mod_report = evaluate_tripwires(mod_diff, 10, TripwireThresholds())
    assert mod_report.passed is False
    assert mod_report.hard[0].category == "protected_overwrite"
    assert ".git/HEAD" in mod_report.hard[0].message

    # .gitignore is NOT .git content
    ignore_diff = [DiffEntry(path=".gitignore", status="M")]
    ignore_report = evaluate_tripwires(ignore_diff, 10, TripwireThresholds())
    assert ignore_report.is_clean is True


def test_protected_prompt_and_agents_md() -> None:
    """Deleting or overwriting PROMPT.md or AGENTS.md triggers hard tripwires."""
    diff = [
        DiffEntry(path="PROMPT.md", status="M"),
        DiffEntry(path="AGENTS.md", status="D"),
    ]
    report = evaluate_tripwires(diff, 10, TripwireThresholds())
    assert report.passed is False
    assert len(report.hard) == 2

    prompt_finding = next(f for f in report.hard if f.path == "PROMPT.md")
    assert prompt_finding.category == "protected_overwrite"

    agents_finding = next(f for f in report.hard if f.path == "AGENTS.md")
    assert agents_finding.category == "protected_deletion"


def test_protected_manifest_and_lockfile_deletion() -> None:
    """Deleting manifest or lockfile is a hard tripwire; editing is allowed."""
    # Editing pyproject.toml is allowed
    edit_diff = [DiffEntry(path="pyproject.toml", status="M")]
    edit_report = evaluate_tripwires(edit_diff, 10, TripwireThresholds())
    assert edit_report.is_clean is True

    # Deleting pyproject.toml is rejected
    del_diff = [DiffEntry(path="pyproject.toml", status="D")]
    del_report = evaluate_tripwires(del_diff, 10, TripwireThresholds())
    assert del_report.passed is False
    assert del_report.hard[0].category == "protected_deletion"

    # Deleting lockfiles
    for lockfile in ("package-lock.json", "poetry.lock", "uv.lock", "Cargo.lock"):
        lock_report = evaluate_tripwires(
            [DiffEntry(path=lockfile, status="D")],
            10,
            TripwireThresholds(),
        )
        assert lock_report.passed is False
        assert lock_report.hard[0].category == "protected_deletion"


def test_protected_top_level_directory_deletion() -> None:
    """Deleting a top-level source or test directory in its entirety triggers hard tripwire."""
    # Directory entry directly
    dir_diff = [DiffEntry(path="src", status="D")]
    dir_report = evaluate_tripwires(dir_diff, 10, TripwireThresholds())
    assert dir_report.passed is False
    assert dir_report.hard[0].category == "protected_deletion"

    # Tree mode entry
    tree_diff = [DiffEntry(path="tests", status="D", old_mode="040000")]
    tree_report = evaluate_tripwires(tree_diff, 10, TripwireThresholds())
    assert tree_report.passed is False
    assert tree_report.hard[0].category == "protected_deletion"


def test_protected_top_level_directory_deleted_in_its_entirety() -> None:
    """Deleting EVERY tracked path of a protected top-level directory is a hard tripwire.

    A recursive diff reports blob entries only, so the removal of a whole
    directory arrives as one deleted blob path per tracked file. The
    base-tree membership carried by the entries is the evidence that the
    directory is gone in its entirety.
    """
    base_paths = frozenset({"src/app.py", "src/util.py", "tests/test_app.py"})
    diff = [
        DiffEntry(path="src/app.py", status="D", base_tree_paths=base_paths),
        DiffEntry(path="src/util.py", status="D", base_tree_paths=base_paths),
    ]
    report = evaluate_tripwires(diff, 10, TripwireThresholds())
    assert report.passed is False
    findings = [f for f in report.hard if f.category == "protected_deletion"]
    assert len(findings) == 1
    assert findings[0].path == "src"
    assert "in its entirety" in findings[0].message


def test_partially_deleted_protected_top_level_directory_is_not_an_entirety_deletion() -> None:
    """Deleting SOME tracked paths of a protected top-level directory fires no such finding."""
    # RALPH-TEST-REASON: the membership is the WHOLE base tree, so the fixture
    # must match the tracked_count it reports: a 2-path subset presented as the
    # whole tree would make one deletion look like half the repository.
    base_paths = frozenset({"src/app.py", "src/util.py"}) | frozenset(
        f"docs/note_{index}.md" for index in range(8)
    )
    diff = [DiffEntry(path="src/app.py", status="D", base_tree_paths=base_paths)]
    report = evaluate_tripwires(diff, 10, TripwireThresholds())
    assert report.is_clean is True
    assert report.hard == ()


def test_custom_protected_paths() -> None:
    """Custom protected paths passed to evaluator trigger hard tripwires."""
    protected_rules = ["config/production.json", "licenses/*"]

    # Modification of protected file
    mod_diff = [DiffEntry(path="config/production.json", status="M")]
    mod_report = evaluate_tripwires(mod_diff, 10, TripwireThresholds(), protected=protected_rules)
    assert mod_report.passed is False
    assert mod_report.hard[0].category == "protected_overwrite"

    # Deletion of glob-matched file
    del_diff = [DiffEntry(path="licenses/BSD.txt", status="D")]
    del_report = evaluate_tripwires(del_diff, 10, TripwireThresholds(), protected=protected_rules)
    assert del_report.passed is False
    assert del_report.hard[0].category == "protected_deletion"


def test_out_of_scope_allowed_directories() -> None:
    """Changes outside allowed_directories trigger soft tripwire findings."""
    allowed = ["ralph/phases", "tests"]

    in_scope_diff = [
        DiffEntry(path="ralph/phases/new_phase.py", status="A"),
        DiffEntry(path="tests/test_new_phase.py", status="A"),
    ]
    in_scope_report = evaluate_tripwires(
        in_scope_diff, 10, TripwireThresholds(), allowed_dirs=allowed
    )
    assert in_scope_report.is_clean is True

    out_of_scope_diff = [
        DiffEntry(path="ralph/phases/new_phase.py", status="A"),
        DiffEntry(path="docs/guide.md", status="M"),
    ]
    out_of_scope_report = evaluate_tripwires(
        out_of_scope_diff, 10, TripwireThresholds(), allowed_dirs=allowed
    )
    assert out_of_scope_report.passed is True  # Soft finding does not fail hard gate
    assert out_of_scope_report.has_soft_tripwires is True
    assert len(out_of_scope_report.soft) == 1
    assert out_of_scope_report.soft[0].category == "out_of_scope"
    assert out_of_scope_report.soft[0].path == "docs/guide.md"


def test_recognized_secret_paths_and_patterns() -> None:
    """Staged recognized secret files trigger soft tripwire findings."""
    assert is_recognized_secret_path(".env") is True
    assert is_recognized_secret_path("config/.env.local") is True
    assert is_recognized_secret_path(".env.staging.local") is True
    assert is_recognized_secret_path("secrets.yml") is True
    assert is_recognized_secret_path("credentials.json") is True
    assert is_recognized_secret_path("keys/id_rsa") is True
    assert is_recognized_secret_path("certs/server.pem") is True
    assert is_recognized_secret_path("src/app.py") is False

    staged_secrets = [
        DiffEntry(path=".env", status="A"),
        DiffEntry(path="sub/credentials.json", status="M"),
    ]
    report = evaluate_tripwires(staged_secrets, 10, TripwireThresholds())
    assert report.passed is True
    assert report.has_soft_tripwires is True
    assert len(report.soft) == 2
    for finding in report.soft:
        assert finding.category == "secret"

    # Deleted secret file does not trigger staged secret soft finding
    del_secret = [DiffEntry(path=".env", status="D")]
    del_report = evaluate_tripwires(del_secret, 10, TripwireThresholds())
    assert del_report.has_soft_tripwires is False


def test_staged_secret_content_scanning() -> None:
    """Content containing credentials triggers soft secret tripwires."""
    secret_diff = [
        DiffEntry(
            path="config/tokens.py",
            status="A",
            content="AWS_KEY = 'AKIAIOSFODNN7EXAMPLE'\n",
        ),
        DiffEntry(
            path="auth/github.py",
            status="M",
            content="TOKEN = 'ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789'\n",
        ),
    ]
    report = evaluate_tripwires(secret_diff, 10, TripwireThresholds())
    assert report.passed is True
    assert report.has_soft_tripwires is True
    assert len(report.soft) == 2
    assert "AWS access key" in report.soft[0].message
    assert "GitHub personal access token" in report.soft[1].message


def test_binary_size_thresholds() -> None:
    """Oversized binary files trigger soft findings; small binaries pass."""
    thresholds = TripwireThresholds(binary_size_limit=500)

    # Small binary passes
    small_binary = [DiffEntry(path="assets/logo.png", status="A", size=400, is_binary=True)]
    small_report = evaluate_tripwires(small_binary, 10, thresholds)
    assert small_report.is_clean is True

    # Large binary triggers soft finding
    large_binary = [DiffEntry(path="assets/banner.png", status="A", size=1000, is_binary=True)]
    large_report = evaluate_tripwires(large_binary, 10, thresholds)
    assert large_report.passed is True
    assert large_report.has_soft_tripwires is True
    assert len(large_report.soft) == 1
    assert large_report.soft[0].category == "binary_size"
    assert large_report.soft[0].count == 1000

    # Large text file does not trigger binary size limit
    large_text = [DiffEntry(path="data.txt", status="A", size=10_000, is_binary=False)]
    text_report = evaluate_tripwires(large_text, 10, thresholds)
    assert text_report.is_clean is True

    # Deleted large binary does not trigger binary size limit
    del_large_binary = [DiffEntry(path="old_asset.png", status="D", size=1000, is_binary=True)]
    del_binary_report = evaluate_tripwires(del_large_binary, 10, thresholds)
    assert del_binary_report.has_soft_tripwires is False


def test_duck_typing_and_dict_compatibility() -> None:
    """evaluate_tripwires supports plain dicts and duck-typed objects."""
    dict_diff = [
        {"path": "src/module.py", "status": "M", "size": 120},
        {"path": ".env", "status": "A", "size": 50},
    ]
    report = evaluate_tripwires(dict_diff, 10, TripwireThresholds())
    assert report.passed is True
    assert len(report.soft) == 1
    assert report.soft[0].category == "secret"


def test_combined_hard_and_soft_findings() -> None:
    """Diff containing both hard and soft violations reports both categories."""
    diff = [
        DiffEntry(path="PROMPT.md", status="M"),  # hard: protected overwrite
        DiffEntry(path="secrets.yml", status="A"),  # soft: secret
    ]
    report = evaluate_tripwires(diff, 10, TripwireThresholds())
    assert report.passed is False
    assert report.has_hard_tripwires is True
    assert report.has_soft_tripwires is True
    assert len(report.hard) == 1
    assert report.hard[0].category == "protected_overwrite"
    assert len(report.soft) == 1
    assert report.soft[0].category == "secret"


def _base_tree_paths(count: int) -> frozenset[str]:
    """Return a base-tree membership of ``count`` tracked paths."""
    return frozenset(f"components/module_{index:03d}.py" for index in range(count))


def test_mass_deletion_fraction_is_measured_against_the_immutable_base_tree() -> None:
    """The fraction denominator is the base tree, never the mutable real index.

    Staging the deletion of every tracked path empties the real index, so a
    denominator read from that index would be zero and the fraction check
    would be skipped entirely. The membership the git layer attaches to the
    entries keeps the denominator at the size of the tree the diff was
    computed against.
    """
    base_paths = _base_tree_paths(10)
    deleted = sorted(base_paths)[:3]
    diff = [DiffEntry(path=path, status="D", base_tree_paths=base_paths) for path in deleted]
    thresholds = TripwireThresholds(max_deleted_files=50, max_deleted_fraction=0.2)

    report = evaluate_tripwires(
        diff_entries=diff,
        tracked_count=0,
        thresholds=thresholds,
    )

    assert report.has_hard_tripwires is True
    mass_findings = [f for f in report.hard if f.category == "mass_deletion"]
    assert len(mass_findings) == 1
    assert mass_findings[0].count == 3
    assert "30.0%" in mass_findings[0].message
    assert "(3/10)" in mass_findings[0].message


def test_staged_additions_cannot_dilute_the_mass_deletion_fraction() -> None:
    """Staged additions never inflate the denominator of the fraction.

    The additions are tracked by the real index, so an index-sized
    denominator would report a fraction of 3/27 and stay silent. The
    immutable base tree holds 10 paths, so 3 of them is 30%.
    """
    base_paths = _base_tree_paths(10)
    deleted = sorted(base_paths)[:3]
    diff = [DiffEntry(path=path, status="D", base_tree_paths=base_paths) for path in deleted]
    diff.extend(
        DiffEntry(path=f"components/added_{index:02d}.py", status="A", base_tree_paths=base_paths)
        for index in range(20)
    )
    thresholds = TripwireThresholds(max_deleted_files=50, max_deleted_fraction=0.2)

    report = evaluate_tripwires(
        diff_entries=diff,
        tracked_count=27,
        thresholds=thresholds,
    )

    assert report.has_hard_tripwires is True
    mass_findings = [f for f in report.hard if f.category == "mass_deletion"]
    assert len(mass_findings) == 1
    assert mass_findings[0].count == 3
    assert "(3/10)" in mass_findings[0].message


def test_a_small_deletion_does_not_trip_the_mass_deletion_fraction() -> None:
    """A couple of deleted files in a large repository stays below the fraction."""
    base_paths = _base_tree_paths(100)
    deleted = sorted(base_paths)[:2]
    diff = [DiffEntry(path=path, status="D", base_tree_paths=base_paths) for path in deleted]

    report = evaluate_tripwires(
        diff_entries=diff,
        tracked_count=98,
        thresholds=TripwireThresholds(),
    )

    assert report.is_clean is True
    assert report.hard == ()


def test_the_deleted_file_count_threshold_survives_an_empty_real_index() -> None:
    """The count threshold keeps working when the real index lists nothing."""
    base_paths = _base_tree_paths(10)
    deleted = sorted(base_paths)[:3]
    diff = [DiffEntry(path=path, status="D", base_tree_paths=base_paths) for path in deleted]
    thresholds = TripwireThresholds(max_deleted_files=2, max_deleted_fraction=0.9)

    report = evaluate_tripwires(
        diff_entries=diff,
        tracked_count=0,
        thresholds=thresholds,
    )

    mass_findings = [f for f in report.hard if f.category == "mass_deletion"]
    assert len(mass_findings) == 1
    assert mass_findings[0].count == 3
    assert "exceeds limit of 2" in mass_findings[0].message


def test_the_immutable_tracked_count_is_unavailable_without_the_base_membership() -> None:
    """A missing membership makes the denominator unknowable, not zero.

    ``None`` is the fail-closed signal: the caller must refuse the proposal
    instead of evaluating the fraction against a denominator it cannot trust.
    """
    deletions = [DiffEntry(path="components/module_000.py", status="D")]
    additions = [DiffEntry(path="components/module_000.py", status="A")]

    assert resolve_immutable_tracked_count(deletions) is None
    assert resolve_immutable_tracked_count(additions) == 0
    assert resolve_immutable_tracked_count([]) == 0

    base_paths = _base_tree_paths(4)
    carried = [DiffEntry(path="components/module_000.py", status="D", base_tree_paths=base_paths)]
    assert resolve_immutable_tracked_count(carried) == 4


# Black-box proofs: the post-edit re-check of the REAL commit-review phase.
#
# A staged secret and an oversized binary are FIRST offered to the edit step:
# the reviewer may untrack or ignore them. When the unsafe content is still
# in the re-sealed proposal, the engine must REJECT the proposal on its own
# evidence (the affected paths and the concrete counts and limits) and must
# never approve it. The proofs below drive ``handle_commit_review_phase``
# against a REAL temporary git repository, so the sealed proposal tree, the
# re-seal after the edit step and the real diff are the subject under test.

_COMMIT_REVIEW_ARTIFACT_PATH = ".agent/artifacts/commit_review.md"
_REVIEW_PHASE = "development_commit_review"

#: A recognized secret file name the engine's default ignore rules do NOT
#: cover, so the sealed proposal really carries it.
_SECRET_REL_PATH = "secrets.yml"

#: A binary one byte above the default ``binary_size_limit``.
_BINARY_REL_PATH = "assets/big.bin"
_BINARY_SIZE = DEFAULT_BINARY_SIZE_LIMIT + 1


def _write_review_artifact(
    repo_root: Path,
    *,
    verdict: str,
    actions: tuple[tuple[str, str], ...] = (),
) -> None:
    """Write a canonical commit_review artifact with the given verdict and actions."""
    lines = ["---", "type: commit_review", f"verdict: {verdict}", "---"]
    if actions:
        lines.extend(["", "## Actions"])
        for index, (action, value) in enumerate(actions, start=1):
            lines.append(f"- [A{index}] {action} | {value}")
    artifact_path = repo_root / _COMMIT_REVIEW_ARTIFACT_PATH
    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    artifact_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _run_review_phase(repo_root: Path) -> list[object]:
    """Drive the real commit-review phase entry point against ``repo_root``."""
    workspace = FsWorkspace(repo_root)
    ctx = PhaseContext.construct(
        workspace=workspace,
        registry=object(),
        chain_manager=object(),
        pipeline_policy=object(),
        artifacts_policy=object(),
        agents_policy=object(),
    )
    effect = InvokeAgentEffect(
        agent_name="dev",
        phase=_REVIEW_PHASE,
        prompt_file="cleanup.txt",
    )
    return handle_commit_review_phase(effect, ctx)


def _write_secret(repo_root: Path) -> None:
    """Write a recognized secret file into the working tree."""
    (repo_root / _SECRET_REL_PATH).write_text("token: hunter2\n", encoding="utf-8")


def _write_oversized_binary(repo_root: Path) -> None:
    """Write a binary one byte above the default binary size limit."""
    target = repo_root / _BINARY_REL_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"\x00" * _BINARY_SIZE)


@pytest.mark.timeout_seconds(20)
def test_a_staged_secret_that_survives_the_edit_step_is_rejected_on_engine_evidence(
    tmp_git_repo: Path,
) -> None:
    """A staged secret the edit step did not remove must never be approved.

    The reviewer approved the proposal, but the secret is still in the sealed
    proposal tree, so the engine rejects it on its own evidence instead of
    honouring the verdict.
    """
    repo_root = tmp_git_repo
    _write_secret(repo_root)
    _write_review_artifact(repo_root, verdict="approve")

    result = _run_review_phase(repo_root)

    assert len(result) == 1, f"Expected exactly one event, got {result!r}"
    rejection = result[0]
    assert isinstance(rejection, CommitReviewRejected), (
        f"A surviving secret must be rejected, got {type(rejection).__name__}: {rejection!r}"
    )
    assert not isinstance(rejection, CommitReviewApproved), (
        "A proposal that still carries a secret must never be approved"
    )
    assert _SECRET_REL_PATH in rejection.reason, (
        f"The engine-owned reason must name the surviving path; got {rejection.reason!r}"
    )


@pytest.mark.timeout_seconds(20)
def test_an_oversized_binary_that_survives_the_edit_step_is_rejected_on_engine_evidence(
    tmp_git_repo: Path,
) -> None:
    """An oversized binary the edit step did not remove must never be approved."""
    repo_root = tmp_git_repo
    _write_oversized_binary(repo_root)
    _write_review_artifact(repo_root, verdict="approve")

    result = _run_review_phase(repo_root)

    assert len(result) == 1, f"Expected exactly one event, got {result!r}"
    rejection = result[0]
    assert isinstance(rejection, CommitReviewRejected), (
        f"A surviving oversized binary must be rejected, got "
        f"{type(rejection).__name__}: {rejection!r}"
    )
    assert not isinstance(rejection, CommitReviewApproved), (
        "A proposal that still carries an oversized binary must never be approved"
    )
    assert _BINARY_REL_PATH in rejection.reason, (
        f"The engine-owned reason must name the surviving path; got {rejection.reason!r}"
    )
    assert str(_BINARY_SIZE) in rejection.reason, (
        f"The engine-owned reason must carry the measured size; got {rejection.reason!r}"
    )
    assert str(DEFAULT_BINARY_SIZE_LIMIT) in rejection.reason, (
        f"The engine-owned reason must carry the limit; got {rejection.reason!r}"
    )


@pytest.mark.timeout_seconds(20)
def test_a_secret_the_edit_step_removed_is_approved(tmp_git_repo: Path) -> None:
    """A secret the edit step DID remove must be approved, with no false rejection.

    The reviewer ignored the secret file, so it is no longer in the re-sealed
    proposal tree and the gate has nothing left to reject.
    """
    repo_root = tmp_git_repo
    _write_secret(repo_root)
    _write_review_artifact(
        repo_root,
        verdict="approve",
        actions=(("add_to_gitignore", _SECRET_REL_PATH),),
    )

    result = _run_review_phase(repo_root)

    assert len(result) == 1, f"Expected exactly one event, got {result!r}"
    assert isinstance(result[0], CommitReviewApproved), (
        f"A secret removed by the edit step must be approved, got {result[0]!r}"
    )


@pytest.mark.timeout_seconds(20)
def test_an_oversized_binary_the_edit_step_excluded_is_approved(tmp_git_repo: Path) -> None:
    """An oversized binary the edit step excluded must be approved."""
    repo_root = tmp_git_repo
    _write_oversized_binary(repo_root)
    _write_review_artifact(
        repo_root,
        verdict="approve",
        actions=(("add_to_git_exclude", _BINARY_REL_PATH),),
    )

    result = _run_review_phase(repo_root)

    assert len(result) == 1, f"Expected exactly one event, got {result!r}"
    assert isinstance(result[0], CommitReviewApproved), (
        f"An oversized binary excluded by the edit step must be approved, got {result[0]!r}"
    )
