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

What an overnight run leaves you
--------------------------------

For readers who already understand the product and want a concrete
artifact to judge it by, here is the actual finish-receipt from the
bundled empty-name-validation example — a real, unedited handoff you
read in the morning instead of a transcript:

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

Manual paths
============

First run
---------

- :doc:`getting-started`
- :doc:`quickstart`
- :doc:`first-task-guide`
- :doc:`first-task-prompt-templates`
- :doc:`diagnostics`

Configure & operate
-------------------

- :doc:`configuration`
- :doc:`agents`
- :doc:`reference`
- :doc:`advanced-pipeline-configuration`
- :doc:`advanced-artifact-configuration`
- :doc:`advanced-mcp-configuration`
- :doc:`parallel-mode`
- :doc:`policy-explanation`

Use-case routing
----------------

- :doc:`user-stories`
- :doc:`which-agent-should-i-start-with`
- :doc:`when-unattended-coding-fits`
- :doc:`agent-compatibility`

Concepts / explanation
----------------------

- :doc:`concepts`
- :doc:`ralph-loop`
- :doc:`policy-driven-pipeline`
- :doc:`phase-routing`
- :doc:`artifact-lifecycle`
- :doc:`watchdogs-and-timeouts`
- :doc:`verification-model`

Reference
---------

- :doc:`cli`
- :doc:`mcp-tools`
- :doc:`artifacts`
- :doc:`recovery`
- :doc:`troubleshooting`
- :doc:`versioning`
- :doc:`pro-support`
- :doc:`modules`
- :doc:`quick-reference`
- :doc:`developer-reference`
- :doc:`developer-internals`
- :doc:`local-web-access`
- :doc:`prompts`
- :doc:`supervising-api`
- :doc:`transcript`
- :doc:`mcp-tool-restriction`
- :doc:`mcp-architecture`
- :doc:`policy-driven-overhaul-migration`
- :doc:`display`

Proof / framing
---------------

- :doc:`after-your-first-run`
- :doc:`reviewable-output`
- :doc:`example-review-bundle`
- :doc:`overnight-demo-real`
- :doc:`free-open-source-proof`
- :doc:`what-a-good-ai-coding-finish-receipt-looks-like`

Comparisons
-----------

- :doc:`ralph-workflow-vs-aider`
- :doc:`ralph-workflow-vs-claude-code`
- :doc:`ralph-workflow-vs-codex-cli`
- :doc:`ralph-workflow-vs-google-anti-gravity`
- :doc:`ralph-workflow-vs-opencode`

How-to articles
---------------

- :doc:`claude-code-approval-mode`
- :doc:`claude-code-automation`
- :doc:`claude-code-codex-workflow`
- :doc:`claude-code-run-until-done`
- :doc:`run-claude-code-overnight-without-babysitting`
- :doc:`remote-supervision-of-coding-agents`
- :doc:`review-ai-coding-output-before-merge`
- :doc:`what-breaks-first-with-multiple-coding-agents`
- :doc:`why-worktrees-are-not-enough`
- :doc:`bounded-autonomy-for-unattended-coding`
- :doc:`how-to-tell-if-an-ai-coding-task-is-actually-done`
- :doc:`good-unattended-ai-coding-task`
- :doc:`open-source-ai-coding-orchestrator`
- :doc:`unattended-coding-agent`
- :doc:`spec-driven-ai-agent`
- :doc:`ai-agent-orchestration-cli`
- :doc:`ai-agent-workflow-composer`
- :doc:`ai-coding-workflow-automation`

.. toctree::
   :hidden:

   getting-started
   quickstart
   first-task-guide
   first-task-prompt-templates
   diagnostics
   configuration
   agents
   reference
   advanced-pipeline-configuration
   advanced-artifact-configuration
   advanced-mcp-configuration
   parallel-mode
   policy-explanation
   user-stories
   which-agent-should-i-start-with
   when-unattended-coding-fits
   agent-compatibility
   concepts
   ralph-loop
   policy-driven-pipeline
   phase-routing
   artifact-lifecycle
   watchdogs-and-timeouts
   verification-model
   cli
   mcp-tools
   artifacts
   recovery
   troubleshooting
   versioning
   pro-support
   modules
   quick-reference
   developer-reference
   developer-internals
   local-web-access
   prompts
   supervising-api
   transcript
   mcp-tool-restriction
   mcp-architecture
   policy-driven-overhaul-migration
   display
   after-your-first-run
   reviewable-output
   example-review-bundle
   overnight-demo-real
   free-open-source-proof
   what-a-good-ai-coding-finish-receipt-looks-like
   ralph-workflow-vs-aider
   ralph-workflow-vs-claude-code
   ralph-workflow-vs-codex-cli
   ralph-workflow-vs-google-anti-gravity
   ralph-workflow-vs-opencode
   claude-code-approval-mode
   claude-code-automation
   claude-code-codex-workflow
   claude-code-run-until-done
   run-claude-code-overnight-without-babysitting
   remote-supervision-of-coding-agents
   review-ai-coding-output-before-merge
   what-breaks-first-with-multiple-coding-agents
   why-worktrees-are-not-enough
   bounded-autonomy-for-unattended-coding
   how-to-tell-if-an-ai-coding-task-is-actually-done
   good-unattended-ai-coding-task
   open-source-ai-coding-orchestrator
   unattended-coding-agent
   spec-driven-ai-agent
   ai-agent-orchestration-cli
   ai-agent-workflow-composer
   ai-coding-workflow-automation