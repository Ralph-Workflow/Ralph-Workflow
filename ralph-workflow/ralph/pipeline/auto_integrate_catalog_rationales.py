"""A1-H7 catalog rationale registry.

The auto-integration prompt enumerates every edge case as
``A1..H7`` and requires EACH to be closed out by either an
automated test or a code-adjacent written rationale that names
the ladder rung. The :mod:`tests.test_auto_integrate_catalog_coverage`
test asserts that each catalog entry has one of those two
backings.

This module is the rationale-side registry: a single source of
truth for every catalog entry's rationale token, kept separate
from the production code so a refactor that moves a primitive
does not lose the rationale the spec required. Each entry
documents the production code that implements the policy, the
ladder rung the entry sits on, and the unique grep-able token
the catalog test uses as evidence.

Rationale tokens are deliberately UPPER_SNAKE_CASE strings that
do not appear anywhere else in the codebase, so a ``grep -r`` for
the token lands here and nowhere else. The
``test_auto_integrate_catalog_coverage`` test asserts the token
appears somewhere in the ralph/ tree, so the test fails fast if
a rationale is removed during a refactor.
"""

from __future__ import annotations

#: A1 — Stale rebase-merge dir. Handled by
#: :func:`ralph.pipeline.auto_integrate_recovery._reclaim_unowned_stale_rebase`
#: which aborts the rebase when the worktree is clean. Ladder rung 2.
A1_STALE_REBASE_MERGE = "A1"

#: A2 — Stale rebase-apply dir. Same path as A1. The apply backend
#: is pin-prevented via :data:`ralph.git.hardening.PINNED_CONFIG_ARGS`
#: (``rebase.backend=merge``), so a rebase-apply dir only ever comes
#: from an external operator, which the AC-11 case-4 dirty-tree
#: protection preserves. Ladder rung 2 (reclaim) or 4 (preserve).
A2_STALE_REBASE_APPLY = "A2"

#: A3 — Corrupted rebase state dir. Handled by
#: :func:`ralph.pipeline.auto_integrate_recovery._rebase_state_dir_is_corrupt`
#: + :func:`ralph.pipeline.auto_integrate_recovery._remove_path`.
#: Ladder rung 2 (recover-then-land).
A3_CORRUPT_REBASE_STATE = "A3"

#: A4 — Lone REBASE_HEAD with no rebase dir. Removed directly by
#: ``_reclaim_unowned_stale_rebase`` because ``git rebase --abort``
#: is not available for a bare marker. Ladder rung 1.
A4_LONE_REBASE_HEAD = "A4"

#: A5 — MERGE_HEAD merge in progress. ``abort_merge`` is called
#: from the same reclaim path; a synthetic / truncated MERGE_HEAD
#: is unlinked when the abort refuses. Ladder rung 2.
A5_MERGE_HEAD = "A5"

#: A6 — CHERRY_PICK_HEAD / REVERT_HEAD / sequencer ops. Handled
#: by :func:`ralph.pipeline.auto_integrate_recovery._run_sequencer_quit`
#: which calls ``git cherry-pick --quit`` / ``git revert --quit``.
#: Ladder rung 2.
A6_CHERRY_PICK_SEQUENCER = "A6"

#: A7 — Bisect state. The precondition check refuses to auto-reset
#: a live ``git bisect`` session because ``bisect reset`` is
#: destructive to the operator's workflow. The integration attempt
#: records a rung-4 diagnostic and re-checks at every seam; the
#: moment the bisect ends, integration resumes automatically. Ladder
#: rung 4 (loud, self-resume).
A7_BISECT_DIAGNOSTIC = "A7"

#: A8 — Benign leftovers (AUTO_MERGE / MERGE_MSG / etc.). The
#: closed blocking set in
#: :data:`ralph.git.rebase.rebase_preconditions._BENIGN_LEFTOVER_ENTRIES`
#: is the floor; unknown new git-dir filenames are observed at
#: DEBUG and never block. Ladder rung 1 (do nothing).
A8_BENIGN_LEFTOVERS = "A8"

