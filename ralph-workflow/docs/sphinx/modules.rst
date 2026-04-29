Python API Reference
====================

This section documents every public Ralph Workflow subpackage. The structure mirrors
the package map in ``ralph-workflow/README.md``.

----

Top-Level
---------

ralph
~~~~~

.. automodule:: ralph
   :no-members:

ralph.main
~~~~~~~~~~

.. automodule:: ralph.main
   :members:
   :show-inheritance:

ralph.banner
~~~~~~~~~~~~

.. automodule:: ralph.banner
   :members:
   :show-inheritance:

ralph.logging
~~~~~~~~~~~~~

.. automodule:: ralph.logging
   :members:
   :show-inheritance:

ralph.install
~~~~~~~~~~~~~

.. automodule:: ralph.install
   :members:
   :show-inheritance:

ralph.platform
~~~~~~~~~~~~~~

.. automodule:: ralph.platform
   :no-members:

ralph.platform.detection
~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.platform.detection
   :members:
   :show-inheritance:

ralph.platform.models
~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.platform.models
   :members:
   :show-inheritance:

ralph.verify_timeout
~~~~~~~~~~~~~~~~~~~~

.. Note: ralph.verify_timeout contains a policy document as its module docstring;
   using :no-members: to avoid RST parse errors from indented policy blocks.

.. automodule:: ralph.verify_timeout
   :no-members:

ralph.__main__
~~~~~~~~~~~~~~

.. Note: ralph.__main__ is the ``python -m ralph`` entry point; using :no-members:
   since it contains no public API beyond invoking the CLI.

.. automodule:: ralph.__main__
   :no-members:

----

CLI
---

ralph.cli
~~~~~~~~~

.. automodule:: ralph.cli
   :members:
   :show-inheritance:

ralph.cli.main
~~~~~~~~~~~~~~

.. automodule:: ralph.cli.main
   :members:
   :show-inheritance:

ralph.cli.options
~~~~~~~~~~~~~~~~~

.. automodule:: ralph.cli.options
   :members:
   :show-inheritance:

ralph.cli.commands
~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.cli.commands
   :members:
   :show-inheritance:

ralph.cli.commands.cleanup
~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.cli.commands.cleanup
   :members:
   :show-inheritance:

ralph.cli.commands.commit
~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.cli.commands.commit
   :members:
   :show-inheritance:

ralph.cli.commands.diagnose
~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.cli.commands.diagnose
   :members:
   :show-inheritance:

ralph.cli.commands.init
~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.cli.commands.init
   :members:
   :show-inheritance:

ralph.cli.commands.run
~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.cli.commands.run
   :members:
   :show-inheritance:

ralph.cli.commands.explain
~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.cli.commands.explain
   :members:
   :show-inheritance:

Config
------

ralph.config
~~~~~~~~~~~~

.. Note: ralph.config re-exports from its submodules; using :no-members: avoids
   duplicate cross-reference warnings.

.. automodule:: ralph.config
   :no-members:

ralph.config.bootstrap
~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.config.bootstrap
   :members:
   :show-inheritance:

ralph.config.enums
~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.config.enums
   :members:
   :show-inheritance:

ralph.config.loader
~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.config.loader
   :members:
   :show-inheritance:

ralph.config.mcp_loader
~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.config.mcp_loader
   :members:
   :show-inheritance:

ralph.config.mcp_models
~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.config.mcp_models
   :members:
   :show-inheritance:

ralph.config.models
~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.config.models
   :members:
   :show-inheritance:

ralph.config.welcome
~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.config.welcome
   :members:
   :show-inheritance:

----

Policy
------

ralph.policy
~~~~~~~~~~~~

.. automodule:: ralph.policy
   :no-members:

ralph.policy.loader
~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.policy.loader
   :members:
   :show-inheritance:

ralph.policy.models
~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.policy.models
   :members:
   :show-inheritance:

ralph.policy.validation
~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.policy.validation
   :members:
   :show-inheritance:

ralph.policy.explain
~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.policy.explain
   :members:
   :show-inheritance:

ralph.policy.render
~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.policy.render
   :members:
   :show-inheritance:

----

Pipeline
--------

ralph.pipeline
~~~~~~~~~~~~~~

.. automodule:: ralph.pipeline
   :no-members:

ralph.pipeline.checkpoint
~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.pipeline.checkpoint
   :members:
   :show-inheritance:

ralph.pipeline.cycle_baseline
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.pipeline.cycle_baseline
   :members:
   :show-inheritance:

