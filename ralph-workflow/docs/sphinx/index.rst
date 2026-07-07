.. title:: Ralph Workflow manual

Ralph Workflow
==============

**Hand your coding agents a spec tonight. Wake up to reviewable, tested commits.**

See the canonical product positioning in the [root README.md](https://codeberg.org/RalphWorkflow/Ralph-Workflow/src/branch/main/README.md).

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
If you need configuration or operator detail, start with :doc:`configuration` or :doc:`reference`.
If you need docs grouped by real user goal, see :doc:`agent-compatibility`.

.. note::

   New here? Start with :doc:`getting-started` before you dive into the rest of the manual.

.. tip::

   Want a concrete artifact to judge the product by? See
   :doc:`example-review-bundle` for a full empty-name-validation
   finish-receipt — a real, unedited handoff you read in the
   morning instead of a transcript.

.. toctree::
   :hidden:
   :caption: First run
   :maxdepth: 1

   getting-started
   quickstart
   prompts
   first-task-guide
   first-task-prompt-templates
   diagnostics

.. toctree::
   :hidden:
   :caption: Configure & operate
   :maxdepth: 1

   configuration
   agents
   reference
   advanced-pipeline-configuration
   advanced-artifact-configuration
   advanced-mcp-configuration
   parallel-mode
   policy-explanation

.. toctree::
   :hidden:
   :caption: Concepts / explanation
   :maxdepth: 1

   concepts
   ralph-loop
   policy-driven-pipeline
   phase-routing
   artifact-lifecycle
   watchdogs-and-timeouts
   verification-model

.. toctree::
   :hidden:
   :caption: Reference
   :maxdepth: 1

   cli
   mcp-tools
   mcp-architecture
   artifacts
   recovery
   supervising-api
   troubleshooting
   versioning
   changelog
   pro-support
   modules
   quick-reference
   developer-reference
   developer-internals
   local-web-access
   mcp-tool-restriction
   display
   transcript
   agent-compatibility

.. toctree::
   :hidden:
   :caption: Proof
   :maxdepth: 1

   example-review-bundle

.. toctree::
   :hidden:
   :caption: How-to
   :maxdepth: 1

   review-ai-coding-output-before-merge

.. toctree::
   :hidden:
   :caption: Archive / Migration
   :maxdepth: 1

   policy-driven-overhaul-migration
