.. title:: Free open-source unattended coding CLI

.. meta::
   :description: Ralph Workflow is a free open-source CLI that orchestrates Claude Code, Codex CLI, and OpenCode on your own machine for reviewable overnight coding work.

Ralph Workflow
==============

.. raw:: html

   <section class="hero">
     <h1 class="hero-headline">Run bigger AI coding tasks without babysitting the terminal</h1>
     <p class="hero-subtitle">Ralph Workflow is free and open source. It orchestrates the coding agents you already use on your own machine, runs planning + development + review as one unattended flow, and brings back a reviewable result instead of just a transcript and a done claim.</p>
     <div class="hero-actions">
       <a class="hero-cta" href="getting-started.html">Run your first real task →</a>
       <a class="hero-cta hero-cta-secondary" href="example-review-bundle.html">See a public review bundle first</a>
       <a class="hero-cta hero-cta-secondary" href="https://codeberg.org/RalphWorkflow/Ralph-Workflow">Inspect on Codeberg</a>
     </div>
     <p class="hero-proof-note">Best first evaluation: pick one real backlog task tonight, then ask tomorrow: <strong>would I merge this?</strong></p>
     <p class="hero-proof-note">Prefer to inspect the code before installing or follow the project where you already evaluate open source? <a href="https://codeberg.org/RalphWorkflow/Ralph-Workflow">Review, star, watch, or fork the primary Codeberg repo</a>. If GitHub is where you already track projects, the <a href="https://github.com/Ralph-Workflow/Ralph-Workflow">mirror is there too</a>.</p>
   </section>

Choose the first path that matches how you already work tonight:

- Already using **Claude Code** or **Codex CLI** and just want the fastest first run? Start with :doc:`which-agent-should-i-start-with`.
- Already using **Claude Code** and deciding whether you even need Ralph Workflow? Start with :doc:`ralph-workflow-vs-claude-code`.
- Already using **Claude Code** and specifically searching for a better automation / unattended path? Start with :doc:`claude-code-automation`.
- Already searching for how to run **Claude Code overnight without babysitting**? Start with :doc:`run-claude-code-overnight-without-babysitting`.
- Already using **Claude Code** but still stuck in approval mode or plan mode babysitting? Read :doc:`claude-code-approval-mode`.
- Already using **Codex CLI** and deciding whether you need Ralph Workflow for the morning-after handoff? Start with :doc:`ralph-workflow-vs-codex-cli`.
- Already splitting work between **Claude Code and Codex**? Jump straight to :doc:`claude-code-codex-workflow`.
- Already running **multiple agents** and wondering what actually breaks first? Read :doc:`what-breaks-first-with-multiple-coding-agents`.
- Already using **worktrees** and still not trusting the morning-after result? Read :doc:`why-worktrees-are-not-enough`.
- Want a clearer **merge decision** for the morning-after handoff? Read :doc:`review-ai-coding-output-before-merge`.
- Want the cleanest possible **finish receipt / re-entry path** before you trust the run? Read :doc:`what-a-good-ai-coding-finish-receipt-looks-like`.
- Want unattended runs to stay **bounded and fail-closed** instead of drifting all night? Read :doc:`bounded-autonomy-for-unattended-coding`.
- Keep reaching for **remote supervision** when the real problem is trusting the finish state? Read :doc:`remote-supervision-of-coding-agents`.
- Searching for an **open-source AI coding orchestrator** you can inspect on Codeberg first? Read :doc:`open-source-ai-coding-orchestrator`.
- Evaluating an **AI agent orchestration CLI** and want the practical difference? Read :doc:`ai-agent-orchestration-cli`.
- Searching for an **unattended coding agent** you can actually trust? Read :doc:`unattended-coding-agent`.
- Want a **spec-driven AI agent** instead of a prompt-first loop? Read :doc:`spec-driven-ai-agent`.
- Want proof before setup? Open the public :doc:`example-review-bundle` and judge whether the morning-after handoff looks mergeable.
- Already finished a first run and want the right public next step? Read :doc:`after-your-first-run`.

Keep the post-run branch simple:

- **Promising first run** → put the public trust signal on Codeberg by starring or watching the primary repo.
- **Rough first run** → open the matching first-run or docs/proof issue form on Codeberg.
- **Need the two-minute scorecard first** → use :doc:`after-your-first-run`.

.. note::

   New here? Read :doc:`getting-started` first — it gets you from install to first run quickly,
   without making you learn the internals first. If you are still deciding whether Ralph Workflow
   fits your work, start with :doc:`when-unattended-coding-fits`, :doc:`first-task-guide`,
   :doc:`first-task-prompt-templates`, and :doc:`reviewable-output`.

.. toctree::
   :hidden:

   agents

What a good first handoff looks like
====================================

A strong first run should be easy to judge the next morning:

1. **One bounded brief** in ``PROMPT.md``
2. **One unattended run** on your own machine with the agent you already use
3. **Checks that actually ran** instead of a draft that stops halfway
4. **Fixes attempted before handoff** so weak spots are not just pushed back to you
5. **Readable repo-local artifacts** you can open without replaying a terminal transcript
6. **One merge question:** *would I merge this?*

If that finish line is what you want, open the public :doc:`example-review-bundle`, follow
:doc:`getting-started`, or inspect Ralph Workflow on the `primary Codeberg repo <https://codeberg.org/RalphWorkflow/Ralph-Workflow>`_.

Want the deeper workflow argument before you install?
=====================================================

If you want a longer answer to what Ralph Workflow is, who it is for, why it is different, and why
it is worth trying now, these are the best supporting reads right now:

- `How to Tell if an AI Coding Task Is Actually Done <https://telegra.ph/How-to-Tell-if-an-AI-Coding-Task-Is-Actually-Done-05-19-2>`_
- `Claude Code + Codex Workflow: Plan, Build, Review <https://telegra.ph/Claude-Code--Codex-Workflow-Plan-Build-Review-05-19>`_
- :doc:`when-unattended-coding-fits`

Those reads all drive back to the same practical free-use path: use the agents you already have on
your own machine, run one real backlog task tonight, and ask tomorrow whether you would merge the
result.

What Ralph Workflow is for
==========================

Ralph Workflow is a **free and open-source** orchestration CLI for developers and technical teams
who want to hand off coding work that is **too big to babysit and too risky to trust blindly**.

What makes it different from a normal AI coding chat is the handoff: Ralph Workflow is built to
leave you with **reviewable output** in your repo — changed files, logs, artifacts, and review
context you can inspect in your normal engineering process.

Why try it now? Because you can use the agents you already trust on your own machine, run one real
backlog task tonight, and decide tomorrow whether the result is something you would actually merge.

If you prefer to inspect or follow open-source projects on GitHub, Ralph Workflow also has a synced
public mirror there. The primary repo lives on Codeberg, and that is where stars, watches, forks,
issues, and contribution history should accumulate. Use GitHub when it helps discovery; use
Codeberg for the main project relationship.

Important first-run expectation: Ralph Workflow is free and open source, but it does not replace
the coding agents you already use. Before you start, have at least one supported agent CLI already
installed and already authenticated on your own machine.

Third-party places to inspect Ralph Workflow
============================================

If you want outside discovery or comparison surfaces before you install, Ralph Workflow is also
showing up on a few developer-facing directories now:

- `GitDB project page <https://gitdb.net/ralph-workflow/ralph-workflow>`_ — inspect the GitHub mirror from a GitHub-native discovery surface.
- `SaaSHub product page <https://www.saashub.com/ralph-workflow>`_ — review the product card with the current positioning.
- `SaaSHub alternatives <https://www.saashub.com/ralph-workflow-alternatives>`_ — compare Ralph Workflow against adjacent tools in the same browse flow.
- `TechTools Launchpad listing <https://techtools.cz/tools/launchpad/?tool=71>`_ — a live developer-tools directory entry.

Those pages still drive back to the same four practical questions: Ralph Workflow is a free and
open-source tool, built for developers with work too big to babysit and too risky to trust
blindly, different because it aims to hand back reviewable output instead of a transcript, and
worth trying now because you can run one real backlog task tonight on your own machine.

Your best first test
====================