#: A9 — Stale index.lock. Handled by
#: :func:`ralph.pipeline.auto_integrate_recovery._lock_holder_is_dead`
#: which removes the lock only when the holder PID is provably
#: dead; a live holder returns False so the bounded retry backs
#: off (E9). Ladder rung 2 (reclaim) or 3 (retry).
A9_STALE_INDEX_LOCK = "A9"

#: A10 — Stale ref locks (refs/heads/<x>.lock, packed-refs.lock,
#: HEAD.lock). Same primitive as A9. Ladder rung 2 or 3.
A10_STALE_REF_LOCK = "A10"

#: A11 — Detached HEAD residue. Handled by
#: :func:`ralph.pipeline.auto_integrate_recovery._detached_head_no_state`
#: + :func:`ralph.pipeline.auto_integrate_recovery._reattach_head_to_branch`.
#: A detached HEAD by operator intent (no residue signature) is
#: skipped and re-checked at the next seam. Ladder rung 2 or 4.
A11_DETACHED_HEAD = "A11"

#: A12 — Stranded autostash / stash entries. The rebase engine
#: never passes ``--autostash`` (D4); dirty trees never start
#: integration in the first place. A ``rebase --abort`` re-applying
#: a stale autostash is the operator's pre-existing problem; the
#: pipeline preserves the stash entry loudly rather than swallowing
#: it. Ladder rung 1.
A12_AUTOSTASH = "A12"

#: B1 — No merge base / unrelated histories. Loud rung-4 diagnostic
#: at every seam; ``--allow-unrelated-histories`` is never passed
#: implicitly; the ``--`` argv terminator prevents a branch name
#: shaped like a flag from injecting it. Ladder rung 4.
B1_UNRELATED_HISTORIES = "B1"

#: B2 — Multiple merge bases (criss-cross). The ort merge backend
#: handles it; the post-attempt verify plus the conflict pipeline
#: absorb any virtual-base markers. Ladder rung 1.
B2_CRISS_CROSS = "B2"

#: B3 — Merge commits in the feature branch. The auto-integration
#: routing in
#: :func:`ralph.pipeline.auto_integrate_rebase_merge._range_routing_reason`
#: detects ``rev-list --merges`` and routes to the endpoint merge
#: (``_REASON_MERGE_COMMITS``) so a flattening rebase cannot
#: silently drop merge resolutions. Ladder rung 1 (route to
#: merge).
B3_MERGE_COMMITS = "B3"

#: B4 — Commits that become empty on replay. ``--empty=drop`` is
#: passed on every rebase; older git that ignores the flag is
#: answered with ``git rebase --skip`` via
#: :func:`ralph.git.rebase.rebase._rebase_stop_reports_empty`.
#: Ladder rung 1.
B4_EMPTY_ON_REPLAY = "B4"

#: B5 — Initially-empty commits. Kept (``--empty=keep`` is the
#: modern default); post-rebase commit count is verified.
#: Ladder rung 1.
B5_INITIALLY_EMPTY = "B5"

#: B6 — Already-upstream patches. Patch-id dedup via
#: ``rev-list --cherry-pick --no-merges`` in
#: :func:`ralph.pipeline.auto_integrate_rebase_merge._all_empty_replay`.
#: Ladder rung 1.
B6_CHERRY_DEDUP = "B6"

#: B7 — Root commits in the branch range. ``rev-list
#: --max-parents=0`` is checked; the routing reason
#: ``_REASON_ROOT_COMMITS`` sends the integration to the endpoint
#: merge (``rebase --root`` is out of scope). Ladder rung 1.
B7_ROOT_COMMITS = "B7"

#: B8 — Fork-point trap. The rebase argv is
#: ``rebase -- <upstream> <active_branch>`` so the replay range
#: is unambiguous and independent of
#: ``branch.<name>.merge``/fork-point heuristics. Ladder rung 1.
B8_FORK_POINT = "B8"

