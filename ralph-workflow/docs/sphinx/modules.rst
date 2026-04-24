API Reference
=============

This section documents every public Ralph Workflow subpackage. The structure mirrors
the package map in ``ralph-workflow/README.md``.

.. contents:: Subpackages
   :local:
   :depth: 1

----

CLI
---

ralph.cli
~~~~~~~~~

.. automodule:: ralph.cli
   :members:
   :show-inheritance:

ralph.cli.commands
~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.cli.commands
   :members:
   :show-inheritance:

----

Config
------

ralph.config
~~~~~~~~~~~~

.. Note: ralph.config re-exports from its submodules; using :no-members: avoids
   duplicate cross-reference warnings.

.. automodule:: ralph.config
   :no-members:

ralph.config.models
~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.config.models
   :members:
   :show-inheritance:

----

Pipeline
--------

ralph.pipeline
~~~~~~~~~~~~~~

.. automodule:: ralph.pipeline
   :members:
   :show-inheritance:

ralph.pipeline.parallel
~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.pipeline.parallel
   :members:
   :show-inheritance:

----

Phases
------

ralph.phases
~~~~~~~~~~~~

.. Note: ralph.phases.__init__ defines PhaseContext with TYPE_CHECKING-only forward
   references; using :no-members: avoids Napoleon introspecting the partially-built
   Pydantic model during autodoc. The module docstring is still rendered.

.. automodule:: ralph.phases
   :no-members:

ralph.phases.required_artifacts
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.phases.required_artifacts
   :members:
   :show-inheritance:

----

Agents
------

ralph.agents
~~~~~~~~~~~~

.. automodule:: ralph.agents
   :members:
   :show-inheritance:

ralph.agents.availability
~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.agents.availability
   :members:
   :show-inheritance:

ralph.agents.parsers
~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.agents.parsers
   :members:
   :show-inheritance:

----

MCP
---

ralph.mcp
~~~~~~~~~

.. automodule:: ralph.mcp
   :members:
   :show-inheritance:

ralph.mcp.artifacts
~~~~~~~~~~~~~~~~~~~

.. Note: ralph.mcp.artifacts re-exports from ralph.mcp.artifacts.store;
   using :no-members: avoids duplicate object warnings.

.. automodule:: ralph.mcp.artifacts
   :no-members:

ralph.mcp.protocol
~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.mcp.protocol
   :members:
   :show-inheritance:

ralph.mcp.server
~~~~~~~~~~~~~~~~

.. automodule:: ralph.mcp.server
   :members:
   :show-inheritance:

ralph.mcp.tools
~~~~~~~~~~~~~~~

.. automodule:: ralph.mcp.tools
   :members:
   :show-inheritance:

ralph.mcp.transport
~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.mcp.transport
   :members:
   :show-inheritance:

ralph.mcp.upstream
~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.mcp.upstream
   :members:
   :show-inheritance:

ralph.mcp.websearch
~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.mcp.websearch
   :members:
   :show-inheritance:

ralph.mcp.websearch.backends
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. Note: ralph.mcp.websearch.backends re-exports from its submodules;
   using :no-members: avoids duplicate object warnings.

.. automodule:: ralph.mcp.websearch.backends
   :no-members:

----

Git
---

ralph.git
~~~~~~~~~

.. automodule:: ralph.git
   :members:
   :show-inheritance:

ralph.git.rebase
~~~~~~~~~~~~~~~~

.. automodule:: ralph.git.rebase
   :members:
   :show-inheritance:

----

Workspace
---------

ralph.workspace
~~~~~~~~~~~~~~~

.. Note: ralph.workspace re-exports from its submodules; using :no-members: avoids
   duplicate object warnings from autodoc.

.. automodule:: ralph.workspace
   :no-members:

----

Recovery
--------

ralph.recovery
~~~~~~~~~~~~~~

.. Note: ralph.recovery re-exports from its submodules; using :no-members: avoids
   duplicate cross-reference warnings.

