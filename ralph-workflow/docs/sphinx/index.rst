.. title:: Ralph Workflow manual

Ralph Workflow
==============

Ralph Workflow is a free and open-source AI agent orchestrator for
coding work. The product positioning is stated once in the
`root README.md <https://codeberg.org/RalphWorkflow/Ralph-Workflow/src/branch/main/README.md>`_.

Install and run
---------------

The first-run sequence (install → init → diagnose → write spec →
run → review) is the single source of truth in the root
``README.md`` and ``START_HERE.md``. Run every step from a
human-operated shell outside any Ralph-managed agent session. The
``pipx install ralph-workflow`` line is the only install command
you need; the rest of the walkthrough is on the repo-root
``START_HERE.md``.

``ralph --diagnose`` is the **pre-flight check** — it shows which
baseline helpers are healthy, missing, unreachable, degraded, or
need repair before you spend a real run on them. See
:doc:`diagnostics` for what each check proves.

Where to go next
----------------

This page is the maintained operator manual home.

If you are brand new, start with :doc:`getting-started`.
If you need configuration or operator detail, start with :doc:`configuration`.
If you need docs grouped by real user goal, see :doc:`agent-compatibility`.

.. note::

   New here? Start with :doc:`getting-started` before you dive into the rest of the manual.

.. toctree::
   :hidden:
   :caption: Get started
   :maxdepth: 1

   getting-started

.. toctree::
   :hidden:
   :caption: Configure
   :maxdepth: 1

   configuration
   cli
   advanced-pipeline-configuration
   advanced-mcp-configuration
   advanced-artifact-configuration

.. toctree::
   :hidden:
   :caption: Understand
   :maxdepth: 1

   concepts
   mcp-architecture
   recovery

.. toctree::
   :hidden:
   :caption: Operate
   :maxdepth: 1

   troubleshooting
   agent-compatibility
   diagnostics
   versioning
   pro-support

.. toctree::
   :hidden:
   :caption: Develop
   :maxdepth: 1

   developer-internals
   agents