#: B9 — ``rebase.updateRefs`` in user config. ``--no-update-refs``
#: is passed on every rebase; PINNED_CONFIG_ARGS does not override.
#: Ladder rung 1.
B9_UPDATE_REFS = "B9"

#: B10 — Already up-to-date / ancestor cases. The
#: :func:`ralph.git.rebase.rebase._merge_base_is_ancestor` pre-flight
#: short-circuits the rebase with ``RebaseNoOp``. The "up to date"
#: text in stderr is ONLY trusted when corroborated by no rebase
#: dir on disk (the
#: :func:`ralph.git.rebase.rebase._contains_up_to_date_message`
#: gate). Ladder rung 1.
B10_ANCESTOR = "B10"

#: B11 — Concurrent gc pruning. A backup ref
#: ``refs/rebase-backup/<id>`` on the original feature tip is
#: held for the attempt duration; deleted after land or verified
#: abort. Ladder rung 1.
B11_GC_BACKUP = "B11"

#: C1 — Content conflict (UU). Detected by ``unmerged_paths`` /
#: :func:`ralph.git.hardening.unmerged_paths_z`; routed to the
#: conflict-resolution pipeline; a ``--continue`` that exits
#: non-zero because the NEXT commit conflicts is PROGRESS
#: (``REBASE_HEAD`` identity change). Ladder rung 1.
C1_CONTENT_CONFLICT = "C1"

#: C2 — Add/add (AA). Same path as C1.
C2_ADD_ADD = "C2"

#: C3 — Modify/delete & delete/modify (UD/DU). Marker-less; the
#: resolver prompt presents the delete-vs-modify intent. The
#: unmerged-XY detection (UD/DU) is index-authoritative.
#: Ladder rung 1.
C3_MODIFY_DELETE = "C3"

#: C4 — Rename/rename & rename/delete. ``parse_porcelain_z``
#: reassembles the rename source + destination NUL pair; the
#: resolver surfaces both names. Ladder rung 1.
C4_RENAME_RENAME = "C4"

#: C5 — Directory/file conflict. Ort parks files as
#: ``path~<label>``; the post-attempt verify cleans up
#: ``~``-suffixed residue via ``git clean``. Ladder rung 1.
C5_DIR_FILE = "C5"

#: C6 — Binary conflict. No markers; the resolver picks a side
#: (ours/theirs) with rationale; otherwise fall to endpoint
#: merge. Ladder rung 1.
C6_BINARY = "C6"

#: C7 — Submodule / gitlink. Deterministic pick: descendant
#: commit contains the other (``git merge-base --is-ancestor``);
#: else endpoint merge. The earlier behavior of silently
#: abandoning is closed. Ladder rung 1.
C7_GITLINK = "C7"

#: C8 — Symlink conflicts. Marker-less; the resolver chooses /
#: authors the link. Ladder rung 1.
C8_SYMLINK = "C8"

#: C9 — Mode-only conflict (100644 vs 100755). Deterministic
#: pick (prefer the target's mode unless the feature changed it
#: deliberately); ``core.fileMode``-aware because the repo lives
#: on an external volume. Ladder rung 1.
C9_MODE_ONLY = "C9"

#: C10 — Custom merge drivers. Hung driver -> per-call timeout
#: -> clean abort -> endpoint merge retry. Ladder rung 3.
C10_MERGE_DRIVER = "C10"

#: C11 — Line-ending churn. Resolver handles as content conflict;
#: never auto-pick a side. Ladder rung 1.
C11_LINE_ENDING = "C11"

#: C12 — Rename-detection limit. Detected warning -> single
#: retry with raised ``-c merge.renameLimit``. Ladder rung 1.
C12_RENAME_LIMIT = "C12"

#: C13 — Conflict-marker-sized legit content. Marker scan
#: tolerates lone ``=======``; only ``<<<<<<< `` / ``>>>>>>> ``
#: pairs are fences
#: (:data:`ralph.git.merge._UNRESOLVED_CONFLICT_FENCES`).
#: Ladder rung 1.
C13_MARKER_TOLERANCE = "C13"

