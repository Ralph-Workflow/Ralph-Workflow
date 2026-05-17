Ralph Workflow
==============

.. raw:: html

   <section class="hero">
     <h1 class="hero-headline">Run bigger AI coding tasks without babysitting the terminal</h1>
     <p class="hero-subtitle">Ralph Workflow keeps the workflow in your repo: write the task in <code>PROMPT.md</code>, let Ralph Workflow run planning, coding, and agent review, then come back to completed work, logs, and artifacts you can inspect in your normal git workflow.</p>
     <a class="hero-cta" href="getting-started.html">Get started →</a>
   </section>

.. note::

   New here? Read :doc:`getting-started` first — it gets you from install to first run quickly,
   without making you learn the internals first. If you are still deciding whether Ralph Workflow
   fits your work, start with :doc:`first-task-guide`, :doc:`first-task-prompt-templates`, and
   :doc:`reviewable-output`.

What Ralph Workflow is for
==========================

Ralph Workflow is a **free and open-source** orchestration CLI for developers and technical teams
who want to hand off coding work that is **too big to babysit and too risky to trust blindly**.

What makes it different from a normal AI coding chat is the handoff: Ralph Workflow is built to
leave you with **reviewable output** in your repo — changed files, logs, artifacts, and review
context you can inspect in your normal engineering process.

Why try it now? Because you can use the agents you already trust on your own machine, run one real
backlog task tonight, and decide tomorrow whether the result is something you would actually merge.

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

.. grid:: 1 2 2 2
   :gutter: 3

   .. grid-item-card:: Get started
      :link: getting-started
      :link-type: doc

      Install Ralph Workflow, point it at a repo, write ``PROMPT.md``, and launch your first unattended run.

   .. grid-item-card:: Choose a good first task
      :link: first-task-guide
      :link-type: doc

      Pick a real backlog task that is small enough to judge, clear enough to verify, and worth trying tonight.

   .. grid-item-card:: Start from a copy-paste prompt
      :link: first-task-prompt-templates
      :link-type: doc

      Use ready-made `PROMPT.md` shapes for the most common high-confidence first runs.

   .. grid-item-card:: Know what good output looks like
      :link: reviewable-output
      :link-type: doc

      See the kind of diff, checks, notes, and merge decision a trustworthy unattended run should hand back.

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
   first-task-guide
   first-task-prompt-templates
   reviewable-output
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
- `Issue tracker <https://codeberg.org/RalphWorkflow/Ralph-Workflow/issues/new>`_
- `License (AGPL-3.0) <https://www.gnu.org/licenses/agpl-3.0.html>`_

Indices and tables
==================

* :ref:`genindex`
* :ref:`modindex`
* :ref:`search`