Do not start with a vague demo.

Start with one real backlog task that is:

- small enough to judge in one sitting
- clear enough that success is easy to define
- cheap to roll back if the run misses
- real enough that you would care if it worked

The evaluation question is simple:

   **Would you merge this result?**

If yes, Ralph Workflow earned a bigger task. If not, tighten the spec, checks, or task choice and
run again.

If you want proof before your own first run, inspect the public :doc:`example-review-bundle` and
judge whether that handoff looks like something you would actually trust yourself to review.

Know whether your task is a good fit
====================================

Ralph Workflow is strongest when the job is substantial enough to hand off but still bounded enough
to review honestly in the morning.

Good first runs usually look like:

- one backlog task with a clear stopping point
- acceptance criteria you can verify quickly
- a result that should come back as a clean diff, not a research memo
- work you already wanted done, not a synthetic demo

Poor first runs usually look like:

- vague exploration with no clear definition of done
- risky production surgery where rollback would be painful
- broad multi-part projects with too many moving pieces for one overnight pass
- anything where the human reviewer could not confidently answer: **would I merge this?**

If you are unsure, read :doc:`when-unattended-coding-fits` before you install. It is the fastest
way to avoid wasting your first run on the wrong task shape.

Your fastest honest first run
=============================

If you want the shortest path from curiosity to a real evaluation, use this exact flow in a real
repo you already care about:

Before you run it, make sure you already have:

- Python 3.12+
- a git repo you can safely test in
- at least one supported agent CLI already installed and already authenticated

.. code-block:: bash

   pipx install ralph-workflow
   cd /path/to/your/project
   ralph --init
   ralph --diagnose
   $EDITOR PROMPT.md
   ralph

Paste a spec this small into ``PROMPT.md``:

.. code-block:: markdown

   # Goal

   Add validation so the CLI rejects empty project names before creating files.
   Keep the rest of the flow unchanged.

   ## Acceptance criteria

   - Empty or whitespace-only project names fail with a clear error
   - No project files are created for invalid names
   - Existing valid-name behavior stays unchanged
   - Tests cover the new validation

Then come back and ask one question:

   **Would I merge this?**

If yes, put the public trust signal on the **primary Codeberg repo**. If no, report the exact first-run friction on **Codeberg** so the next evaluator has a cleaner path. The shortest route for either outcome is :doc:`after-your-first-run`.