#: C14 — ``merge.conflictStyle``. The marker scan tolerates all
#: three styles; style is only pinned for paths that parse
#: hunks. Ladder rung 1.
C14_CONFLICT_STYLE = "C14"

#: C15 — Resolver misbehavior. The post-stage marker scan and
#: unmerged-paths gate reject edits outside the conflict set;
#: empty resolutions are answered with ``--skip`` / ``--allow-empty``;
#: resolver timeout / drain down -> clean abort + retry.
#: Ladder rung 1.
C15_RESOLVER_MISBEHAVIOR = "C15"

#: D1 — Editors/pagers/prompts. Pinned in
#: :data:`ralph.git.subprocess_runner._GIT_BATCH_MODE_ENV`:
#: ``GIT_EDITOR=``, ``GIT_SEQUENCE_EDITOR=``, ``EDITOR=``,
#: ``VISUAL=``, ``GIT_PAGER=cat``, ``GIT_TERMINAL_PROMPT=0``,
#: ``GCM_INTERACTIVE=Never``. Never passes ``-i``. Ladder rung 1.
D1_NON_INTERACTIVE = "D1"

#: D2 — Hooks. Pre-rebase rejection is classified and recorded;
#: hung hooks hit the universal per-call timeout; pre-push
#: rejection is recorded per-remote and never blocks the local
#: result (R3). Ladder rung 3.
D2_HOOKS = "D2"

#: D3 — Rerere. ``-c rerere.enabled=false`` is part of
#: :data:`ralph.git.hardening.PINNED_CONFIG_ARGS`; a recorded
#: wrong resolution cannot be silently committed. Ladder rung 1.
D3_RERERE = "D3"

#: D4 — Autostash. ``--no-autostash`` on every rebase; dirty
#: trees never start integration (precondition gate). Ladder
#: rung 1.
D4_AUTOSTASH = "D4"

#: D5 — Autosquash. ``--no-autosquash`` on every rebase; user
#: config cannot turn it on. Ladder rung 1.
D5_AUTOSQUASH = "D5"

#: D6 — Commit signing. ``-c commit.gpgsign=false`` and
#: ``-c tag.gpgsign=false`` in PINNED_CONFIG_ARGS; a locked
#: key or pinentry prompt cannot hang or fail a replayed
#: commit. Ladder rung 1.
D6_SIGNING = "D6"

#: D7 — Missing identity. ``_ensure_git_identity`` checks
#: ``user.name`` / ``user.email`` and raises with the exact
#: remediation; re-checked at every seam. Ladder rung 4.
D7_IDENTITY = "D7"

#: D8 — Fsmonitor. ``-c core.fsmonitor=false`` in
#: PINNED_CONFIG_ARGS; phantom-dirty from a wedged daemon
#: cannot block integration. Ladder rung 1.
D8_FSMONITOR = "D8"

#: D9 — LFS / clean-smudge filters. Per-call timeout -> clean
#: abort -> retry; classified distinctly. Ladder rung 3.
D9_LFS = "D9"

#: D10 — Sparse checkout. ``_check_sparse_checkout_state`` reads
#: ``core.sparseCheckout`` / ``sparseCheckoutCone``; a post-
#: rebase ``git sparse-checkout reapply`` fixes incoherent state
#: rather than refusing. Ladder rung 1.
D10_SPARSE = "D10"

#: D11 — Case-insensitive FS. Detects case-only renames in the
#: replay range; routes to endpoint merge on a checkout
#: failure rather than looping on a phantom-dirty tree.
#: Ladder rung 1.
D11_CASE_INSENSITIVE = "D11"

#: D12 — Paths with spaces / newlines / unicode. All porcelain
#: parsing is NUL-delimited via :mod:`ralph.git.hardening`. The
#: repo path itself contains a space; path safety is built into
#: the parser. Ladder rung 1.
D12_PATH_SAFETY = "D12"

