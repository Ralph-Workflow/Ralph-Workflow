.. title:: Ralph Workflow manual

Ralph Workflow
==============

**Hand your coding agents a spec tonight. Wake up to reviewable, tested commits.**

Ralph Workflow is **the autopilot for coding agents** —
a free and open-source operating system for autonomous coding, an AI agent
orchestrator built around a simple Ralph-loop core that becomes powerful
through composition. It runs the coding agents you already use — Claude
Code, Codex, OpenCode, Nanocoder, AGY, or Pi.dev — on your own machine.

**Hand it a well-specified coding task, let the agents plan, build,
verify, and fix, and come back to reviewable, tested work.**

The default workflow is strong enough to adopt as-is before you customize
anything.

Install and run
---------------

The first-run sequence below matches the path used on
:doc:`getting-started`, :doc:`quickstart`, and the two
``START_HERE`` files. Run every step from a human-operated shell
outside any Ralph-managed agent session.

.. code-block:: bash

   pipx install ralph-workflow      # 1. install the autopilot
   cd /path/to/your/project         # 2. move into the repo you want agents on
   ralph --init                     # 3. scaffold .agent/ + PROMPT.md
   ralph --diagnose                 # 4. pre-flight: agents, MCP, capabilities
   $EDITOR PROMPT.md                # 5. write the task — see PROMPT.md template
   ralph                            # 6. run the unattended workflow

``ralph --diagnose`` is the **pre-flight check** — it shows which
baseline helpers are healthy, missing, unreachable, degraded, or
need repair before you spend a real run on them. See
:doc:`diagnostics` for what each check proves.

Where to go next
----------------

This page is the maintained operator manual home.

If you are brand new, start with :doc:`getting-started`.
If you need configuration or operator detail, start with :doc:`configuration` or :doc:`reference`.
If you need docs grouped by real user goal, start with :doc:`user-stories`.

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
   policy-explanation

.. toctree::
   :hidden:
   :caption: Use-case routing
   :maxdepth: 1

   user-stories
   when-unattended-coding-fits
   agent-compatibility

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
   parallel-mode