.. grid:: 1 2 2 2
   :gutter: 3

   .. grid-item-card:: Get started
      :link: getting-started
      :link-type: doc

      Install Ralph Workflow, point it at a repo, write ``PROMPT.md``, and launch your first unattended run.

   .. grid-item-card:: Is my task a good fit?
      :link: when-unattended-coding-fits
      :link-type: doc

      Use a quick good-fit vs bad-fit filter before you spend time on setup or a weak first run.

   .. grid-item-card:: Choose a good first task
      :link: first-task-guide
      :link-type: doc

      Pick a real backlog task that is small enough to judge, clear enough to verify, and worth trying tonight.

   .. grid-item-card:: Choose your first agent path
      :link: which-agent-should-i-start-with
      :link-type: doc

      Do not over-optimize the provider choice: start with the agent already installed and authenticated on your machine.

   .. grid-item-card:: Need the category-level explanation first?
      :link: open-source-ai-coding-orchestrator
      :link-type: doc

      See the Codeberg-first answer to what Ralph Workflow adds if you are comparing open-source AI coding orchestrators.

   .. grid-item-card:: Already use Claude Code?
      :link: ralph-workflow-vs-claude-code
      :link-type: doc

      See when staying interactive in Claude Code is enough and when a reviewable unattended handoff is the better fit.

   .. grid-item-card:: Already use Codex CLI?
      :link: ralph-workflow-vs-codex-cli
      :link-type: doc

      See when staying interactive in Codex CLI is enough and when a reviewable unattended handoff is the better fit.

   .. grid-item-card:: Already split work across Claude Code and Codex?
      :link: claude-code-codex-workflow
      :link-type: doc

      See how to keep the role split but get a cleaner finish than manual copy-paste glue.

   .. grid-item-card:: Running multiple agents already?
      :link: what-breaks-first-with-multiple-coding-agents
      :link-type: doc

      See why trust usually breaks on shared boundaries, merged-state checks, and weak handoffs before raw Git conflicts.

   .. grid-item-card:: Start from a copy-paste prompt
      :link: first-task-prompt-templates
      :link-type: doc

      Use ready-made `PROMPT.md` shapes for the most common high-confidence first runs.

   .. grid-item-card:: Already use Aider?
      :link: ralph-workflow-vs-aider
      :link-type: doc

      See when interactive AI pair programming is enough and when an unattended reviewable handoff is the better fit.

   .. grid-item-card:: Know what good output looks like
      :link: reviewable-output
      :link-type: doc

      See the kind of diff, checks, notes, and merge decision a trustworthy unattended run should hand back.

   .. grid-item-card:: Review AI output before merge
      :link: review-ai-coding-output-before-merge
      :link-type: doc

      Use the five-minute merge check: diff, finish receipt, real checks, shared boundaries, then the merge question.

   .. grid-item-card:: Know what a good finish receipt says
      :link: what-a-good-ai-coding-finish-receipt-looks-like
      :link-type: doc

      See the short morning-after handoff that should tell you what changed, what passed, and what still needs judgment.

   .. grid-item-card:: Inspect a real review bundle
      :link: example-review-bundle
      :link-type: doc

      Open a public sample prompt, result notes, review feedback, and artifact files before your own first run.

   .. grid-item-card:: What should I do after the first run?
      :link: after-your-first-run
      :link-type: doc

      Convert a promising run into a Codeberg star/watch or convert rough edges into a useful primary-repo issue.

   .. grid-item-card:: Learn the concepts
      :link: concepts
      :link-type: doc

      Learn the small set of terms that matter when you run, resume, or customize a workflow.

   .. grid-item-card:: Look up commands
      :link: cli
      :link-type: doc

      Find the commands and flags you actually use in day-to-day operation.

   .. grid-item-card:: Fix common issues
      :link: troubleshooting
      :link-type: doc

      Start with the common failure modes and the shortest path to a fix.

.. toctree::
   :maxdepth: 2
   :caption: Get Started

   getting-started
   when-unattended-coding-fits
   bounded-autonomy-for-unattended-coding
   remote-supervision-of-coding-agents
   open-source-ai-coding-orchestrator
   ai-agent-orchestration-cli
   unattended-coding-agent
   spec-driven-ai-agent
   first-task-guide
   which-agent-should-i-start-with
   claude-code-automation
   run-claude-code-overnight-without-babysitting
   claude-code-approval-mode
   ralph-workflow-vs-claude-code
   ralph-workflow-vs-codex-cli
   claude-code-codex-workflow
   what-breaks-first-with-multiple-coding-agents
   why-worktrees-are-not-enough
   first-task-prompt-templates
   ralph-workflow-vs-aider
   reviewable-output
   review-ai-coding-output-before-merge
   what-a-good-ai-coding-finish-receipt-looks-like
   bounded-autonomy-for-unattended-coding
   remote-supervision-of-coding-agents
   example-review-bundle
   after-your-first-run
   quickstart
   concepts

.. toctree::
   :maxdepth: 2
   :caption: Operate

   recovery
   parallel-mode
   policy-explanation

.. toctree::
   :maxdepth: 2
   :caption: Reference

   reference

.. toctree::
   :maxdepth: 2
   :caption: Developer Reference

   developer-internals
   developer-reference
   display

.. toctree::
   :maxdepth: 1
   :caption: Troubleshoot

   troubleshooting

Related Links
=============

- `Ralph Workflow website <https://ralphworkflow.com>`_
- `Source code on Codeberg <https://codeberg.org/RalphWorkflow/Ralph-Workflow.git>`_
- `GitHub mirror <https://github.com/Ralph-Workflow/Ralph-Workflow>`_
- `Issue tracker <https://codeberg.org/RalphWorkflow/Ralph-Workflow/issues/new>`_
- `License (AGPL-3.0) <https://www.gnu.org/licenses/agpl-3.0.html>`_
