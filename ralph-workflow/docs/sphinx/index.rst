Ralph Workflow Documentation
============================

Ralph Workflow is an opinionated AI agent orchestration framework that turns a
``PROMPT.md`` into a verified change set.

Ralph Workflow drives AI coding agents through a structured
**planning → development → review → fix** loop. You describe what you want built,
and Ralph Workflow handles the rest: planning the implementation, running the agent,
reviewing the output, and applying fixes — all unattended.

.. note::

   New here? Read :doc:`getting-started` first — it walks you from zero to your first
   pipeline run in minutes, without assuming any prior knowledge of Ralph Workflow.

.. toctree::
   :maxdepth: 2
   :caption: Getting Started:

   getting-started
   quickstart
   concepts

.. toctree::
   :maxdepth: 2
   :caption: Operations:

   recovery
   parallel-mode

.. toctree::
   :maxdepth: 2
   :caption: Reference:

   cli
   configuration
   mcp-tools
   transcript
   modules
   local-web-access

.. toctree::
   :maxdepth: 1
   :caption: Troubleshooting:

   troubleshooting

.. toctree::
   :maxdepth: 1
   :caption: Project:

   versioning

.. grid:: 1 2 2 2
   :gutter: 3

   .. grid-item-card:: Getting Started
      :link: getting-started
      :link-type: doc

      Step-by-step first-run walkthrough — install, init, edit PROMPT.md, run.

   .. grid-item-card:: Reference
      :link: cli
      :link-type: doc

      Every CLI flag, every config field, the full Python API.

   .. grid-item-card:: Operations
      :link: recovery
      :link-type: doc

      Checkpoint, resume, recovery cycles, parallel worktrees.

   .. grid-item-card:: Troubleshooting
      :link: troubleshooting
      :link-type: doc

      Common errors and how to fix them.

Indices and tables
==================

* :ref:`genindex`
* :ref:`modindex`
* :ref:`search`
