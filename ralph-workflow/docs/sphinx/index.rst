.. title:: Ralph Workflow manual

Ralph Workflow
==============

Ralph Workflow is **the operating system for autonomous coding** — a
free and open-source **AI agent orchestrator** built on a **simple
Ralph-loop core** that becomes **powerful in composition**. It ships
with a **strong default workflow for writing software** that you can
adopt **as-is first** and extend later.

Hand it a well-specified coding task, let the agents plan, build,
verify, and fix, and come back to reviewable, tested work. See the
canonical product positioning in the
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
   :caption: First run
   :maxdepth: 1

   getting-started

.. toctree::
   :hidden:
   :caption: Configure & operate
   :maxdepth: 1

   configuration
   cli
   troubleshooting

.. toctree::
   :hidden:
   :caption: Concepts
   :maxdepth: 1

   concepts
   ralph-loop
   verification-model

.. toctree::
   :hidden:
   :caption: Reference
   :maxdepth: 1

   artifacts
   mcp-tools
   recovery
   modules
   developer-reference