:orphan:

Display Architecture
====================

This maintainer-facing page explains the internal display architecture built around :class:`~ralph.display.context.DisplayContext`.

If you only need to understand what appears in the terminal during a run, start with :doc:`transcript` instead.

.. contents:: On this page
   :local:
   :depth: 2

Overview
--------

Every renderer receives a ``DisplayContext`` instead of constructing its own ``Console`` or reading environment variables directly. This keeps rendering testable, predictable, and easier to audit.

The DI invariant
----------------

The following rules are enforced by ``tests/display/test_di_invariants.py``,
which scans every ``*.py`` under ``ralph/display/`` at test time:

- ``Console(`` may only appear in ``ralph/display/theme.py``.
- ``Theme(`` may only appear in ``ralph/display/theme.py``.
- ``os.environ`` and ``os.getenv`` may only appear in
  ``ralph/display/context.py`` and ``ralph/display/content_condenser.py``.

To opt a line out of the invariant scan, append ``# noqa: di-allow`` to it
and document why in the same commit.

Single display owner
-------------------

:class:`~ralph.display.parallel_display.ParallelDisplay` is the **only**
display class in Ralph Workflow. Every public display helper lives in
exactly one module (``ralph/display/parallel_display.py`` or
``ralph/display/context.py``) and is re-exported through
:mod:`ralph.display`. The complete public surface is:

.. list-table::
   :header-rows: 1
   :widths: 35 65

   * - Symbol
     - Owner
   * - :class:`~ralph.display.parallel_display.ParallelDisplay`
     - ``ralph/display/parallel_display.py``
   * - :func:`~ralph.display.parallel_display.emit_activity_line`
     - ``ralph/display/parallel_display.py``
   * - :func:`~ralph.display.parallel_display.resolve_active_display`
     - ``ralph/display/parallel_display.py``
   * - :func:`~ralph.display.parallel_display.get_display_context`
     - ``ralph/display/parallel_display.py``
   * - :func:`~ralph.display.parallel_display.phase_style_for_phase`
     - ``ralph/display/parallel_display.py``
   * - :func:`~ralph.display.parallel_display.status_text`
     - ``ralph/display/parallel_display.py``
   * - :func:`~ralph.display.parallel_display.subscriber_for_display`
     - ``ralph/display/parallel_display.py``
   * - :func:`~ralph.display.parallel_display.strip_markup`
     - ``ralph/display/parallel_display.py``

The 36 consolidated ``emit_*`` methods on ``ParallelDisplay`` (35
instance methods + the module-level ``emit_activity_line``) own every
user-facing banner, table, panel, and status surface. They are grouped
by surface below.

Run lifecycle
~~~~~~~~~~~~~

- ``emit_run_start`` — start-of-run banner with mode-aware title and
  project root.
- ``emit_run_end`` — end-of-run recap line with status symbol.
- ``emit_parsed_event`` — turn one parsed transcript event into a log
  line and (optionally) a banner.
- ``emit_analysis_result`` — render the analysis-cycle result.

Phase banners
~~~~~~~~~~~~

- ``emit_phase_start`` — show a phase-start banner from explicit
  parameters.
- ``emit_phase_start_from_entry`` — show a phase-start banner from a
  lifecycle entry model.
- ``emit_phase_transition`` — show a phase-transition banner between
  two phases.
- ``emit_phase_close`` — show a phase-close banner from explicit
  parameters.
- ``emit_phase_close_from_exit`` — show a phase-close banner from a
  lifecycle exit model.
- ``emit_phase_close_banner`` — show the rich, model-based phase-close
  banner.

Artifact renderers
~~~~~~~~~~~~~~~~~~

- ``emit_plan_artifact`` — render the plan artifact.
- ``emit_development_artifact`` — render the development artifact.
- ``emit_review_artifact`` — render the review artifact.
- ``emit_fix_artifact`` — render the fix artifact.
- ``emit_analysis_decision`` — render the analysis-decision artifact.
- ``emit_commit_message`` — render the generated commit message.
- ``emit_missing_plan_hint`` — emit the missing-plan hint.

Tables and panels
~~~~~~~~~~~~~~~~~

- ``emit_agents_table`` — render the agents table.
- ``emit_providers_table`` — render the providers table.
- ``emit_config_table`` — render the config table.
- ``emit_metrics_table`` — render the pipeline-metrics table.
- ``emit_checkpoint_summary_table`` — render the checkpoint-summary
  table.
- ``emit_diagnose_inventory_table`` — render the diagnose inventory
  table.
- ``emit_diagnose_probe_table`` — render the diagnose probe table.
- ``emit_diagnose_servers_table`` — render the diagnose servers table.
- ``emit_capability_summary`` — render the skill capability summary.
- ``emit_info_panel`` — render a titled info panel.

Status and warnings
~~~~~~~~~~~~~~~~~~

- ``emit_status`` — emit a one-line status message.
- ``emit_warning`` — emit a one-line warning (also the error path; uses
  ``theme.status.error`` styling for error text).
- ``emit_skill_failure_warning`` — emit the skills-auto-install failure
  hint.
- ``emit_fallback_next_steps`` — emit a numbered fallback next-steps
  list.

First-run and welcome
~~~~~~~~~~~~~~~~~~~~~

- ``emit_welcome_banner`` — emit the welcome ASCII banner.
- ``emit_first_run_panel`` — emit the first-run panel.

Helpers
~~~~~~~

- ``emit_blank_line`` — emit a single blank line.
- ``emit_dry_run_summary`` — emit the dry-run-mode recap block.

This contract is enforced by two test classes:

- :class:`tests.display.test_di_invariants.TestDisplayIsOnlyParallelDisplay`
  in ``tests/display/test_di_invariants.py`` (DI seam contract).
- :class:`tests.test_no_anti_drift_regression.TestParallelDisplayOwnsAllDisplayHelpers`
  in ``tests/test_no_anti_drift_regression.py`` (anti-drift regression pin).

No drift in CLI/pipeline display
--------------------------------

CLI command modules under ``ralph/cli/commands/`` and pipeline modules
under ``ralph/pipeline/`` are forbidden from constructing their own
``Console`` instances or from reading environment variables directly once
a ``DisplayContext`` is in scope. The anti-drift invariant test
:class:`tests.display.test_di_invariants.TestNoInlineConsoleConstructor`
walks every ``*.py`` under ``ralph/`` (excluding ``tests/``, ``docs/``,
and the legitimate ``ralph/display/theme.py`` source) and asserts zero
inline ``Console(`` constructions and zero module-level
``DisplayContext(...)`` calls. The companion test
:class:`tests.display.test_di_invariants.TestNoModuleLevelDisplayContext`
in ``tests/test_no_anti_drift_regression.py`` performs the same scan
specifically for ``DisplayContext`` materialisation at import time.

The :class:`tests.test_no_anti_drift_regression.TestPublicSurfaceImports`
test pins the public surface by importing all nine canonical symbols from
:mod:`ralph.display` and asserting they are all callable or class
objects — this catches accidental re-export drift before users notice.

Visual hierarchy
----------------

:class:`~ralph.display.parallel_display.ParallelDisplay` emits distinct
visual section breaks (a ``───`` rule in Unicode mode, an ASCII ``---``
fallback otherwise) between run-start, phase-close, and run-end blocks.
The rule glyph is sourced from ``ralph/display/theme.py`` via
:meth:`~ralph.display.context.DisplayContext.glyph_for` so it is
substitutable per the existing Okabe-Ito discipline. Quiet mode
(``is_quiet=True``) short-circuits every emit method that owns a banner
so no banner or log line leaks when
:func:`~ralph.display.parallel_display.resolve_active_display` is called
with ``is_quiet=True``.

The section-rule contract is enforced by
``tests/display/test_parallel_display_visual_hierarchy.py``:

- Every emit method that opens a section calls
  ``self._emit_section_rule(tag)`` in non-compact mode and is silent
  in compact mode.
- Headers use the ``theme.banner.title`` style; body cells use
  ``theme.text.muted``.
- Output is markup-free: callers do not need to escape ``[brackets]``
  or rich markup.
- Wide-mode emit methods emit a trailing ``Rule`` for visual symmetry
  around the section block.

Environment variables
---------------------

The following environment variables influence display behaviour.  All are
resolved once during ``make_display_context()``; no renderer reads the
environment after that.

**Width and mode**

.. list-table::
   :header-rows: 1
   :widths: 35 65

   * - Variable
     - Effect
   * - ``RALPH_FORCE_NARROW``
     - Any truthy value (``1``, ``true``, ``yes``, ``on``) forces ``compact``
       mode regardless of terminal width.
   * - ``COLUMNS``
     - Positive integer overrides the console's auto-detected width.

**Color**

.. list-table::
   :header-rows: 1
   :widths: 35 65

   * - Variable
     - Effect
   * - ``NO_COLOR``
     - Any value disables color.  Takes precedence over ``FORCE_COLOR``.
   * - ``FORCE_COLOR``
     - Any value forces color on non-TTY streams.

**Glyphs**

.. list-table::
   :header-rows: 1
   :widths: 35 65

   * - Variable
     - Effect
   * - ``RALPH_FORCE_ASCII``
     - Any truthy value disables Unicode glyphs; ASCII fallbacks are used
       (e.g. ``->`` instead of ``→``, ``[OK]`` instead of ``✓``).
   * - ``TERM=dumb``
     - Disables Unicode glyphs via the same fallback path as
       ``RALPH_FORCE_ASCII``.

**Streaming**

.. list-table::
   :header-rows: 1
   :widths: 35 65

   * - Variable
     - Effect
   * - ``RALPH_STREAMING_DEDUP``
     - Set to ``0``/``false``/``no``/``off`` to disable consecutive-fragment
       deduplication in streaming blocks.
   * - ``RALPH_STREAMING_CHECKPOINTS``
     - Set to ``0``/``false``/``no``/``off`` to disable periodic checkpoint
       lines during long streaming blocks.

**Long content**

.. list-table::
   :header-rows: 1
   :widths: 35 65

   * - Variable
     - Effect
   * - ``RALPH_LONG_CONTENT_SUMMARY``
     - Set to ``0``/``false``/``no``/``off`` to disable fallback-headline
       generation for long content blocks.
   * - ``RALPH_LONG_CONTENT_AI_SUMMARY``
     - Set to ``0``/``false``/``no``/``off`` to disable AI-based headline
       generation for long content blocks.

Mode thresholds
---------------

.. list-table::
   :header-rows: 1
   :widths: 15 85

   * - Mode
     - Trigger
   * - ``compact``
     - Terminal width < 60 columns, or ``RALPH_FORCE_NARROW`` is set.
   * - ``medium``
     - Terminal width 60–99 columns.
   * - ``wide``
     - Terminal width ≥ 100 columns.

In ``compact`` mode, renderers suppress secondary table columns, extra blank
lines, and descriptive Rules to fit narrow terminals.

Iteration context labels
------------------------

When the pipeline renders phase-start banners, ``[phase-close]`` lines, and the
final completion panel, it uses a set of **canonical iteration labels** that appear
consistently across all three display surfaces.

.. list-table::
   :header-rows: 1
   :widths: 30 30 40

   * - Label format
     - Style
     - Meaning
   * - ``Dev N/cap`` or ``Dev #N``
     - Bold sky-blue (``theme.outer_dev``)
     - Outer development cycle number (1-indexed).  Shows ``N/cap`` when the
       total budget is known, ``#N`` otherwise.
   * - ``Analysis N/cap`` or ``Analysis #N``
     - Purple (``theme.inner_analysis``)
     - Inner analysis loop iteration.  Shows ``N/cap`` when the loop cap is
       known, ``#N`` otherwise.
   * - ``Budget: N left``
     - Bold orange (``theme.level.warn``)
     - Remaining invocations allowed by the active budget counter.

These labels are produced by helpers in ``ralph.display.phase_status``
(``format_dev_cycle``, ``format_analysis_cycle``) and consumed via
:class:`PhaseIterationContext` when rendering ``[phase-close]`` lines.

Lifecycle view-model
--------------------

The :mod:`ralph.display.phase_lifecycle` module defines the single source of
truth for data flowing through phase-start banners, phase-close after-banners,
and the final run summary.  Three frozen dataclasses capture the lifecycle:

.. list-table::
   :header-rows: 1
   :widths: 30 70

   * - Class
     - Used by
   * - :class:`~ralph.display.phase_lifecycle.PhaseEntryModel`
     - Phase-start banners (``show_phase_start`` family).
   * - :class:`~ralph.display.phase_lifecycle.PhaseExitModel`
     - Phase-close after-banners (``emit_phase_close``).
   * - :class:`~ralph.display.phase_lifecycle.RunCompletionModel`
     - Final run-completion panel and ``[run-end]`` transcript block.

All three share the same canonical iteration fields
(``outer_dev_iteration``, ``outer_dev_cap``, ``inner_analysis``,
``inner_analysis_cap``) so every surface expresses iteration context in the
same vocabulary derived from :mod:`ralph.display.phase_status`.

Phase-close line format
-----------------------

After each phase ends, a structured ``[phase-close]`` line is written to the
transcript::

    <ISO-TS> INFO META [phase-close] <glyph> phase=<name> [Dev N/cap] [Analysis N/cap] <produced> exit=<trigger> (elapsed=Ns, content_blocks=N, thinking_blocks=N, tool_calls=N, errors=N)

- The ``<glyph>`` prefix (``◆`` Unicode, ``*`` ASCII) appears only for
  milestone-role phases (execution, review, fix).
- Canonical iteration labels (``[Dev N/cap]`` or ``[Dev #N]``,
  ``[Analysis N/cap]`` or ``[Analysis #N]``, etc.) appear between the phase
  name and the produced-artifact summary when a
  :class:`~ralph.display.phase_status.PhaseIterationContext` is provided.
- ``exit=<trigger>`` (e.g. ``exit=produced``) appears after the artifact
  summary when an ``exit_trigger`` string is supplied to ``emit_phase_close``.
  Runner code passes ``exit_trigger="produced"`` for all artifact-success paths.
- The trailing counter tuple always appears so every ``[phase-close]`` line
  carries phase-level activity metrics.

Phase-close rich banner
-----------------------

In addition to the ``[phase-close]`` transcript line, a rich visual banner is
emitted to the console at the start of each phase transition.  In medium and
wide modes the banner also includes:

- A ``↳ artifact:`` line showing what was produced (e.g.
  ``plan: 5 step(s), 2 risk(s)``), sourced from
  :attr:`~ralph.display.phase_lifecycle.PhaseExitModel.artifact_outcome`.
  This line is omitted in compact mode and when the artifact outcome is empty.
- A ``↳ stats:`` line showing per-phase activity counters (content, thinking,
  tool calls, errors), omitted when all counters are zero.
- A ``debug:`` line showing the last waiting-status breadcrumb and failure
  category when either is set, to surface failure context without requiring
  the completion summary to be read.

The runner populates ``waiting_status_line`` from the display subscriber and
``last_failure_category`` from pipeline state so these breadcrumbs appear even
when the phase exits unexpectedly.

See also
--------

The full API reference for all display modules is available in the
:doc:`modules` page, generated from docstrings.