#: D13 — GIT_* env leakage. :data:`ralph.git.subprocess_runner._SCRUBBED_GIT_ENV_KEYS`
#: removes inherited GIT_DIR / GIT_WORK_TREE / GIT_INDEX_FILE /
#: GIT_COMMON_DIR at every precedence level. Ladder rung 1.
D13_ENV_SCRUB = "D13"

#: D14 — Localized / changing stderr. ``LC_ALL=C`` in
#: _GIT_BATCH_MODE_ENV; classification reads the index / refs
#: / exit codes first, stderr substring matching only as a
#: fallback. Ladder rung 1.
D14_LOCALE = "D14"

#: D15 — Universal timeout + signal discipline.
#: :data:`ralph.timeout_defaults.GIT_SUBPROCESS_TIMEOUT_SECONDS`
#: bounds every git call; the post-attempt verify runs even on
#: timeout. Ladder rung 3.
D15_TIMEOUT = "D15"

#: E1 — Feature branch rebased only from its own worktree. The
#: ``worktree_lookup`` helper in :mod:`ralph.git.merge` is the
#: single source of truth for "who has this branch checked
#: out"; a failed query fails closed. Ladder rung 4 (loud).
E1_WORKTREE_LOOKUP = "E1"

#: E2 — Fast-forwarding a target checked out in another
#: worktree. ``fast_forward_via_worktree`` (``git merge
#: ``--ff-only`` in the holding worktree) is the FIRST attempt;
#: the CAS is the fallback. The CAS path logs a WARN when it
#: leaves a dirty checkout's index behind. Ladder rung 1.
E2_FF_VIA_WORKTREE = "E2"

#: E3 — Fast-forward race between agents. ``compare_and_swap_branch``
#: with an explicit expected-old SHA; the bounded retry uses
#: jittered backoff; the loser never force-writes. Ladder rung 3.
E3_FF_RACE = "E3"

#: E4 — Target ref deleted / renamed / unresolvable mid-run.
#: ``observe_branch_sha`` distinguishes absent (rc==1) from
#: unreadable (rc>=2); a target resolution failure is
#: retryable, distinguished from absence. Ladder rung 3.
E4_TARGET_RESOLVE = "E4"

#: E5 — Concurrent gc/prune in any worktree. Backup ref
#: ``refs/rebase-backup/<id>`` keeps in-flight objects
#: reachable; packed-refs / gc.pid lock contention is
#: retryable-with-backoff. Ladder rung 3.
E5_GC = "E5"

#: E6 — ``worktree prune`` vs removable volumes. The pipeline
#: never runs ``git worktree prune``; a worktree whose volume
#: is unmounted must not have its admin dir reaped. Ladder rung 1.
E6_NO_PRUNE = "E6"

#: E7 — Per-worktree vs shared namespaces. ``_common_git_dir`` /
#: ``git rev-parse --git-path`` resolves per-worktree state
#: correctly; shared refs are read from the common dir.
#: Ladder rung 1.
E7_PATH_RESOLUTION = "E7"

#: E8 — Config writes. The pipeline never writes ``git config
#: ``--local``; all settings are per-invocation ``-c``. Ladder
#: rung 1.
E8_CONFIG_WRITES = "E8"

#: E9 — Live lock contention. ``_lock_holder_is_dead`` returns
#: False for a live holder; the bounded retry backs off and the
#: reclaim does not delete the lock. Ladder rung 3.
E9_LIVE_LOCK = "E9"

#: E10 — Broken worktree administrivia. The precondition check
#: fails closed with rung-4 diagnostic + the repair command
#: (``git worktree repair``); re-checked at every seam.
#: Ladder rung 4.
E10_BROKEN_GITDIR = "E10"

#: E11 — Two integrations in one worktree. One durable
#: ownership record per worktree; a retained record blocks a
#: FRESH integration; the record is retried, never overwritten,
#: never silently dropped. Ladder rung 1.
E11_OWNERSHIP = "E11"