ralph.pipeline.effects
~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.pipeline.effects
   :members:
   :show-inheritance:

ralph.pipeline.events
~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.pipeline.events
   :members:
   :show-inheritance:

ralph.pipeline.handoffs
~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.pipeline.handoffs
   :members:
   :show-inheritance:

ralph.pipeline.orchestrator
~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.pipeline.orchestrator
   :members:
   :show-inheritance:

ralph.pipeline.parallel
~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.pipeline.parallel
   :no-members:

ralph.pipeline.parallel.coordinator
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.pipeline.parallel.coordinator
   :members:
   :show-inheritance:

ralph.pipeline.parallel.mode
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.pipeline.parallel.mode
   :members:
   :show-inheritance:

ralph.pipeline.parallel.scheduler
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.pipeline.parallel.scheduler
   :members:
   :show-inheritance:

ralph.pipeline.parallel.worker_session
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.pipeline.parallel.worker_session
   :members:
   :show-inheritance:

ralph.pipeline.progress
~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.pipeline.progress
   :no-members:

ralph.pipeline.reducer
~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.pipeline.reducer
   :members:
   :show-inheritance:

ralph.pipeline.runner
~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.pipeline.runner
   :members:
   :show-inheritance:

ralph.pipeline.state
~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.pipeline.state
   :members:
   :show-inheritance:
   :no-index:

ralph.pipeline.work_units
~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.pipeline.work_units
   :members:
   :show-inheritance:

ralph.pipeline.worker_state
~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.pipeline.worker_state
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

ralph.phases.analysis
~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.phases.analysis
   :members:
   :show-inheritance:

ralph.phases.artifacts
~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.phases.artifacts
   :members:
   :show-inheritance:

ralph.phases.commit
~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.phases.commit
   :members:
   :show-inheritance:

ralph.phases.commit_logging
~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.phases.commit_logging
   :members:
   :show-inheritance:

ralph.phases.development
~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.phases.development
   :members:
   :show-inheritance:

ralph.phases.fix
~~~~~~~~~~~~~~~~

.. automodule:: ralph.phases.fix
   :members:
   :show-inheritance:

ralph.phases.integrity
~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.phases.integrity
   :members:
   :show-inheritance:

ralph.phases.planning
~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.phases.planning
   :members:
   :show-inheritance:

ralph.phases.required_artifacts
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.phases.required_artifacts
   :members:
   :show-inheritance:

ralph.phases.review
~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.phases.review
   :members:
   :show-inheritance:

ralph.phases.timing
~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.phases.timing
   :members:
   :show-inheritance:

----

Agents
------

ralph.agents
~~~~~~~~~~~~

.. automodule:: ralph.agents
   :no-members:

ralph.agents.activity
~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.agents.activity
   :members:
   :show-inheritance:

ralph.agents.availability
~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.agents.availability
   :members:
   :show-inheritance:

ralph.agents.chain
~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.agents.chain
   :members:
   :show-inheritance:

ralph.agents.completion_signals
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.agents.completion_signals
   :members:
   :show-inheritance:

ralph.agents.execution_state
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.agents.execution_state
   :members:
   :show-inheritance:

ralph.agents.executor
~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.agents.executor
   :members:
   :show-inheritance:

ralph.agents.idle_watchdog
~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.agents.idle_watchdog
   :members:
   :show-inheritance:

ralph.agents.post_exit_watchdog
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.agents.post_exit_watchdog
   :members:
   :show-inheritance:

ralph.agents.invoke
~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.agents.invoke
   :members:
   :show-inheritance:

ralph.agents.parsers
~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.agents.parsers
   :no-members:

ralph.agents.parsers.base
~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.agents.parsers.base
   :members:
   :show-inheritance:

ralph.agents.parsers.claude
~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.agents.parsers.claude
   :members:
   :show-inheritance:

ralph.agents.parsers.codex
~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.agents.parsers.codex
   :members:
   :show-inheritance:

ralph.agents.parsers.gemini
~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.agents.parsers.gemini
   :members:
   :show-inheritance:

ralph.agents.parsers.generic
~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.agents.parsers.generic
   :members:
   :show-inheritance:

ralph.agents.parsers.opencode
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.agents.parsers.opencode
   :members:
   :show-inheritance:

ralph.agents.registry
~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.agents.registry
   :members:
   :show-inheritance:

