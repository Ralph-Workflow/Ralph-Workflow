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

Section guide
-------------

- **Getting Started** — walkthroughs: first run, quickstart, and key concepts
- **Operations** — recovery behaviour, parallel work units, and checkpoint management
- **Reference** — complete CLI flag table, configuration file reference, API docs, and web access capabilities
- **Troubleshooting** — common errors and how to fix them

Indices and tables
==================

* :ref:`genindex`
* :ref:`modindex`
* :ref:`search`