#: F1 — CAS semantics. ``compare_and_swap_branch`` always
#: takes an explicit expected-old SHA; an empty old-value or
#: omitted old-value is a bug. ``update-ref -m <reason>``
#: explains the reflog. Ladder rung 1.
F1_CAS = "F1"

#: F2 — Ref resolve-failure during CAS. ``observe_branch_sha``
#: is the observe primitive; failure is the retryable
#: ``_TARGET_QUERY_FAILED``, not corruption. Ladder rung 3.
F2_RESOLVE_FAILURE = "F2"

#: F3 — Packed vs loose refs. The pipeline never reads ref
#: files directly; only ``rev-parse`` / ``show-ref`` /
#: ``for-each-ref``; ``packed-refs.lock`` contention is
#: retryable. Ladder rung 3.
F3_NO_DIRECT_READ = "F3"

#: F4 — Symbolic target ref. Preflight ``git symbolic-ref -q``;
#: a symbolic target is rung-4 (updating it would move the
#: pointee). Ladder rung 4.
F4_SYMBOLIC = "F4"

#: F5 — Ref-name case-folding on macOS. Preflight
#: ``for-each-ref`` duplicate detection; rung-4 on collision.
#: Ladder rung 4.
F5_CASE_FOLD = "F5"

#: F6 — Recovery of a landed-but-unrecorded fast-forward.
#: ``_refresh_before_verdict`` re-reads the target pointer
#: BEFORE either verdict; a refresh that cannot establish a
#: fresh pointer fails closed with a retryable skip and the
#: record retained. Ladder rung 3.
F6_RECOVERY = "F6"

#: G1 — ``--continue`` gates. ``continue_rebase`` only fires
#: after the index is unmerged-free and the marker scan
#: passes; a ``--continue`` that stops on the next conflicting
#: commit is PROGRESS. Ladder rung 1.
G1_CONTINUE = "G1"

#: G2 — Empty-resolution stop. ``--skip`` is issued by
#: ``_rebase_stop_reports_empty`` (B4). The engine owns this
#: transition. Ladder rung 1.
G2_SKIP = "G2"

#: G3 — ``--abort`` verification. ``_head_matches_sha``
#: verifies HEAD == recorded pre-attempt SHA; never
#: ``ORIG_HEAD`` (G3).
G3_ABORT_VERIFY = "G3"

#: G4 — ``--quit`` is forbidden. ``_detached_head_no_state``
#: detects quit-residue; ``_reattach_head_to_branch`` restores
#: the branch. Ladder rung 1.
G4_QUIT = "G4"

#: G5 — Backend pinning. ``-c rebase.backend=merge`` is part of
#: :data:`ralph.git.hardening.PINNED_CONFIG_ARGS`; the apply
#: backend has unrecoverable-interrupt states. Ladder rung 1.
G5_BACKEND = "G5"

#: H1 — Shallow clone. ``_check_shallow_clone`` reads the
#: common-dir shallow marker; raises with the exact
#: ``git fetch --unshallow`` remediation. Re-checked at every
#: seam (rung 4 self-resume). Ladder rung 4.
H1_SHALLOW = "H1"

#: H2 — Partial / promisor clone. ``extensions.partialClone`` is
#: preflighted; ``rev-list --objects --missing=print`` walks
#: the replay range to surface missing objects; rung-4 if
#: objects are missing. Ladder rung 4.
H2_PARTIAL = "H2"

#: H3 — Grafts / ``info/grafts``. Presence is rung-4 (the fake
#: ancestry poisons merge-base and patch-id dedup). Ladder rung 4.
H3_GRAFTS = "H3"

#: H4 — Replace refs. ``GIT_NO_REPLACE_OBJECTS=1`` in
#: ``_GIT_BATCH_MODE_ENV``; produced history never silently
#: depends on ``refs/replace/*``. Ladder rung 1.
H4_REPLACE = "H4"

#: H5 — Object corruption. Cheap
#: ``git rev-list --quiet <target>..HEAD`` walk; rung-4 with
#: ``git fsck`` remediation. Ladder rung 4.
H5_CORRUPTION = "H5"

#: H6 — Unborn branch. ``rebase_onto`` returns
#: ``RebaseNoOp('Repository has no commits yet (unborn "
#: "branch)')``; once commits exist, the fast-forward is
#: still eligible. Ladder rung 1.
H6_UNBORN = "H6"

#: H7 — Detached HEAD at a seam (operator intent). Skipped and
#: re-checked at the next seam; ``_detached_head_no_state``
#: distinguishes residue (A11/G4) from intent by the absence
#: of an active state dir. Ladder rung 1.
H7_DETACHED_INTENT = "H7"


__all__ = [
    "A1_STALE_REBASE_MERGE",
    "A2_STALE_REBASE_APPLY",
    "A3_CORRUPT_REBASE_STATE",
    "A4_LONE_REBASE_HEAD",
    "A5_MERGE_HEAD",
    "A6_CHERRY_PICK_SEQUENCER",
    "A7_BISECT_DIAGNOSTIC",
    "A8_BENIGN_LEFTOVERS",
    "A9_STALE_INDEX_LOCK",
    "A10_STALE_REF_LOCK",
    "A11_DETACHED_HEAD",
    "A12_AUTOSTASH",
    "B1_UNRELATED_HISTORIES",
    "B2_CRISS_CROSS",
    "B3_MERGE_COMMITS",
    "B4_EMPTY_ON_REPLAY",
    "B5_INITIALLY_EMPTY",
    "B6_CHERRY_DEDUP",
    "B7_ROOT_COMMITS",
    "B8_FORK_POINT",
    "B9_UPDATE_REFS",
    "B10_ANCESTOR",
    "B11_GC_BACKUP",
    "C1_CONTENT_CONFLICT",
    "C2_ADD_ADD",
    "C3_MODIFY_DELETE",
    "C4_RENAME_RENAME",
    "C5_DIR_FILE",
    "C6_BINARY",
    "C7_GITLINK",
    "C8_SYMLINK",
    "C9_MODE_ONLY",
    "C10_MERGE_DRIVER",
    "C11_LINE_ENDING",
    "C12_RENAME_LIMIT",
    "C13_MARKER_TOLERANCE",
    "C14_CONFLICT_STYLE",
    "C15_RESOLVER_MISBEHAVIOR",
    "D1_NON_INTERACTIVE",
    "D2_HOOKS",
    "D3_RERERE",
    "D4_AUTOSTASH",
    "D5_AUTOSQUASH",
    "D6_SIGNING",
    "D7_IDENTITY",
    "D8_FSMONITOR",
    "D9_LFS",
    "D10_SPARSE",
    "D11_CASE_INSENSITIVE",
    "D12_PATH_SAFETY",
    "D13_ENV_SCRUB",
    "D14_LOCALE",
    "D15_TIMEOUT",
    "E1_WORKTREE_LOOKUP",
    "E2_FF_VIA_WORKTREE",
    "E3_FF_RACE",
    "E4_TARGET_RESOLVE",
    "E5_GC",
    "E6_NO_PRUNE",
    "E7_PATH_RESOLUTION",
    "E8_CONFIG_WRITES",
    "E9_LIVE_LOCK",
    "E10_BROKEN_GITDIR",
    "E11_OWNERSHIP",
    "F1_CAS",
    "F2_RESOLVE_FAILURE",
    "F3_NO_DIRECT_READ",
    "F4_SYMBOLIC",
    "F5_CASE_FOLD",
    "F6_RECOVERY",
    "G1_CONTINUE",
    "G2_SKIP",
    "G3_ABORT_VERIFY",
    "G4_QUIT",
    "G5_BACKEND",
    "H1_SHALLOW",
    "H2_PARTIAL",
    "H3_GRAFTS",
    "H4_REPLACE",
    "H5_CORRUPTION",
    "H6_UNBORN",
    "H7_DETACHED_INTENT",
]