ralph.agents.subprocess_executor
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.agents.subprocess_executor
   :members:
   :show-inheritance:

----

ralph.agents.timeout_clock
~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.agents.timeout_clock
   :members:
   :show-inheritance:

----

MCP
---

ralph.mcp
~~~~~~~~~

.. automodule:: ralph.mcp
   :no-members:

ralph.mcp.artifacts
~~~~~~~~~~~~~~~~~~~

.. Note: ralph.mcp.artifacts re-exports from ralph.mcp.artifacts.store;
   using :no-members: avoids duplicate object warnings.

.. automodule:: ralph.mcp.artifacts
   :no-members:

ralph.mcp.artifacts.audit_adapter
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.mcp.artifacts.audit_adapter
   :members:
   :show-inheritance:

ralph.mcp.artifacts.bridge
~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.mcp.artifacts.bridge
   :members:
   :show-inheritance:

ralph.mcp.artifacts.commit_message
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.mcp.artifacts.commit_message
   :members:
   :show-inheritance:

ralph.mcp.artifacts.development_result
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.mcp.artifacts.development_result
   :members:
   :show-inheritance:

ralph.mcp.artifacts.file_backend
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.mcp.artifacts.file_backend
   :members:
   :show-inheritance:

ralph.mcp.artifacts.format_docs
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.mcp.artifacts.format_docs
   :members:
   :show-inheritance:

ralph.mcp.artifacts.handoffs
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.mcp.artifacts.handoffs
   :members:
   :show-inheritance:

ralph.mcp.artifacts.plan
~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.mcp.artifacts.plan
   :members:
   :show-inheritance:

ralph.mcp.artifacts.policy_outcomes
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.mcp.artifacts.policy_outcomes
   :members:
   :show-inheritance:

ralph.mcp.artifacts.store
~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.mcp.artifacts.store
   :members:
   :show-inheritance:

ralph.mcp.artifacts.typed_artifacts
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.mcp.artifacts.typed_artifacts
   :members:
   :show-inheritance:

ralph.mcp.protocol
~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.mcp.protocol
   :members:
   :show-inheritance:

ralph.mcp.protocol.capability_mapping
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.mcp.protocol.capability_mapping
   :members:
   :show-inheritance:

ralph.mcp.protocol.env
~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.mcp.protocol.env
   :members:
   :show-inheritance:

ralph.mcp.protocol.session
~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.mcp.protocol.session
   :members:
   :show-inheritance:

ralph.mcp.protocol.startup
~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.mcp.protocol.startup
   :members:
   :show-inheritance:

ralph.mcp.protocol.transport
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.mcp.protocol.transport
   :members:
   :show-inheritance:

ralph.mcp.server
~~~~~~~~~~~~~~~~

.. automodule:: ralph.mcp.server
   :members:
   :show-inheritance:

ralph.mcp.server.factory
~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.mcp.server.factory
   :members:
   :show-inheritance:

ralph.mcp.server.factory_impl
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.mcp.server.factory_impl
   :members:
   :show-inheritance:

ralph.mcp.server.lifecycle
~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.mcp.server.lifecycle
   :members:
   :show-inheritance:

ralph.mcp.server.runtime
~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.mcp.server.runtime
   :members:
   :show-inheritance:

ralph.mcp.server.__main__
~~~~~~~~~~~~~~~~~~~~~~~~~

.. Note: ralph.mcp.server.__main__ is the standalone ``ralph-mcp`` entry point;
   using :no-members: since it contains no public API beyond starting the server.

.. automodule:: ralph.mcp.server.__main__
   :no-members:

ralph.mcp.session_plan
~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.mcp.session_plan
   :members:
   :show-inheritance:

ralph.mcp.tools
~~~~~~~~~~~~~~~

.. automodule:: ralph.mcp.tools
   :members:
   :show-inheritance:

ralph.mcp.tools.artifact
~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.mcp.tools.artifact
   :members:
   :show-inheritance:

ralph.mcp.tools.bridge
~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.mcp.tools.bridge
   :members:
   :show-inheritance:

ralph.mcp.tools.coordination
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.mcp.tools.coordination
   :members:
   :show-inheritance:

ralph.mcp.tools.exec
~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.mcp.tools.exec
   :members:
   :show-inheritance:

ralph.mcp.tools.git_read
~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.mcp.tools.git_read
   :members:
   :show-inheritance:

ralph.mcp.tools.names
~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.mcp.tools.names
   :members:
   :show-inheritance:

ralph.mcp.tools.websearch
~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.mcp.tools.websearch
   :members:
   :show-inheritance:

ralph.mcp.tools.webvisit
~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.mcp.tools.webvisit
   :members:
   :show-inheritance:

ralph.mcp.tools.workspace
~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.mcp.tools.workspace
   :members:
   :show-inheritance:

ralph.mcp.transport
~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.mcp.transport
   :members:
   :show-inheritance:

ralph.mcp.transport.claude
~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.mcp.transport.claude
   :members:
   :show-inheritance:

ralph.mcp.transport.codex
~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.mcp.transport.codex
   :members:
   :show-inheritance:

ralph.mcp.transport.common
~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.mcp.transport.common
   :members:
   :show-inheritance:

ralph.mcp.transport.opencode
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.mcp.transport.opencode
   :members:
   :show-inheritance:

ralph.mcp.upstream
~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.mcp.upstream
   :members:
   :show-inheritance:

ralph.mcp.upstream.agent_probe
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.mcp.upstream.agent_probe
   :members:
   :show-inheritance:

ralph.mcp.upstream.client
~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.mcp.upstream.client
   :members:
   :show-inheritance:

ralph.mcp.upstream.config
~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.mcp.upstream.config
   :members:
   :show-inheritance:

ralph.mcp.upstream.models
~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.mcp.upstream.models
   :members:
   :show-inheritance:

ralph.mcp.upstream.registry
~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.mcp.upstream.registry
   :members:
   :show-inheritance:

ralph.mcp.upstream.validation
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.mcp.upstream.validation
   :members:
   :show-inheritance:

ralph.mcp.webvisit
~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.mcp.webvisit
   :members:
   :show-inheritance:

ralph.mcp.webvisit.extractor
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.mcp.webvisit.extractor
   :members:
   :show-inheritance:

ralph.mcp.webvisit.fetcher
~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.mcp.webvisit.fetcher
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

ralph.mcp.websearch.backends.base
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.mcp.websearch.backends.base
   :members:
   :show-inheritance:

ralph.mcp.websearch.backends.brave
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.mcp.websearch.backends.brave
   :members:
   :show-inheritance:

ralph.mcp.websearch.backends.ddgs
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.mcp.websearch.backends.ddgs
   :members:
   :show-inheritance:

ralph.mcp.websearch.backends.exa
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.mcp.websearch.backends.exa
   :members:
   :show-inheritance:

ralph.mcp.websearch.backends.searxng
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.mcp.websearch.backends.searxng
   :members:
   :show-inheritance:

ralph.mcp.websearch.backends.tavily
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.mcp.websearch.backends.tavily
   :members:
   :show-inheritance:

ralph.mcp.websearch.secrets
~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.mcp.websearch.secrets
   :members:
   :show-inheritance:

----

Git
---

ralph.git
~~~~~~~~~

.. automodule:: ralph.git
   :members:
   :show-inheritance:

ralph.git.executor
~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.git.executor
   :members:
   :show-inheritance:

ralph.git.hooks
~~~~~~~~~~~~~~~

.. automodule:: ralph.git.hooks
   :members:
   :show-inheritance:

ralph.git.operations
~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.git.operations
   :members:
   :show-inheritance:

ralph.git.rebase
~~~~~~~~~~~~~~~~

.. automodule:: ralph.git.rebase
   :members:
   :show-inheritance:

ralph.git.rebase.rebase
~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.git.rebase.rebase
   :members:
   :show-inheritance:

ralph.git.rebase.rebase_checkpoint
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.git.rebase.rebase_checkpoint
   :members:
   :show-inheritance:

ralph.git.rebase.rebase_continuation
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.git.rebase.rebase_continuation
   :members:
   :show-inheritance:

ralph.git.rebase.rebase_kinds
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.git.rebase.rebase_kinds
   :members:
   :show-inheritance:

ralph.git.rebase.rebase_preconditions
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.git.rebase.rebase_preconditions
   :members:
   :show-inheritance:

ralph.git.rebase.rebase_state_machine
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.git.rebase.rebase_state_machine
   :members:
   :show-inheritance:

ralph.git.subprocess_runner
~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.git.subprocess_runner
   :members:
   :show-inheritance:

ralph.git.wrapper
~~~~~~~~~~~~~~~~~

.. automodule:: ralph.git.wrapper
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

ralph.workspace.fs
~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.workspace.fs
   :no-members:

ralph.workspace.memory
~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.workspace.memory
   :members:
   :show-inheritance:

ralph.workspace.protocol
~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.workspace.protocol
   :members:
   :show-inheritance:

ralph.workspace.scope
~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.workspace.scope
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

ralph.recovery.budget
~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.recovery.budget
   :members:
   :show-inheritance:

ralph.recovery.classifier
~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.recovery.classifier
   :members:
   :show-inheritance:

ralph.recovery.connectivity
~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.recovery.connectivity
   :members:
   :show-inheritance:

ralph.recovery.controller
~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.recovery.controller
   :members:
   :show-inheritance:

ralph.recovery.cycle_cap
~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.recovery.cycle_cap
   :members:
   :show-inheritance:

ralph.recovery.events
~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.recovery.events
   :members:
   :show-inheritance:

ralph.recovery.testing
~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.recovery.testing
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

ralph.runtime.environment
~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.runtime.environment
   :members:
   :show-inheritance:

ralph.runtime.verify_timeout
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.runtime.verify_timeout
   :no-members:

----

Process
-------

ralph.process
~~~~~~~~~~~~~

.. automodule:: ralph.process
   :members:
   :show-inheritance:

ralph.process.liveness
~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.process.liveness
   :members:
   :show-inheritance:

ralph.process.manager
~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.process.manager
   :no-members:

----

API / Cloud
-----------

ralph.api
~~~~~~~~~

.. automodule:: ralph.api
   :members:
   :show-inheritance:

ralph.api.cloud
~~~~~~~~~~~~~~~

.. automodule:: ralph.api.cloud
   :members:
   :show-inheritance:

ralph.api.opencode
~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.api.opencode
   :members:
   :show-inheritance:

ralph.cloud
~~~~~~~~~~~

.. automodule:: ralph.cloud
   :no-members:

ralph.cloud.client
~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.cloud.client
   :members:
   :show-inheritance:

----

Utilities
---------

ralph.checkpoint
~~~~~~~~~~~~~~~~

.. automodule:: ralph.checkpoint
   :no-members:

ralph.checkpoint.builder
~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.checkpoint.builder
   :members:
   :show-inheritance:

ralph.checkpoint.execution_history
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.checkpoint.execution_history
   :members:
   :show-inheritance:

ralph.checkpoint.run_context
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.checkpoint.run_context
   :members:
   :show-inheritance:

ralph.checkpoint.size_monitor
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.checkpoint.size_monitor
   :members:
   :show-inheritance:

ralph.diagnostics
~~~~~~~~~~~~~~~~~

.. automodule:: ralph.diagnostics
   :members:
   :show-inheritance:

ralph.display
~~~~~~~~~~~~~

.. automodule:: ralph.display
   :members:
   :show-inheritance:
   :exclude-members: DisplayContext, make_display_context

ralph.display.activity_model
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.display.activity_model
   :members:
   :show-inheritance:

ralph.display.activity_router
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.display.activity_router
   :members:
   :show-inheritance:

ralph.display.artifact_reader
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.display.artifact_reader
   :no-members:

ralph.display.artifact_renderer
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.display.artifact_renderer
   :members:
   :show-inheritance:

ralph.display.context
~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.display.context
   :members:
   :show-inheritance:

ralph.display.completion_summary
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.display.completion_summary
   :members:
   :show-inheritance:

ralph.display.content_condenser
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.display.content_condenser
   :members:
   :show-inheritance:

ralph.display.lifecycle_filter
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.display.lifecycle_filter
   :no-members:

ralph.display.line_sanitizer
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.display.line_sanitizer
   :members:
   :show-inheritance:

ralph.display.long_content_summary
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.display.long_content_summary
   :members:
   :show-inheritance:

ralph.display.mode
~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.display.mode
   :members:
   :show-inheritance:

ralph.display.parallel_display
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.display.parallel_display
   :members:
   :show-inheritance:

ralph.display.phase_banner
~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.display.phase_banner
   :members:
   :show-inheritance:

ralph.display.plain_renderer
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.display.plain_renderer
   :no-members:

ralph.display.progress
~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.display.progress
   :members:
   :show-inheritance:

ralph.display.prompt_reader
~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.display.prompt_reader
   :members:
   :show-inheritance:

ralph.display.raw_overflow
~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.display.raw_overflow
   :members:
   :show-inheritance:

ralph.display.ring_buffer
~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.display.ring_buffer
   :members:
   :show-inheritance:

ralph.display.snapshot
~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.display.snapshot
   :members:
   :show-inheritance:

ralph.display.status
~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.display.status
   :members:
   :show-inheritance:

ralph.display.subscriber
~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.display.subscriber
   :members:
   :show-inheritance:

ralph.display.tables
~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.display.tables
   :members:
   :show-inheritance:

ralph.display.theme
~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.display.theme
   :members:
   :show-inheritance:

ralph.display.tool_args
~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.display.tool_args
   :members:
   :show-inheritance:

ralph.executor
~~~~~~~~~~~~~~

.. automodule:: ralph.executor
   :members:
   :show-inheritance:

ralph.executor.process
~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.executor.process
   :members:
   :show-inheritance:

ralph.exit_pause
~~~~~~~~~~~~~~~~

.. automodule:: ralph.exit_pause
   :members:
   :show-inheritance:

ralph.files
~~~~~~~~~~~

.. automodule:: ralph.files
   :members:
   :show-inheritance:

ralph.files.operations
~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.files.operations
   :members:
   :show-inheritance:

ralph.guidelines
~~~~~~~~~~~~~~~~

.. automodule:: ralph.guidelines
   :members:
   :show-inheritance:

ralph.guidelines.go
~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.guidelines.go
   :members:
   :show-inheritance:

ralph.guidelines.java
~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.guidelines.java
   :members:
   :show-inheritance:

ralph.guidelines.javascript
~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.guidelines.javascript
   :members:
   :show-inheritance:

ralph.guidelines.php
~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.guidelines.php
   :members:
   :show-inheritance:

ralph.guidelines.python
~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.guidelines.python
   :members:
   :show-inheritance:

ralph.guidelines.ruby
~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.guidelines.ruby
   :members:
   :show-inheritance:

ralph.guidelines.rust
~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.guidelines.rust
   :members:
   :show-inheritance:

ralph.guidelines.stack
~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.guidelines.stack
   :members:
   :show-inheritance:

ralph.interrupt
~~~~~~~~~~~~~~~

.. automodule:: ralph.interrupt
   :members:
   :show-inheritance:

ralph.interrupt.asyncio_bridge
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.interrupt.asyncio_bridge
   :no-members:

ralph.language_detector
~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.language_detector
   :members:
   :show-inheritance:

ralph.language_detector.extensions
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.language_detector.extensions
   :members:
   :show-inheritance:

ralph.language_detector.models
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.language_detector.models
   :members:
   :show-inheritance:

ralph.language_detector.scanner
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.language_detector.scanner
   :members:
   :show-inheritance:

ralph.language_detector.signatures
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.language_detector.signatures
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

ralph.prompts.commit
~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.prompts.commit
   :members:
   :show-inheritance:

ralph.prompts.debug_dump
~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.prompts.debug_dump
   :members:
   :show-inheritance:

ralph.prompts.developer
~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.prompts.developer
   :members:
   :show-inheritance:

ralph.prompts.materialize
~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.prompts.materialize
   :members:
   :show-inheritance:

ralph.prompts.payload_refs
~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.prompts.payload_refs
   :members:
   :show-inheritance:

ralph.prompts.reviewer
~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.prompts.reviewer
   :members:
   :show-inheritance:

ralph.prompts.system_prompt
~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.prompts.system_prompt
   :members:
   :show-inheritance:

ralph.prompts.template_context
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.prompts.template_context
   :members:
   :show-inheritance:

ralph.prompts.template_engine
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.prompts.template_engine
   :members:
   :show-inheritance:

ralph.prompts.template_parsing
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.prompts.template_parsing
   :members:
   :show-inheritance:

ralph.prompts.template_registry
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.prompts.template_registry
   :members:
   :show-inheritance:

ralph.prompts.template_variables
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.prompts.template_variables
   :no-members:

ralph.prompts.types
~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.prompts.types
   :members:
   :show-inheritance:
   :exclude-members: CapabilitySet,PolicyFlag,PolicyFlagSet

----

Testing
-------

.. Note: Every public package under ralph/ must have an autodoc entry here.
   When adding a new package, add the corresponding ~~~ section.

ralph.testing
~~~~~~~~~~~~~

.. Note: ralph.testing re-exports timeout helpers from ralph.verify_timeout;
   using :no-members: avoids duplicate cross-reference warnings.

.. automodule:: ralph.testing
   :no-members:

ralph.testing.fake_agent_executor
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.testing.fake_agent_executor
   :members:
   :show-inheritance:
