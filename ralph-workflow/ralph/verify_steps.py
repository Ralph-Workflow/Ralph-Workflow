"""The ordered registry of ``make verify`` steps.

``make verify`` runs the entries of :data:`_VERIFY_STEPS` in tuple order.
This module owns those STEP DEFINITIONS; :mod:`ralph.verify` owns the budget
policy that charges them (the pinned wall/CPU budgets, the budget-tracked
index set, the known test-step labels) and the import-time invariants that
police both.

Register a new step HERE, in tuple order -- the position of an entry IS its
execution order. A step that runs a test suite must ALSO be registered in
:mod:`ralph.verify`'s ``_KNOWN_TEST_STEP_LABELS`` and
``_BUDGET_TRACKED_STEPS``: the labels/steps sync invariant fails closed
otherwise, so a test step can never escape the combined test budget.

Importing this module has no side effects beyond defining these constants.
"""

from __future__ import annotations

from typing import Final

#: One ``make verify`` step: ``(label, command, args, per_step_timeout)``.
VerifyStep = tuple[str, str, tuple[str, ...], float | None]

_VERIFY_STEP_TIMEOUT_SECONDS: Final = 30.0
#: Per-step wall-clock timeout for the non-test verification steps (ruff,
#: mypy, and the policy/lifecycle audits). Independent of the combined test
#: budget. Pinned to a minimum of 5 seconds by an import-time invariant in
#: :mod:`ralph.verify` so a step cannot be silently disabled by lowering
#: this constant. The pinned copy in :mod:`ralph.verify` must match this
#: one; the import-time invariant fails closed when the two diverge.

_TOTAL_TEST_HANG_CAP_SECONDS: Final = 180.0
#: Cumulative wall clock after which a tracked step is killed however busy
#: the host is. Three times the wall budget: a saturated host stretched the
#: suite to ~1.2x, so only a hang reaches this. Pinned to
#: ``3 * _TOTAL_TEST_BUDGET_SECONDS`` by an import-time invariant in
#: :mod:`ralph.verify`. The pinned copy in :mod:`ralph.verify` must match
#: this one; the import-time invariant fails closed when the two diverge.

# --- Verification step definitions ---
#
# Each entry: (label, command, args, per_step_timeout)
# The per_step_timeout is the MAX timeout passed to the runner for
# that single step. For test-budget-tracked steps, the actual timeout
# is further capped by the remaining cumulative budget.
#
# IMPORTANT: ANY step added here that runs test suites MUST:
#   1. Have its label added to _KNOWN_TEST_STEP_LABELS
#   2. Have its index added to _BUDGET_TRACKED_STEPS
# Both sets live in ralph/verify.py; the module-level runtime checks
# there enforce this at import time.
#
# _BUDGET_TRACKED_STEPS (ralph/verify.py): the indices within _VERIFY_STEPS
# whose elapsed wall-clock time counts against _TOTAL_TEST_BUDGET_SECONDS.
# The current tracked entry is make test, whose combined marker profile
# includes required_auto_integrate_e2e real-git tests. Any future step
# that runs tests must also be tracked. Adding test-related steps does
# NOT increase the combined budget: the cumulative tracker sums time
# across ALL tracked indices.
_VERIFY_STEPS: tuple[VerifyStep, ...] = (
    (
        "ruff check ralph/ tests/",
        "uv",
        ("run", "ruff", "check", "ralph/", "tests/"),
        _VERIFY_STEP_TIMEOUT_SECONDS,
    ),
    (
        "python -m mypy ralph/",
        "uv",
        ("run", "python", "-m", "mypy", "ralph/"),
        _VERIFY_STEP_TIMEOUT_SECONDS,
    ),
    # wt-056: the ``make test`` step is charged against the combined
    # budget via ``_BUDGET_TRACKED_STEPS``. Its per-step timeout is the
    # cumulative hang cap: the budget verdict is decided after the tracked
    # steps finish, net of host contention, so killing the suite at 60 s
    # would turn a busy host into a failure the suite did not cause.
    ("make test", "make", ("test",), _TOTAL_TEST_HANG_CAP_SECONDS),
    (
        "ruff format --check ralph/ tests/",
        "uv",
        ("run", "ruff", "format", "--check", "ralph/", "tests/"),
        _VERIFY_STEP_TIMEOUT_SECONDS,
    ),
    (
        "lint bypass audit (audit_lint_bypass)",
        "uv",
        ("run", "python", "-m", "ralph.testing.audit_lint_bypass"),
        _VERIFY_STEP_TIMEOUT_SECONDS,
    ),
    (
        "typecheck bypass audit (audit_typecheck_bypass)",
        "uv",
        ("run", "python", "-m", "ralph.testing.audit_typecheck_bypass"),
        _VERIFY_STEP_TIMEOUT_SECONDS,
    ),
    (
        "policy audit (audit_test_policy)",
        "uv",
        ("run", "python", "-m", "ralph.testing.audit_test_policy"),
        _VERIFY_STEP_TIMEOUT_SECONDS,
    ),
    (
        "mcp timeout audit (audit_mcp_timeout)",
        "uv",
        ("run", "python", "-m", "ralph.testing.audit_mcp_timeout"),
        _VERIFY_STEP_TIMEOUT_SECONDS,
    ),
    (
        "di seam audit (audit_di_seam)",
        "uv",
        ("run", "python", "-m", "ralph.testing.audit_di_seam"),
        _VERIFY_STEP_TIMEOUT_SECONDS,
    ),
    (
        "activity-aware watchdog audit (audit_activity_aware_watchdog)",
        "uv",
        ("run", "python", "-m", "ralph.testing.audit_activity_aware_watchdog"),
        _VERIFY_STEP_TIMEOUT_SECONDS,
    ),
    (
        # Drift audit that locks the watchdog consolidation: forbids
        # the legacy root watchdog sentinel at the package root, duplicate
        # ``IdleWatchdog`` / ``PostExitWatchdog`` class definitions outside
        # their canonical owner files, and ``WatchdogFireReason``
        # construction outside those same owners. See
        # docs/agents/watchdog-architecture.md for the full invariant
        # list. The audit is fast (<1s) and is NOT a test-budget step.
        "watchdog drift audit (audit_watchdog_drift)",
        "uv",
        ("run", "python", "-m", "ralph.testing.audit_watchdog_drift"),
        _VERIFY_STEP_TIMEOUT_SECONDS,
    ),
    (
        "parallelization dormant audit (audit_parallelization_dormant)",
        "uv",
        ("run", "python", "-m", "ralph.testing.audit_parallelization_dormant"),
        _VERIFY_STEP_TIMEOUT_SECONDS,
    ),
    (
        "artifact submission canonical-path audit (audit_artifact_submission_canonical_path)",
        "uv",
        ("run", "python", "-m", "ralph.testing.audit_artifact_submission_canonical_path"),
        _VERIFY_STEP_TIMEOUT_SECONDS,
    ),
    (
        "agent registry sync audit (audit_agent_registry_sync)",
        "uv",
        ("run", "python", "-m", "ralph.testing.audit_agent_registry_sync"),
        _VERIFY_STEP_TIMEOUT_SECONDS,
    ),
    (
        "agent module state audit (audit_agent_module_state)",
        "uv",
        ("run", "python", "-m", "ralph.testing.audit_agent_module_state"),
        _VERIFY_STEP_TIMEOUT_SECONDS,
    ),
    (
        "agent internal paths audit (audit_agent_internal_paths)",
        "uv",
        ("run", "python", "-m", "ralph.testing.audit_agent_internal_paths"),
        _VERIFY_STEP_TIMEOUT_SECONDS,
    ),
    (
        "social-proof gate (verify_social_proof)",
        "python3",
        ("../scripts/verify_social_proof.py",),
        _VERIFY_STEP_TIMEOUT_SECONDS,
    ),
    (
        # wt-024 memory-perf AC-03 / AC-04: AST audit that enforces the
        # resource-lifecycle contract documented in
        # ralph-workflow/docs/agents/memory-lifecycle.md (daemon=True threads,
        # with-managed HTTP clients, raw os fd creation only under ralph/process/).
        "resource lifecycle audit (audit_resource_lifecycle)",
        "uv",
        ("run", "python", "-m", "ralph.testing.audit_resource_lifecycle"),
        _VERIFY_STEP_TIMEOUT_SECONDS,
    ),
    (
        # wt-025 / AC-01, AC-04, AC-05: AST + literal-string audit that pins the
        # deterministic skill-update auto-commit contract (``chore(skills): sync
        # baseline bundle`` subject, the FIVE skill-root prefixes, AST placement of the
        # early-skip in commit_cleanup.py, existence of the new helper module).
        "skill auto-commit audit (audit_skill_auto_commit)",
        "uv",
        ("run", "python", "-m", "ralph.testing.audit_skill_auto_commit"),
        _VERIFY_STEP_TIMEOUT_SECONDS,
    ),
    (
        # wt-013 free-form skill contract: AST + literal-string audit
        # that pins the project-scope skill-mirror freshness invariant
        # so a future skill-content edit that updates the bundled
        # ``ralph/skills/content/<name>.md`` but forgets to re-run the
        # project-scope installer cannot silently leave a divergent
        # mirror and reintroduce the removed structured contract.
        # Walks every (root, skill) pair across the FIVE canonical
        # project-scope skill-root prefixes and asserts the mirrored
        # ``SKILL.md`` byte-equals ``ralph.skills.content.get_skill_content(name)``.
        # The audit derives the repository root from its own module
        # location (``Path(__file__).resolve().parents[3]``); it is an
        # audit, NOT a test suite: NOT added to ``_BUDGET_TRACKED_STEPS``
        # or ``_KNOWN_TEST_STEP_LABELS`` (does NOT count against the
        # immutable 60-second combined test budget). Inserted BESIDE the
        # existing skill auto-commit audit step so the smoke-budget
        # index ``len(_VERIFY_STEPS) - 2`` (the position of
        # ``make test-verification-smoke``) remains valid.
        "skill mirror freshness audit (audit_skill_mirror_freshness)",
        "uv",
        ("run", "python", "-m", "ralph.testing.audit_skill_mirror_freshness"),
        _VERIFY_STEP_TIMEOUT_SECONDS,
    ),
    (
        # AST-only, import-safe module-docstring floor that locks a non-empty docstring
        # on every public module under ralph/ (leaf modules AND package __init__.py).
        # complements the existing test_sphinx_modules_coverage /
        # test_sphinx_documentation_setup / test_sphinx_member_documentation checks by
        # being modules.rst-independent, exhaustive, and AST-only.
        "public docstring audit (audit_public_docstrings)",
        "uv",
        ("run", "python", "-m", "ralph.testing.audit_public_docstrings"),
        _VERIFY_STEP_TIMEOUT_SECONDS,
    ),
    (
        # AST + literal-string audit that pins the terminal-escape containment contract
        # documented in docs/agents/verification.md §'Terminal escape containment'.
        # Enforces literal-string + AST-scoped invariants across the display sinks, the
        # SpawnOptions dataclass + every call site anywhere under ralph/, both loguru
        # logging configurators, the DisplayContext sink, and the PTY child-spawn path
        # so that the hosti's full CSI+OSC containment (alternate screen, erase display,
        # private-parameter CSI, OSC, SGR) survives future refactors of those files.
        "terminal escape containment audit (audit_terminal_escape_containment)",
        "uv",
        ("run", "python", "-m", "ralph.testing.audit_terminal_escape_containment"),
        _VERIFY_STEP_TIMEOUT_SECONDS,
    ),
    (
        # Repo-structure policy: file size, one public class per module, no private
        # ralph imports in tests, no unallowlisted bypass comments. This lived ONLY in a
        # subprocess_e2e test that `make test` excludes, so it rotted silently while the
        # gate stayed green. Wired here so ANY structural regression fails verify on
        # every run, regardless of who introduced it.
        "repo structure audit (audit_repo_structure)",
        "uv",
        ("run", "python", "-m", "ralph.testing.audit_repo_structure"),
        _VERIFY_STEP_TIMEOUT_SECONDS,
    ),
    (
        # wt-039 fsevents: AST-only drift audit that locks the
        # single-recursive-root-watch consolidation in
        # ``ralph/agents/invoke/_workspace.py`` so a future refactor cannot silently
        # re-introduce multi-stream / dynamic per-directory / per-loop-iteration
        # watchdog watches that reinflate the macOS fseventsd footprint. Mirrors the
        # existing ``audit_watchdog_drift`` consolidation-lock pattern (ast +
        # Path.read_text only -- no subprocess, no sleep, no real I/O).
        "fsevents watch consolidation audit (audit_fsevents_watch_consolidation)",
        "uv",
        ("run", "python", "-m", "ralph.testing.audit_fsevents_watch_consolidation"),
        _VERIFY_STEP_TIMEOUT_SECONDS,
    ),
    (
        # wt-039 fsevents: AST-only drift audit that locks the ``buffering=8192``
        # invariant on every file-path ``logger.add(...)`` call in ``ralph/logging.py``
        # so a future refactor cannot silently regress to loguru's ``FileSink``
        # line-buffered default (one OS write per record, one fsevents notification per
        # record -- the exact per-record filesystem-mutation source the fseventsd
        # mitigation closes). Discriminates file sinks structurally (a ``/``-join
        # ``BinOp`` or ``Path(...)`` call) from callable/stream sinks
        # (``make_stderr_log_sink()``, ``sys.stderr``) so the CLI's terminal sink is not
        # false-flagged. AST + Path.read_text only -- no subprocess, no sleep, no real
        # I/O.
        "log sink buffering audit (audit_log_sink_buffering)",
        "uv",
        ("run", "python", "-m", "ralph.testing.audit_log_sink_buffering"),
        _VERIFY_STEP_TIMEOUT_SECONDS,
    ),
    (
        # Package-wide fail-closed filesystem mutation audit. Every raw write,
        # delete, replacement, copy, move, mkdir, or durability barrier under
        # ralph/ must route through a shared compliant boundary or carry a
        # local filesystem-write-ok reason marker (D1-D3).
        "filesystem write consolidation audit (audit_filesystem_write_consolidation)",
        "uv",
        ("run", "python", "-m", "ralph.testing.audit_filesystem_write_consolidation"),
        _VERIFY_STEP_TIMEOUT_SECONDS,
    ),
    (
        # Package-wide fail-closed filesystem read audit. Mirrors the
        # write-consolidation contract for R1-R4: every raw read, existence/stat probe,
        # full-file load, or directory traversal under ralph/ must route through a
        # shared compliant boundary (FileBackend, Workspace, idempotent helpers) or
        # carry a local filesystem-read-ok reason marker. AST + Path.read_text only --
        # no subprocess, no sleep, no real I/O.
        "filesystem read consolidation audit (audit_filesystem_read_consolidation)",
        "uv",
        ("run", "python", "-m", "ralph.testing.audit_filesystem_read_consolidation"),
        _VERIFY_STEP_TIMEOUT_SECONDS,
    ),
    (
        # Package-wide fail-closed lifecycle audit for P1-P4 and Ralph-chosen
        # subprocess selection. New raw polling sleeps, watchdog Observer
        # construction, and direct subprocess calls must use the lifecycle /
        # typed-process owners or carry a local reasoned exception.
        "filesystem polling/invocation ownership audit (audit_filesystem_polling_invocation)",
        "uv",
        ("run", "python", "-m", "ralph.testing.audit_filesystem_polling_invocation"),
        _VERIFY_STEP_TIMEOUT_SECONDS,
    ),
    (
        "regression-test elimination audit (audit_regression_test_elimination)",
        "uv",
        ("run", "python", "-m", "ralph.testing.audit_regression_test_elimination"),
        _VERIFY_STEP_TIMEOUT_SECONDS,
    ),
    (
        # wt-043: render-integrity audit for the packaged prompt templates. Renders
        # every top-level .jinja template through the real
        # registry/partials/render_template path across the main toggle scenarios
        # (LAST_RETRY_ERROR, ANALYSIS_FEEDBACK, HAS_GIT_WRITE,
        # HIDE_ARTIFACT_SUBMISSION_GUIDANCE) and fails on unrendered Jinja markers,
        # include-resolution errors, duplicated headings, duplicated >=120-char
        # paragraphs, 3+ blank-line runs, and doubled label lines. In-memory rendering
        # only (<2s) -- no subprocess, no sleep, no repo mutation.
        "template render-integrity audit (audit_template_render_integrity)",
        "uv",
        ("run", "python", "-m", "ralph.testing.audit_template_render_integrity"),
        _VERIFY_STEP_TIMEOUT_SECONDS,
    ),
    (
        # wt-043 Task #14: validate every fenced artifact example in packaged
        # prompt templates and format docs against its registered Markdown
        # spec. The audit performs bounded local reads and parsing only, so it
        # is not a test-budget-tracked step.
        "fenced artifact example audit (audit_fenced_artifact_examples)",
        "uv",
        ("run", "python", "-m", "ralph.testing.audit_fenced_artifact_examples"),
        _VERIFY_STEP_TIMEOUT_SECONDS,
    ),
    (
        "prompt single-sourcing audit (audit_prompt_single_sourcing)",
        "uv",
        ("run", "python", "-m", "ralph.testing.audit_prompt_single_sourcing"),
        _VERIFY_STEP_TIMEOUT_SECONDS,
    ),
    (
        # wt-045 typechecking: AST + literal-string audit that enforces the ``Type
        # assertions and casts`` section of
        # ``docs/ralph-workflow-policy/typechecking-policy.md``. The audit rejects every
        # ``cast(`` call in production code that is not (a) sound-by-construction
        # (universal leaf types), (b) confined to a named boundary helper, or (c)
        # carrying an inline ``# cast-policy: seam: <rationale>`` marker; it also
        # rejects EVERY ``cast(`` in tests/. AST + Path.read_text only -- no subprocess,
        # no sleep, no real I/O.
        "cast policy audit (audit_cast_policy)",
        "uv",
        ("run", "python", "-m", "ralph.testing.audit_cast_policy"),
        _VERIFY_STEP_TIMEOUT_SECONDS,
    ),
    (
        # wt-057: product-level workspace resource inventory completeness audit.
        # Validates the structured workspace_resource_inventory.json without repeating
        # the five independently owned filesystem audit scans: every canonical-primitive
        # owner must have an inventory entry, and every inventory site must resolve to a
        # real source symbol. AST + Path.read_text only -- no subprocess, no sleep, no
        # real I/O. Appended BEFORE the aggregate smoke step;
        "workspace resource inventory audit (audit_workspace_resource_inventory)",
        "uv",
        ("run", "python", "-m", "ralph.testing.audit_workspace_resource_inventory"),
        _VERIFY_STEP_TIMEOUT_SECONDS,
    ),
    (
        # Pins the interactive Claude session-text classification vocabulary
        # and its PTY exemption before the budget-tracked aggregate smoke step.
        "canonical session text audit (audit_canonical_session_text)",
        "uv",
        ("run", "python", "-m", "ralph.testing.audit_canonical_session_text"),
        _VERIFY_STEP_TIMEOUT_SECONDS,
    ),
    (
        # Duplicate-keyword forwarding audit: rejects any call that unpacks a
        # ``**catchall`` while also passing an explicit keyword the enclosing signature
        # does not bind. Such a call raises ``TypeError: got multiple values for keyword
        # argument`` only when a caller happens to name that keyword, so it type-checks
        # and fails in production -- which is exactly how every pipeline commit broke
        # via ``runner.execute_commit_effect``. Covers ``ralph/`` and ``tests/``. AST +
        # Path.read_text only -- no subprocess, no sleep, no real I/O.
        "kwargs forwarding audit (audit_kwargs_forwarding)",
        "uv",
        ("run", "python", "-m", "ralph.testing.audit_kwargs_forwarding"),
        _VERIFY_STEP_TIMEOUT_SECONDS,
    ),
    (
        # The complement to the forwarding audit: rejects a keyword passed to a
        # ``**opts`` callee that never reads that name, which is discarded in silence
        # rather than raising. That is how the fan-out lost its monitor-stop callback
        # and left the connectivity probe running through an interrupt. Stays silent
        # whenever the callee reads its catch-all opaquely, so it only speaks when the
        # key is provably unreachable. AST + Path.read_text only -- nothing is imported
        # or executed.
        "opts key drift audit (audit_opts_key_drift)",
        "uv",
        ("run", "python", "-m", "ralph.testing.audit_opts_key_drift"),
        _VERIFY_STEP_TIMEOUT_SECONDS,
    ),
    (
        "agent failure single owner audit (audit_agent_failure_single_owner)",
        "uv",
        ("run", "python", "-m", "ralph.testing.audit_agent_failure_single_owner"),
        _VERIFY_STEP_TIMEOUT_SECONDS,
    ),
    (
        # Every ralph module must import cleanly as the FIRST import of a fresh
        # interpreter. Tests cannot catch a cycle that only fails from one side, because
        # conftest fixes the import order; `make install` broke that way (ralph.install
        # -> ralph.display -> ... -> ralph.display.parallel_display while partially
        # initialised). AST replay of the import machinery -- nothing is imported or
        # executed.
        "import order audit (audit_import_order)",
        "uv",
        ("run", "python", "-m", "ralph.testing.audit_import_order"),
        _VERIFY_STEP_TIMEOUT_SECONDS,
    ),
    (
        # Instruction / MCP-config integrity: content from files, tool output and
        # MCP responses is DATA, never instructions. The gate checks the whole
        # tree, so it needs no baseline.
        "instruction integrity audit (audit_instruction_integrity)",
        "uv",
        ("run", "python", "-m", "ralph.testing.audit_instruction_integrity"),
        _VERIFY_STEP_TIMEOUT_SECONDS,
    ),
    (
        # Dependency verification: a NEW dependency must name a resolvable
        # source and a publisher, must be locked with artifact hashes, and must
        # not introduce an install script. The gate diffs the manifests against
        # the committed HEAD baseline.
        "dependency integrity audit (audit_dependency_integrity)",
        "uv",
        ("run", "python", "-m", "ralph.testing.audit_dependency_integrity"),
        _VERIFY_STEP_TIMEOUT_SECONDS,
    ),
    (
        # Secret scan and SAST: a committed credential, or a shipped shell
        # escape, is a finding. The gate reports only new or changed lines
        # against the committed HEAD baseline.
        "secret scan audit (audit_secret_scan)",
        "uv",
        ("run", "python", "-m", "ralph.testing.audit_secret_scan"),
        _VERIFY_STEP_TIMEOUT_SECONDS,
    ),
    (
        # Comment policy: a comment records only what the code cannot say. The
        # gate reports only new or changed lines against the committed HEAD
        # baseline, and honours an inline '# comment-ok: <reason>' marker.
        "comment policy audit (audit_comments)",
        "uv",
        ("run", "python", "-m", "ralph.testing.audit_comments"),
        _VERIFY_STEP_TIMEOUT_SECONDS,
    ),
    (
        # Test integrity: a test may not be weakened to let a change pass, and
        # shipped code may not detect the test runner. The gate reads the
        # working-tree diff against HEAD.
        "test integrity audit (audit_test_integrity)",
        "uv",
        ("run", "python", "-m", "ralph.testing.audit_test_integrity"),
        _VERIFY_STEP_TIMEOUT_SECONDS,
    ),
    (
        # Preserve all installer, multimodal, and visual smoke scenarios while
        # amortizing pytest imports and worker startup across one invocation.
        # This entire aggregate remains charged to the combined budget.
        "make test-verification-smoke",
        "make",
        ("test-verification-smoke",),
        _TOTAL_TEST_HANG_CAP_SECONDS,
    ),
    (
        "audit_mcp_agent_boundary",
        "uv",
        ("run", "python", "-m", "ralph.testing.audit_mcp_agent_boundary"),
        _VERIFY_STEP_TIMEOUT_SECONDS,
    ),
)
