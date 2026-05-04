Display Architecture
====================

Ralph Workflow's display layer uses a single-source-of-truth dependency injection
pattern built around :class:`~ralph.display.context.DisplayContext`.

.. contents:: On this page
   :local:
   :depth: 2

Overview
--------

Every renderer receives a ``DisplayContext`` rather than constructing its own
``Console`` or reading environment variables directly.  This keeps rendering
testable, predictable, and easy to audit.

The DI invariant
----------------

The following rules are enforced by ``tests/display/test_di_invariants.py``,
which scans every ``*.py`` under ``ralph/display/`` and ``ralph/banner.py``
at test time:

- ``Console(`` may only appear in ``ralph/display/theme.py``.
- ``Theme(`` may only appear in ``ralph/display/theme.py``.
- ``os.environ`` and ``os.getenv`` may only appear in
  ``ralph/display/context.py`` and ``ralph/display/content_condenser.py``.

To opt a line out of the invariant scan, append ``# noqa: di-allow`` to it
and document why in the same commit.

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
   * - ``Dev #N``
     - Bold sky-blue (``theme.outer_dev``)
     - Outer development cycle number (1-indexed).  Increments each time the
       pipeline completes a full development loop.
   * - ``Analysis N/cap``
     - Purple (``theme.inner_analysis``)
     - Inner analysis count within a fixer context, or the current repeat of
       an analysis phase that has a ``loop_policy``.
   * - ``Fixer #N``
     - Vermillion (``theme.fixer_iteration``)
     - Fixer iteration number (1-indexed) when the pipeline entered a fix
       loop after analysis issued a *revise* decision.
   * - ``Budget: N left``
     - Bold orange (``theme.level.warn``)
     - Remaining invocations allowed by the active budget counter.

These labels are produced by helpers in ``ralph.display.phase_status``
(``format_dev_cycle``, ``format_analysis_cycle``, ``format_fixer_cycle``,
``format_budget_remaining``) and consumed via :class:`PhaseIterationContext`
when rendering ``[phase-close]`` lines.

Phase-close line format
-----------------------

After each phase ends, a structured ``[phase-close]`` line is written to the
transcript::

    <ISO-TS> INFO META [phase-close] <glyph> phase=<name> [Dev #N] [Analysis N/cap] <produced> (elapsed=Ns, content_blocks=N, thinking_blocks=N, tool_calls=N, errors=N)

- The ``<glyph>`` prefix (``◆`` Unicode, ``*`` ASCII) appears only for
  milestone-role phases (execution, review, fix).
- Canonical iteration labels (``[Dev #N]``, ``[Analysis N/cap]``, etc.) appear
  between the phase name and the produced-artifact summary when an
  :class:`~ralph.display.phase_status.PhaseIterationContext` is provided.
- The trailing counter tuple always appears so every ``[phase-close]`` line
  carries phase-level activity metrics.

See also
--------

The full API reference for all display modules is available in the
:doc:`modules` page, generated from docstrings.
