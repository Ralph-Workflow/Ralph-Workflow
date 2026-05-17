Ralph Workflow
==============

.. raw:: html

   <section class="hero">
     <h1 class="hero-headline">Run bigger AI coding tasks without babysitting the terminal</h1>
     <p class="hero-subtitle">Ralph Workflow is free and open source. It orchestrates the coding agents you already use on your own machine, runs planning + development + review as one unattended flow, and brings back a reviewable result instead of just a transcript and a done claim.</p>
     <div class="hero-actions">
       <a class="hero-cta" href="getting-started.html">Run your first real task →</a>
       <a class="hero-cta hero-cta-secondary" href="example-review-bundle.html">See a public review bundle first</a>
     </div>
     <p class="hero-proof-note">Best first evaluation: pick one real backlog task tonight, then ask tomorrow: <strong>would I merge this?</strong></p>
     <p class="hero-proof-note">Prefer to inspect the code before installing? <a href="https://github.com/Ralph-Workflow/Ralph-Workflow">Review the GitHub mirror</a> or <a href="https://codeberg.org/RalphWorkflow/Ralph-Workflow">read the primary Codeberg repo</a>.</p>
   </section>

Choose the first path that matches how you already work tonight:

- Already using **Claude Code** or **Codex CLI** and just want the fastest first run? Start with :doc:`which-agent-should-i-start-with`.
- Already splitting work between **Claude Code and Codex**? Jump straight to :doc:`claude-code-codex-workflow`.
- Want proof before setup? Open the public :doc:`example-review-bundle` and judge whether the morning-after handoff looks mergeable.

.. note::

   New here? Read :doc:`getting-started` first — it gets you from install to first run quickly,
   without making you learn the internals first. If you are still deciding whether Ralph Workflow
   fits your work, start with :doc:`when-unattended-coding-fits`, :doc:`first-task-guide`,
   :doc:`first-task-prompt-templates`, and :doc:`reviewable-output`.

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
public mirror there. The primary repo lives on Codeberg, but you can review, star, or watch Ralph
Workflow from either place.

Important first-run expectation: Ralph Workflow is free and open source, but it does not replace
the coding agents you already use. Before you start, have at least one supported agent CLI already
installed and already authenticated on your own machine.

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

   .. grid-item-card:: Already split work across Claude Code and Codex?
      :link: claude-code-codex-workflow
      :link-type: doc

      See how to keep the role split but get a cleaner finish than manual copy-paste glue.

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

   .. grid-item-card:: Inspect a real review bundle
      :link: example-review-bundle
      :link-type: doc

      Open a public sample prompt, result notes, review feedback, and artifact files before your own first run.

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
   first-task-guide
   which-agent-should-i-start-with
   claude-code-codex-workflow
   first-task-prompt-templates
   ralph-workflow-vs-aider
   reviewable-output
   example-review-bundle
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

   agents
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

Indices and tables
==================

* :ref:`genindex`
* :ref:`modindex`
* :ref:`search`
