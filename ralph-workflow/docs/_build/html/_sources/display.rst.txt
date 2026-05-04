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

See also
--------

The full API reference for all display modules is available in the
:doc:`modules` page, generated from docstrings.
