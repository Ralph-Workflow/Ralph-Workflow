Ralph Workflow
==============

.. rst-class:: hero

   Unattended AI agent orchestration for long-running development pipelines.

   Ralph Workflow drives AI coding agents through a structured
   **planning → development → review → fix** loop. You describe what you want built in
   ``PROMPT.md``, and Ralph Workflow handles the rest: planning the implementation,
   invoking the agent, reviewing the output, applying fixes — all unattended, with full
   checkpoint and recovery support.

.. note::

   New here? Read :doc:`getting-started` first — it walks you from zero to your first
   pipeline run in minutes.

.. grid:: 1 2 2 2
   :gutter: 3

   .. grid-item-card:: Get Started
      :link: getting-started
      :link-type: doc

      Install, initialise, write your first PROMPT.md, and run the pipeline.

   .. grid-item-card:: Operate
      :link: recovery
      :link-type: doc

      Checkpoint, resume, recovery cycles, parallel worktrees.

   .. grid-item-card:: Reference
      :link: cli
      :link-type: doc

      Every CLI flag, config field, MCP tool, and Python API.

   .. grid-item-card:: Troubleshoot
      :link: troubleshooting
      :link-type: doc

      Common errors and how to fix them.

.. toctree::
   :maxdepth: 2
   :caption: Get Started

   getting-started
   quickstart
   concepts

.. toctree::
   :maxdepth: 2
   :caption: Operate

   recovery
   parallel-mode

.. toctree::
   :maxdepth: 2
   :caption: Reference

   cli
   configuration
   mcp-tools
   mcp-architecture
   agents
   artifacts
   prompts
   transcript
   modules
   local-web-access

.. toctree::
   :maxdepth: 1
   :caption: Troubleshoot

   troubleshooting

.. toctree::
   :maxdepth: 1
   :caption: Project

   versioning

Indices and tables
==================

* :ref:`genindex`
* :ref:`modindex`
* :ref:`search`