.. automodule:: ralph.recovery
   :no-members:

ralph.recovery.classifier
~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.recovery.classifier
   :members:
   :show-inheritance:

ralph.recovery.controller
~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.recovery.controller
   :members:
   :show-inheritance:

----

API
---

ralph.api
~~~~~~~~~

.. automodule:: ralph.api
   :members:
   :show-inheritance:

----

Banner
------

ralph.banner
~~~~~~~~~~~~

.. automodule:: ralph.banner
   :members:
   :show-inheritance:

----

Checkpoint
----------

ralph.checkpoint
~~~~~~~~~~~~~~~~

.. automodule:: ralph.checkpoint
   :members:
   :show-inheritance:

----

Cloud
-----

ralph.cloud
~~~~~~~~~~~

.. automodule:: ralph.cloud
   :members:
   :show-inheritance:

----

Diagnostics
-----------

ralph.diagnostics
~~~~~~~~~~~~~~~~~

.. automodule:: ralph.diagnostics
   :members:
   :show-inheritance:

----

Display
-------

ralph.display
~~~~~~~~~~~~~

.. automodule:: ralph.display
   :members:
   :show-inheritance:

----

Executor
--------

ralph.executor
~~~~~~~~~~~~~~

.. automodule:: ralph.executor
   :members:
   :show-inheritance:

----

Exit Pause
----------

ralph.exit_pause
~~~~~~~~~~~~~~~~

.. automodule:: ralph.exit_pause
   :members:
   :show-inheritance:

----

Files
-----

ralph.files
~~~~~~~~~~~

.. automodule:: ralph.files
   :members:
   :show-inheritance:

----

Guidelines
----------

ralph.guidelines
~~~~~~~~~~~~~~~~

.. automodule:: ralph.guidelines
   :members:
   :show-inheritance:

----

Install
-------

ralph.install
~~~~~~~~~~~~~

.. automodule:: ralph.install
   :members:
   :show-inheritance:

----

Interrupt
---------

ralph.interrupt
~~~~~~~~~~~~~~~

.. automodule:: ralph.interrupt
   :members:
   :show-inheritance:

----

Language Detector
-----------------

ralph.language_detector
~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.language_detector
   :members:
   :show-inheritance:

----

Logging
-------

ralph.logging
~~~~~~~~~~~~~

.. automodule:: ralph.logging
   :members:
   :show-inheritance:

----

Main
----

ralph.main
~~~~~~~~~~

.. automodule:: ralph.main
   :members:
   :show-inheritance:

----

Platform
--------

ralph.platform
~~~~~~~~~~~~~~

.. automodule:: ralph.platform
   :members:
   :show-inheritance:

----

Policy
------

ralph.policy
~~~~~~~~~~~~

.. automodule:: ralph.policy
   :members:
   :show-inheritance:

----

Process
-------

ralph.process
~~~~~~~~~~~~~

.. automodule:: ralph.process
   :members:
   :show-inheritance:

----

Prompts
-------

ralph.prompts
~~~~~~~~~~~~~

.. automodule:: ralph.prompts
   :members:
   :show-inheritance:

ralph.prompts.developer
~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.prompts.developer
   :members:
   :show-inheritance:

ralph.prompts.reviewer
~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.prompts.reviewer
   :members:
   :show-inheritance:

ralph.prompts.commit
~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.prompts.commit
   :members:
   :show-inheritance:

----

Runtime
-------

ralph.runtime
~~~~~~~~~~~~~

.. automodule:: ralph.runtime
   :members:
   :show-inheritance:

----

Verify Timeout
--------------

ralph.verify_timeout
~~~~~~~~~~~~~~~~~~~~

.. Note: ralph.verify_timeout contains a policy document as its module docstring;
   using :no-members: to avoid RST parse errors from indented policy blocks.

.. automodule:: ralph.verify_timeout
   :no-members:
