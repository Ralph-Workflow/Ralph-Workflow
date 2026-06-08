.. title:: Ralph Workflow manual

Ralph Workflow
==============

**Hand your coding agents a spec tonight. Wake up to reviewable, tested commits.**

Ralph Workflow is a free, open-source composable loop framework that runs the coding agents you already use — Claude Code, Codex, or OpenCode — on your own machine. Simple at the center, powerful in composition.

Install and run
---------------

.. code-block:: bash

   pipx install ralph-workflow   # 1. install
   ralph --init                  # 2. scaffold .agent/ and PROMPT.md
   $EDITOR PROMPT.md             # 3. edit PROMPT.md — your spec for the run
   ralph                         # 4. run the unattended workflow

What an overnight run leaves you
--------------------------------

This is the actual finish-receipt from the bundled empty-name-validation example — a real, unedited handoff you read in the morning instead of a transcript:

.. code-block:: text

   # Development Result

   ## Outcome
   Implemented empty-name validation in the CLI create flow and added
   test coverage for empty and whitespace-only input.

   ## Changed files
   - cli/create.py
   - tests/test_create.py

   ## Checks run
   - pytest tests/test_create.py        ✓ passed
   - project formatting / lint checks    ✓ passed

   ## Reviewer focus
   - confirm validation happens before any file creation side effect
   - confirm the error message is clear enough for CLI users
   - confirm no unrelated flow changed

.. tip::

   **Star to bookmark before your overnight run.** It is the only signal we get that Ralph is working for you, and it sets what we build next: `star on Codeberg <https://codeberg.org/RalphWorkflow/Ralph-Workflow>`_. ⭐

This page is the maintained operator manual home.
If you are brand new, start with :doc:`getting-started`.
If you need configuration or operator detail, start with :doc:`configuration` or :doc:`reference`.
If you need docs grouped by real user goal, start with :doc:`user-stories`.

.. note::

   New here? Start with :doc:`getting-started` before you dive into the rest of the manual.

Manual paths
============

First run
---------

- :doc:`getting-started`
- :doc:`quickstart`
- :doc:`first-task-guide`

Configuration and customization
-------------------------------

- :doc:`configuration`
- :doc:`reference`
- :doc:`advanced-pipeline-configuration`
- :doc:`advanced-artifact-configuration`
- :doc:`advanced-mcp-configuration`
- :doc:`policy-explanation`

Use-case routing
----------------

- :doc:`user-stories`
- :doc:`which-agent-should-i-start-with`
- :doc:`when-unattended-coding-fits`

.. toctree::
   :hidden:

   getting-started
   quickstart
   first-task-guide
   configuration
   reference
   advanced-pipeline-configuration
   advanced-artifact-configuration
   advanced-mcp-configuration
   policy-explanation
   user-stories
   which-agent-should-i-start-with
   when-unattended-coding-fits
