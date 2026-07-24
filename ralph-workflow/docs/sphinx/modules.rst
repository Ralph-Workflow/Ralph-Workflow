Python API Reference
====================

This section documents every public Ralph Workflow subpackage. All modules listed below are part of the maintained Python package under `ralph-workflow/ralph/`.

----

Top-Level
---------

The top-level package exposes version metadata, the CLI entrypoint
(`ralph.main`), platform detection helpers, and the runtime verification
primitives used by ``make verify``. The package docstring lists the major
subpackages a contributor will touch most often. See
:doc:`developer-internals` for the contributor map and
:doc:`configuration` for how layered config and policy defaults are wired
together.

ralph
~~~~~

.. automodule:: ralph
   :no-members:

ralph.main
~~~~~~~~~~

.. automodule:: ralph.main
   :members:
   :show-inheritance:

ralph.logging
~~~~~~~~~~~~~

.. automodule:: ralph.logging
   :members:
   :show-inheritance:

ralph.onboarding
~~~~~~~~~~~~~~~~

.. automodule:: ralph.onboarding
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

ralph.platform.architecture
~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.platform.architecture
   :members:
   :show-inheritance:

ralph.platform.environment_info
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.platform.environment_info
   :members:
   :show-inheritance:

ralph.platform.operating_system
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.platform.operating_system
   :members:
   :show-inheritance:

ralph.contrib
~~~~~~~~~~~~~

.. automodule:: ralph.contrib
   :no-members:

ralph.contrib.cla
~~~~~~~~~~~~~~~~~

.. automodule:: ralph.contrib.cla
   :members:
   :show-inheritance:

ralph.update_check
~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.update_check
   :members:
   :show-inheritance:

ralph.update_check.compare
~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.update_check.compare
   :members:
   :show-inheritance:

ralph.update_check.environment
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.update_check.environment
   :members:
   :show-inheritance:

ralph.update_check.gating
~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.update_check.gating
   :members:
   :show-inheritance:

ralph.update_check.pypi
~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.update_check.pypi
   :members:
   :show-inheritance:

ralph.update_check.state
~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.update_check.state
   :members:
   :show-inheritance:

ralph.verify
~~~~~~~~~~~~

.. automodule:: ralph.verify
   :members:
   :show-inheritance:

ralph.timeout_defaults
~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.timeout_defaults
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

ralph.instance_status
~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.instance_status
   :members:
   :show-inheritance:

ralph.project_urls
~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.project_urls
   :members:
   :show-inheritance:

ralph.pydantic_validation_errors
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.pydantic_validation_errors
   :members:
   :show-inheritance:

ralph.logging_models
~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.logging_models
   :members:
   :show-inheritance:

ralph.logging_worker_sink
~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.logging_worker_sink
   :members:
   :show-inheritance:

ralph.session_runtime
~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.session_runtime
   :members:
   :show-inheritance:

ralph.rich_protocols
~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.rich_protocols
   :members:
   :show-inheritance:

ralph.pydantic_compat
~~~~~~~~~~~~~~~~~~~~~

.. Note: ralph.pydantic_compat defines ``RalphBaseModel`` as a TYPE_CHECKING
   static facade that re-exports ``pydantic.BaseModel`` at runtime; using
   :no-members: avoids duplicate object-description warnings from autodoc on
   the conditional class definitions.

.. automodule:: ralph.pydantic_compat
   :no-members:

ralph.test_suites
~~~~~~~~~~~~~~~~~

.. Note: ralph.test_suites contains a ``SuiteRunner`` Protocol guarded by
   ``TYPE_CHECKING`` and helper code that is only meaningful from a
   subprocess driver; using :no-members: keeps autodoc from introspecting the
   conditional protocol. The CLI entry is registered separately under
   ``ralph.cli.commands`` where it is documented in full.

.. automodule:: ralph.test_suites
   :no-members:

----

CLI
---

The CLI is a Typer application built on :doc:`cli`. It exposes every
subcommand a user runs day to day: ``init``, ``diagnose``, ``run``,
``commit``, ``cleanup``, ``explain``, ``prompt-helper``, ``smoke``, and
``star`` — plus policy helpers (``check-policy``, ``contribute``).
Each command lives in its own submodule so the CLI surface stays
discoverable and individual commands can be tested in isolation.

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

ralph.cli.commands.contribute
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.cli.commands.contribute
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

ralph.cli.commands.smoke
~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.cli.commands.smoke
   :members:
   :show-inheritance:

ralph.cli.commands.star
~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.cli.commands.star
   :members:
   :show-inheritance:

ralph.cli.commands.check_policy
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.cli.commands.check_policy
   :members:
   :show-inheritance:

ralph.cli.commands.explain
~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.cli.commands.explain
   :members:
   :show-inheritance:

ralph.cli.commands.prompt_helper
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.cli.commands.prompt_helper
   :members:
   :show-inheritance:

ralph.cli.commands.prompt_helper_prompt
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.cli.commands.prompt_helper_prompt
   :members:
   :show-inheritance:

Config
------

This group holds the Pydantic models, loaders, and bootstrap helpers
that back Ralph Workflow's layered config (CLI flag → project-local
→ user-global → bundled defaults). The merged ``UnifiedConfig`` is what
the runtime sees on every run. See :doc:`configuration` for the
operator-facing reference (which now folds in the policy-driven
overhaul migration background).

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

ralph.config.agent_detection
~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.config.agent_detection
   :members:
   :show-inheritance:

ralph.config.config_error_messages
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.config.config_error_messages
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

ralph.config.agent_config
~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.config.agent_config
   :members:
   :show-inheritance:

ralph.config.agent_transport
~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.config.agent_transport
   :members:
   :show-inheritance:

ralph.config.ccs_config
~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.config.ccs_config
   :members:
   :show-inheritance:

ralph.config.general_config
~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.config.general_config
   :members:
   :show-inheritance:

ralph.config.json_parser_type
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.config.json_parser_type
   :members:
   :show-inheritance:

ralph.config.mcp_server_spec
~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.config.mcp_server_spec
   :members:
   :show-inheritance:

ralph.config.pause_on_exit
~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.config.pause_on_exit
   :members:
   :show-inheritance:

ralph.config.prompt_helper_config
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.config.prompt_helper_config
   :members:
   :show-inheritance:

ralph.config.recovery_strategy
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.config.recovery_strategy
   :members:
   :show-inheritance:

ralph.config.verbosity
~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.config.verbosity
   :members:
   :show-inheritance:

ralph.config.web_search_config
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.config.web_search_config
   :members:
   :show-inheritance:

ralph.config.web_service_configs
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.config.web_service_configs
   :members:
   :show-inheritance:

Policy
------

The policy group is the heart of Ralph Workflow's policy-driven
pipeline: it loads, validates, explains, and renders the TOML policy
tables (``pipeline.toml``, ``agents.toml``, ``artifacts.toml``,
``mcp.toml``). The runtime is a generic policy interpreter; behavior
changes land in ``ralph/policy/defaults/`` rather than in code. See
:doc:`concepts` for the model and
:doc:`configuration` for the explain surface used by ``ralph
explain``.

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

.. Note: ralph.policy.explain re-exports from its submodules; using :no-members:
   avoids duplicate cross-reference warnings.

.. automodule:: ralph.policy.explain
   :no-members:

ralph.policy.explain.budget_counter_explanation
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.policy.explain.budget_counter_explanation
   :members:
   :show-inheritance:

ralph.policy.explain.commit_policy_explanation
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.policy.explain.commit_policy_explanation
   :members:
   :show-inheritance:

ralph.policy.explain.loop_counter_explanation
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.policy.explain.loop_counter_explanation
   :members:
   :show-inheritance:

ralph.policy.explain.lifecycle_explanation
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.policy.explain.lifecycle_explanation
   :members:
   :show-inheritance:

ralph.policy.explain.loop_policy_explanation
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.policy.explain.loop_policy_explanation
   :members:
   :show-inheritance:

ralph.policy.explain.parallel_explanation
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.policy.explain.parallel_explanation
   :members:
   :show-inheritance:

ralph.policy.explain.phase_explanation
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.policy.explain.phase_explanation
   :members:
   :show-inheritance:

ralph.policy.explain.policy_explanation
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.policy.explain.policy_explanation
   :members:
   :show-inheritance:

ralph.policy.explain.post_commit_route_explanation
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.policy.explain.post_commit_route_explanation
   :members:
   :show-inheritance:

ralph.policy.explain.recovery_explanation
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.policy.explain.recovery_explanation
   :members:
   :show-inheritance:

ralph.policy.explain.terminal_outcome_explanation
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.policy.explain.terminal_outcome_explanation
   :members:
   :show-inheritance:

ralph.policy.explain.verification_explanation
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.policy.explain.verification_explanation
   :members:
   :show-inheritance:

ralph.policy.render
~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.policy.render
   :members:
   :show-inheritance:

Pipeline
--------

The pipeline group owns the Ralph-loop runtime: state model, reducer,
effect router, phase agent handlers, and the run loop. Effects are
declared in TOML and routed through ``ralph/pipeline/effect_router.py``
to handlers under ``ralph/pipeline/parallel/``, ``ralph/pipeline/effects/``,
and ``ralph/pipeline/recovery/`` (covered by their own groups). See
:doc:`concepts` for the loop model and :doc:`concepts` for how
phases route between planning, development, commit, and analysis.

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

.. Note: ralph.pipeline.effects re-exports from its effect modules; using
   :no-members: avoids duplicate cross-reference warnings.

.. automodule:: ralph.pipeline.effects
   :no-members:

ralph.pipeline.effects.commit_effect
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.pipeline.effects.commit_effect
   :members:
   :show-inheritance:

ralph.pipeline.effects.early_skip_commit_effect
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.pipeline.effects.early_skip_commit_effect
   :members:
   :show-inheritance:

ralph.pipeline.effects.exhausted_analysis_phase_advance_effect
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.pipeline.effects.exhausted_analysis_phase_advance_effect
   :members:
   :show-inheritance:

ralph.pipeline.effects.exit_failure_effect
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.pipeline.effects.exit_failure_effect
   :members:
   :show-inheritance:

ralph.pipeline.effects.exit_success_effect
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.pipeline.effects.exit_success_effect
   :members:
   :show-inheritance:

ralph.pipeline.effects.fan_out_effect
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.pipeline.effects.fan_out_effect
   :members:
   :show-inheritance:

ralph.pipeline.effects.invoke_agent_effect
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.pipeline.effects.invoke_agent_effect
   :members:
   :show-inheritance:

ralph.pipeline.effects.prepare_prompt_effect
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.pipeline.effects.prepare_prompt_effect
   :members:
   :show-inheritance:

ralph.pipeline.effects.push_effect
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.pipeline.effects.push_effect
   :members:
   :show-inheritance:

ralph.pipeline.effects.save_checkpoint_effect
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.pipeline.effects.save_checkpoint_effect
   :members:
   :show-inheritance:

ralph.pipeline.events
~~~~~~~~~~~~~~~~~~~~~

.. Note: ralph.pipeline.events re-exports from its submodules; using :no-members:
   avoids duplicate cross-reference warnings.

.. automodule:: ralph.pipeline.events
   :no-members:

ralph.pipeline.events.analysis_decision_event
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.pipeline.events.analysis_decision_event
   :members:
   :show-inheritance:

ralph.pipeline.events.execution_result_event
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.pipeline.events.execution_result_event
   :members:
   :show-inheritance:

ralph.pipeline.events.phase_failure_event
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.pipeline.events.phase_failure_event
   :members:
   :show-inheritance:

ralph.pipeline.events.pipeline_event
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.pipeline.events.pipeline_event
   :members:
   :show-inheritance:

ralph.pipeline.events.post_fanout_verification_event
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.pipeline.events.post_fanout_verification_event
   :members:
   :show-inheritance:

ralph.pipeline.events.worker_completed_event
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.pipeline.events.worker_completed_event
   :members:
   :show-inheritance:

ralph.pipeline.events.worker_failed_event
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.pipeline.events.worker_failed_event
   :members:
   :show-inheritance:

ralph.pipeline.events.worker_started_event
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.pipeline.events.worker_started_event
   :members:
   :show-inheritance:

ralph.pipeline.handoffs
~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.pipeline.handoffs
   :members:
   :show-inheritance:

ralph.pipeline.loopback
~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.pipeline.loopback
   :members:
   :show-inheritance:

ralph.pipeline.phase_entry_cleaner
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.pipeline.phase_entry_cleaner
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

ralph.pipeline.parallel.worker_manifest
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.pipeline.parallel.worker_manifest
   :members:
   :show-inheritance:

ralph.pipeline.parallel.worker_runtime
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.pipeline.parallel.worker_runtime
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

ralph.pipeline.parallel.parallel_execution_mode
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.pipeline.parallel.parallel_execution_mode
   :members:
   :show-inheritance:

ralph.pipeline.progress
~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.pipeline.progress
   :members:
   :show-inheritance:

ralph.pipeline.reducer
~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.pipeline.reducer
   :members:
   :show-inheritance:

ralph.pipeline.auto_integrate
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.pipeline.auto_integrate
   :members:
   :show-inheritance:

ralph.pipeline.auto_integrate_backup_refs
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.pipeline.auto_integrate_backup_refs
   :members:
   :show-inheritance:

ralph.pipeline.auto_integrate_recovery_terminal
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.pipeline.auto_integrate_recovery_terminal
   :members:
   :show-inheritance:

ralph.pipeline.auto_integrate_terminal
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.pipeline.auto_integrate_terminal
   :members:
   :show-inheritance:

ralph.pipeline.auto_integrate_backoff
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.pipeline.auto_integrate_backoff
   :members:
   :show-inheritance:

ralph.pipeline.auto_integrate_boundary_refresh
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.pipeline.auto_integrate_boundary_refresh
   :members:
   :show-inheritance:

ralph.pipeline.conflict_resolution
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.pipeline.conflict_resolution
   :members:
   :show-inheritance:

ralph.pipeline.conflict_resolution.graph
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.pipeline.conflict_resolution.graph
   :members:
   :show-inheritance:

ralph.pipeline.conflict_resolution.prompt
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.pipeline.conflict_resolution.prompt
   :members:
   :show-inheritance:

ralph.pipeline.conflict_resolution.status
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.pipeline.conflict_resolution.status
   :members:
   :show-inheritance:

ralph.pipeline.conflict_resolution.session
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.pipeline.conflict_resolution.session
   :members:
   :show-inheritance:

ralph.pipeline.conflict_resolution.driver
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.pipeline.conflict_resolution.driver
   :members:
   :show-inheritance:

ralph.pipeline.conflict_resolution.rebase_loop
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.pipeline.conflict_resolution.rebase_loop
   :members:
   :show-inheritance:

ralph.pipeline.auto_integrate_conflict_budget
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.pipeline.auto_integrate_conflict_budget
   :members:
   :show-inheritance:

ralph.pipeline.auto_integrate_budget_seam
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.pipeline.auto_integrate_budget_seam
   :members:
   :show-inheritance:

ralph.pipeline.auto_integrate_context
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.pipeline.auto_integrate_context
   :members:
   :show-inheritance:

ralph.pipeline.auto_integrate_catchup
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.pipeline.auto_integrate_catchup
   :members:
   :show-inheritance:

ralph.pipeline.auto_integrate_ff
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.pipeline.auto_integrate_ff
   :members:
   :show-inheritance:

ralph.pipeline.auto_integrate_outcome
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.pipeline.auto_integrate_outcome
   :members:
   :show-inheritance:

ralph.pipeline.auto_integrate_rebase_merge
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.pipeline.auto_integrate_rebase_merge
   :members:
   :show-inheritance:

ralph.pipeline.auto_integrate_record
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.pipeline.auto_integrate_record
   :members:
   :show-inheritance:

ralph.pipeline.auto_integrate_resolve
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.pipeline.auto_integrate_resolve
   :members:
   :show-inheritance:

ralph.pipeline.auto_integrate_agent
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.pipeline.auto_integrate_agent
   :members:
   :show-inheritance:

ralph.pipeline.auto_integrate_recovery
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.pipeline.auto_integrate_recovery
   :members:
   :show-inheritance:

ralph.pipeline.auto_integrate_recovery_terminal
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.pipeline.auto_integrate_recovery_terminal
   :members:
   :no-index:
   :show-inheritance:

ralph.pipeline.auto_integrate_refresh
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.pipeline.auto_integrate_refresh
   :members:
   :show-inheritance:

ralph.pipeline.auto_integrate_terminal
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.pipeline.auto_integrate_terminal
   :members:
   :no-index:
   :show-inheritance:

ralph.pipeline.auto_integrate_sync
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.pipeline.auto_integrate_sync
   :members:
   :show-inheritance:

ralph.pipeline.runner
~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.pipeline.runner
   :members:
   :show-inheritance:

ralph.pipeline.activity_stream
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.pipeline.activity_stream
   :members:
   :show-inheritance:

ralph.pipeline.effect_executor
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.pipeline.effect_executor
   :members:
   :show-inheritance:

ralph.pipeline.effect_router
~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.pipeline.effect_router
   :members:
   :show-inheritance:

ralph.pipeline.fan_out
~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.pipeline.fan_out
   :members:
   :show-inheritance:

ralph.pipeline.phase_agent_handler
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.pipeline.phase_agent_handler
   :members:
   :show-inheritance:

ralph.pipeline.phase_rendering
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.pipeline.phase_rendering
   :members:
   :show-inheritance:

ralph.pipeline.phase_transition
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.pipeline.phase_transition
   :members:
   :show-inheritance:

ralph.pipeline.prompt_prep
~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.pipeline.prompt_prep
   :members:
   :show-inheritance:

ralph.pipeline.factory
~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.pipeline.factory
   :members:
   :show-inheritance:

ralph.pipeline.run_loop
~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.pipeline.run_loop
   :members:
   :show-inheritance:

ralph.pipeline.state_init
~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.pipeline.state_init
   :members:
   :show-inheritance:

ralph.pipeline.waiting_dispatch
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.pipeline.waiting_dispatch
   :members:
   :show-inheritance:

ralph.pipeline.agent_retry_decision
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.pipeline.agent_retry_decision
   :members:
   :show-inheritance:

ralph.pipeline.agent_retry_intent
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.pipeline.agent_retry_intent
   :members:
   :show-inheritance:

ralph.pipeline.retryable_failure
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.pipeline.retryable_failure
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

ralph.pipeline.exhausted_analysis_bypass_result
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.pipeline.exhausted_analysis_bypass_result
   :members:
   :show-inheritance:

ralph.pipeline.exhausted_analysis_skip
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.pipeline.exhausted_analysis_skip
   :members:
   :show-inheritance:

ralph.pipeline.state_models
~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.pipeline.state_models
   :members:
   :show-inheritance:

ralph.pipeline.work_unit
~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.pipeline.work_unit
   :members:
   :show-inheritance:

ralph.pipeline.work_units_validation_error
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.pipeline.work_units_validation_error
   :members:
   :show-inheritance:

ralph.pipeline.worker_status
~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.pipeline.worker_status
   :members:
   :show-inheritance:

Skills
------

This group owns the shipped skill bundle and the helpers that sync it
with each agent's skill tree. The mirrored baseline skill content lives
under ``ralph/skills/content/`` and is listed in ``BASELINE_SKILL_NAMES``
in ``ralph/skills/_content.py``. The manager (``ralph/skills/manager.py``)
handles install, adoption, and the deterministic auto-commit contract
enforced by ``ralph/testing/audit_skill_auto_commit.py`` (verify step 18).

ralph.skills
~~~~~~~~~~~~

.. automodule:: ralph.skills
   :members:
   :show-inheritance:

ralph.skills.manager
~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.skills.manager
   :members:
   :show-inheritance:

Git
---

The Git group bundles the GitPython-backed repository operations used by
the runtime: status, diff, log, show, rebase, hooks, subprocess runner,
and the commit-cleanup helpers that ship under ``ralph/git/commit_cleanup.py``.
Every blocking call in this group uses a bounded timeout via
``ralph/git/subprocess_runner.run_git``; see the bounded-subprocess
contract in `docs/agents/verification.md
<https://github.com/Ralph-Workflow/Ralph-Workflow/blob/main/docs/agents/verification.md>`__.

ralph.git.commit_cleanup
~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.git.commit_cleanup
   :members:
   :show-inheritance:

ralph.git.scoped_auto_commit
~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.git.scoped_auto_commit
   :members:
   :show-inheritance:

----

Phases
------

This group holds the per-phase logic: analysis (``ralph/phases/analysis.py``),
artifact-required checks (``required_artifacts``), commit / commit-cleanup
/ commit-logging, execution, integrity, review, verification, and timing.
Each phase maps to a TOML block under ``[blocks.*]`` in the bundled
``ralph/policy/defaults/pipeline.toml``. See :doc:`concepts` for how
phase transitions are wired and :doc:`advanced-pipeline-configuration`
for extending the phase graph.

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

ralph.phases.commit_cleanup
~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.phases.commit_cleanup
   :members:
   :show-inheritance:

ralph.phases.commit_logging
~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.phases.commit_logging
   :members:
   :show-inheritance:

ralph.phases.execution
~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.phases.execution
   :members:
   :show-inheritance:

ralph.phases.integrity
~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.phases.integrity
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

ralph.phases.verification
~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.phases.verification
   :members:
   :show-inheritance:

ralph.phases.timing
~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.phases.timing
   :members:
   :show-inheritance:

Agents
------

This is the largest group: it owns the built-in agent catalog, the
executor protocol and subprocess adapter, the parsers for every
supported agent (Claude, Codex, OpenCode, Nanocoder, AGY, Pi), the
execution strategies, and the activity-aware idle watchdog with its
post-exit sibling. See :doc:`agents` for the selection and trust story,
:doc:`concepts` for the timeout model, and
:doc:`agent-compatibility` for the supported-agent matrix.

ralph.agents
~~~~~~~~~~~~

.. automodule:: ralph.agents
   :no-members:

ralph.agents.activity
~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.agents.activity
   :members:
   :show-inheritance:

ralph.agents.agent_activity_kind
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.agents.agent_activity_kind
   :members:
   :show-inheritance:

ralph.agents.agent_chain
~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.agents.agent_chain
   :members:
   :show-inheritance:

ralph.agents.agent_entry
~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.agents.agent_entry
   :members:
   :show-inheritance:

ralph.agents.clock
~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.agents.clock
   :members:
   :show-inheritance:

ralph.agents.drain_not_bound_error
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.agents.drain_not_bound_error
   :members:
   :show-inheritance:

ralph.agents.executor_error
~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.agents.executor_error
   :members:
   :show-inheritance:

ralph.agents.system_clock
~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.agents.system_clock
   :members:
   :show-inheritance:

ralph.agents.unknown_agent_error
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.agents.unknown_agent_error
   :members:
   :show-inheritance:

ralph.agents.builtin_spec
~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.agents.builtin_spec
   :members:
   :show-inheritance:

ralph.agents.builtin
~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.agents.builtin
   :members:
   :show-inheritance:

ralph.agents.idle_watchdog_kill
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.agents.idle_watchdog_kill
   :members:
   :show-inheritance:

ralph.agents.worker_result
~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.agents.worker_result
   :members:
   :show-inheritance:

ralph.agents.availability
~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.agents.availability
   :members:
   :show-inheritance:

ralph.agents.agent_install_links
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.agents.agent_install_links
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

.. Note: ralph.agents.execution_state re-exports from its submodules;
   using :no-members: avoids duplicate cross-reference warnings.

.. automodule:: ralph.agents.execution_state
   :no-members:

ralph.agents.execution_state.agent_execution_state
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.agents.execution_state.agent_execution_state
   :members:
   :show-inheritance:

ralph.agents.execution_state.claude_execution_strategy
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.agents.execution_state.claude_execution_strategy
   :members:
   :show-inheritance:

ralph.agents.execution_state.claude_interactive_execution_strategy
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.agents.execution_state.claude_interactive_execution_strategy
   :members:
   :show-inheritance:

ralph.agents.execution_state.generic_execution_strategy
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.agents.execution_state.generic_execution_strategy
   :members:
   :show-inheritance:

ralph.agents.execution_state.opencode_execution_strategy
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.agents.execution_state.opencode_execution_strategy
   :members:
   :show-inheritance:

ralph.agents.executor
~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.agents.executor
   :members:
   :show-inheritance:

ralph.agents.idle_watchdog
~~~~~~~~~~~~~~~~~~~~~~~~~~

.. Note: ralph.agents.idle_watchdog re-exports from its submodules;
   using :no-members: avoids duplicate cross-reference warnings.

.. automodule:: ralph.agents.idle_watchdog
   :no-members:

ralph.agents.idle_watchdog.corroboration_snapshot
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.agents.idle_watchdog.corroboration_snapshot
   :members:
   :show-inheritance:

ralph.agents.idle_watchdog.idle_watchdog
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.agents.idle_watchdog.idle_watchdog
   :members:
   :show-inheritance:

ralph.agents.idle_watchdog.repetition_tracker
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.agents.idle_watchdog.repetition_tracker
   :members:
   :show-inheritance:

ralph.agents.idle_watchdog.timeout_policy
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.agents.idle_watchdog.timeout_policy
   :members:
   :show-inheritance:

ralph.agents.idle_watchdog.waiting_status_event
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.agents.idle_watchdog.waiting_status_event
   :members:
   :show-inheritance:

ralph.agents.idle_watchdog.waiting_status_kind
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.agents.idle_watchdog.waiting_status_kind
   :members:
   :show-inheritance:

ralph.agents.idle_watchdog.watchdog_fire_reason
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.agents.idle_watchdog.watchdog_fire_reason
   :members:
   :show-inheritance:

ralph.agents.idle_watchdog.watchdog_verdict
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.agents.idle_watchdog.watchdog_verdict
   :members:
   :show-inheritance:

ralph.agents.invoke
~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.agents.invoke
   :members:
   :show-inheritance:
   :noindex:

ralph.agents.parsers
~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.agents.parsers
   :no-members:

ralph.agents.parsers.agy
~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.agents.parsers.agy
   :members:
   :show-inheritance:

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

ralph.agents.parsers.cursor
~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.agents.parsers.cursor
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

ralph.agents.parsers.nanocoder
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.agents.parsers.nanocoder
   :members:
   :show-inheritance:

ralph.agents.parsers.opencode
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.agents.parsers.opencode
   :members:
   :show-inheritance:

ralph.agents.parsers.pi
~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.agents.parsers.pi
   :members:
   :show-inheritance:

ralph.agents.parsers.claude_interactive
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.agents.parsers.claude_interactive
   :members:
   :show-inheritance:

ralph.agents.parsers.claude_interactive_transcript_parser
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.agents.parsers.claude_interactive_transcript_parser
   :members:
   :show-inheritance:

ralph.agents.parsers.agent_output_line
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.agents.parsers.agent_output_line
   :members:
   :show-inheritance:

ralph.agents.parsers.interactive_transcript_event
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.agents.parsers.interactive_transcript_event
   :members:
   :show-inheritance:

ralph.agents.parsers.text_accumulator
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.agents.parsers.text_accumulator
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

ralph.agents.timeout_clock
~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.agents.timeout_clock
   :members:
   :show-inheritance:

----

MCP
---

The MCP group is the bridge between the agent and the workspace: it
exposes the in-process ``ralph-workflow`` MCP server, the upstream
proxy for third-party MCP servers, the tool surface
(read_file / write_file / exec / git_read / websearch / webvisit /
artifact submit / plan draft edit), the canonical artifact-submission
contract, and the multimodal (image / audio / video / PDF) capability
detection. See :doc:`mcp-architecture` for the runtime topology and
:doc:`artifacts` for the artifact-submission contract.

ralph.mcp
~~~~~~~~~

.. automodule:: ralph.mcp
   :no-members:

ralph.mcp.artifacts
~~~~~~~~~~~~~~~~~~~

.. Note: ralph.mcp.artifacts re-exports from its submodules;
   using :no-members: avoids duplicate object warnings.

.. automodule:: ralph.mcp.artifacts
   :no-members:

ralph.mcp.artifacts.audit_adapter
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.mcp.artifacts.audit_adapter
   :members:
   :show-inheritance:

ralph.mcp.artifacts.canonical_submit
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.mcp.artifacts.canonical_submit
   :members:
   :show-inheritance:

ralph.mcp.artifacts.commit_message
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.mcp.artifacts.commit_message
   :members:
   :show-inheritance:

ralph.mcp.artifacts.completion_receipts
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.mcp.artifacts.completion_receipts
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

ralph.mcp.artifacts.idempotent_write
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.mcp.artifacts.idempotent_write
   :members:
   :show-inheritance:

ralph.mcp.artifacts.handoffs
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.mcp.artifacts.handoffs
   :members:
   :show-inheritance:

ralph.mcp.artifacts.history
~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.mcp.artifacts.history
   :members:
   :show-inheritance:

ralph.mcp.artifacts.md_draft_io
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.mcp.artifacts.md_draft_io
   :members:
   :show-inheritance:

ralph.mcp.artifacts.plan
~~~~~~~~~~~~~~~~~~~~~~~~

.. Note: ralph.mcp.artifacts.plan re-exports from its submodules;
   using :no-members: avoids duplicate cross-reference warnings.

.. automodule:: ralph.mcp.artifacts.plan
   :no-members:

ralph.mcp.artifacts.plan.plan_schema
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.mcp.artifacts.plan.plan_schema
   :members:
   :show-inheritance:

ralph.mcp.artifacts.policy_outcomes
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.mcp.artifacts.policy_outcomes
   :members:
   :show-inheritance:

ralph.mcp.artifacts.smoke_test_result
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.mcp.artifacts.smoke_test_result
   :members:
   :show-inheritance:

ralph.mcp.artifacts.product_spec
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.mcp.artifacts.product_spec
   :members:
   :show-inheritance:

ralph.mcp.artifacts.typed_artifacts
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.mcp.artifacts.typed_artifacts
   :members:
   :show-inheritance:

ralph.mcp.artifacts.state_db
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.mcp.artifacts.state_db
   :members:
   :show-inheritance:

ralph.mcp.artifacts.markdown
~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.mcp.artifacts.markdown
   :no-members:

ralph.mcp.artifacts.markdown.registry
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.mcp.artifacts.markdown.registry
   :members:

ralph.mcp.artifacts.markdown.specs
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.mcp.artifacts.markdown.specs
   :no-members:

.. automodule:: ralph.mcp.artifacts.markdown.specs.analysis_decision
   :members:

.. automodule:: ralph.mcp.artifacts.markdown.specs.commit_cleanup
   :members:

.. automodule:: ralph.mcp.artifacts.markdown.specs.commit_message
   :members:

.. automodule:: ralph.mcp.artifacts.markdown.specs.development_result
   :members:

.. automodule:: ralph.mcp.artifacts.markdown.specs.fix_result
   :members:

.. automodule:: ralph.mcp.artifacts.markdown.specs.issues
   :members:

.. automodule:: ralph.mcp.artifacts.markdown.specs.plan
   :members:

.. automodule:: ralph.mcp.artifacts.markdown.specs.product_spec
   :members:

.. automodule:: ralph.mcp.artifacts.markdown.specs.smoke_test_result
   :members:

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

.. Note: ralph.mcp.server re-exports from its submodules; using :no-members:
   avoids duplicate object-description warnings.

.. automodule:: ralph.mcp.server
   :no-members:

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

ralph.mcp.server.runtime_session
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.mcp.server.runtime_session
   :members:
   :show-inheritance:

ralph.mcp.server.exec_sse_streaming
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.mcp.server.exec_sse_streaming
   :members:
   :show-inheritance:

ralph.mcp.server.__main__
~~~~~~~~~~~~~~~~~~~~~~~~~

.. Note: ralph.mcp.server.__main__ is the standalone ``ralph-mcp`` entry point;
   using :no-members: since it contains no public API beyond starting the server.

.. automodule:: ralph.mcp.server.__main__
   :no-members:

ralph.mcp.multimodal
~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.mcp.multimodal
   :no-members:

ralph.mcp.multimodal.artifacts
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.mcp.multimodal.artifacts
   :members:
   :show-inheritance:

ralph.mcp.multimodal.capabilities
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.mcp.multimodal.capabilities
   :members:
   :show-inheritance:

ralph.mcp.multimodal.errors
~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.mcp.multimodal.errors
   :members:
   :show-inheritance:

ralph.mcp.multimodal.resources
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.mcp.multimodal.resources
   :members:
   :show-inheritance:

ralph.mcp.effective_session_mcp_plan
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.mcp.effective_session_mcp_plan
   :members:
   :show-inheritance:

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

ralph.mcp.tools.md_artifact
~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.mcp.tools.md_artifact
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
   :exclude-members: ImageContent

ralph.mcp.tools.exec
~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.mcp.tools.exec
   :members:
   :show-inheritance:

ralph.mcp.tools.unsafe_exec
~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.mcp.tools.unsafe_exec
   :members:
   :show-inheritance:

   The ``raw_exec`` tool is an alias for ``unsafe_exec`` — it uses the same
   handler and exposes the same functionality under a different name.

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

ralph.mcp.tool_contract
~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.mcp.tool_contract
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

ralph.mcp.tools.capability_denied_error
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.mcp.tools.capability_denied_error
   :members:
   :show-inheritance:

ralph.mcp.tools.coordination_session_like
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.mcp.tools.coordination_session_like
   :members:
   :show-inheritance:

ralph.mcp.tools.invalid_params_error
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.mcp.tools.invalid_params_error
   :members:
   :show-inheritance:

ralph.mcp.tools.tool_content
~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.mcp.tools.tool_content
   :members:
   :show-inheritance:

ralph.mcp.tools.tool_error
~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.mcp.tools.tool_error
   :members:
   :show-inheritance:

ralph.mcp.tools.tool_result
~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.mcp.tools.tool_result
   :members:
   :show-inheritance:

ralph.mcp.tools.workspace_like
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.mcp.tools.workspace_like
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

ralph.mcp.transport.cursor
~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.mcp.transport.cursor
   :members:
   :show-inheritance:

ralph.mcp.transport.opencode
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.mcp.transport.opencode
   :members:
   :show-inheritance:

ralph.mcp.transport.nanocoder
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.mcp.transport.nanocoder
   :members:
   :show-inheritance:

ralph.mcp.transport.pi
~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.mcp.transport.pi
   :members:
   :show-inheritance:

ralph.mcp.transport.agy
~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.mcp.transport.agy
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

ralph.mcp.upstream.tool_catalog_cache
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.mcp.upstream.tool_catalog_cache
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

.. Note: ralph.mcp.websearch re-exports from its submodules; using :no-members:
   avoids duplicate object-description warnings.

.. automodule:: ralph.mcp.websearch
   :no-members:

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

ralph.mcp.explore
~~~~~~~~~~~~~~~~~

.. automodule:: ralph.mcp.explore
   :members:
   :show-inheritance:
   :no-index:

ralph.mcp.explore.audit_register
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.mcp.explore.audit_register
   :members:
   :show-inheritance:

ralph.mcp.explore.family_baseline
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.mcp.explore.family_baseline
   :members:
   :show-inheritance:

ralph.mcp.explore.bench
~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.mcp.explore.bench
   :members:
   :show-inheritance:

ralph.mcp.explore.deferred_phases
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.mcp.explore.deferred_phases
   :members:
   :show-inheritance:

ralph.mcp.explore.dirty_paths
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.mcp.explore.dirty_paths
   :members:
   :show-inheritance:

ralph.mcp.explore.graph
~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.mcp.explore.graph
   :members:
   :show-inheritance:

ralph.mcp.explore.path_filter
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.mcp.explore.path_filter
   :members:
   :show-inheritance:

ralph.mcp.explore.structure
~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.mcp.explore.structure
   :members:
   :show-inheritance:

ralph.mcp.explore.handlers
~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.mcp.explore.handlers
   :members:
   :show-inheritance:

ralph.mcp.explore.lifecycle
~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.mcp.explore.lifecycle
   :members:
   :show-inheritance:

ralph.mcp.explore.pipeline
~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.mcp.explore.pipeline
   :members:
   :show-inheritance:

ralph.mcp.explore.ranking
~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.mcp.explore.ranking
   :members:
   :show-inheritance:

ralph.mcp.explore.store
~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.mcp.explore.store
   :members:
   :show-inheritance:

Git
---

ralph.git
~~~~~~~~~

.. automodule:: ralph.git
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

ralph.git.merge
~~~~~~~~~~~~~~~

.. automodule:: ralph.git.merge
   :members:
   :show-inheritance:

ralph.git.rebase
~~~~~~~~~~~~~~~~

.. Note: ralph.git.rebase re-exports from its private submodules; using
   :no-members: avoids duplicate object-description warnings.

.. automodule:: ralph.git.rebase
   :no-members:

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

ralph.git.remote_push
~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.git.remote_push
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

.. Note: ralph.workspace.fs re-exports FsWorkspace which is already in ralph.workspace.__all__;
   using :no-members: avoids a duplicate object description warning for FsWorkspace.root when
   the package-level module and the submodule are both rendered by autodoc.

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
   :members:
   :show-inheritance:

ralph.workspace.skip
~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.workspace.skip
   :members:
   :show-inheritance:

ralph.workspace.agent_dir_retention
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.workspace.agent_dir_retention
   :members:
   :show-inheritance:

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

ralph.recovery.agent_budget_registry
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.recovery.agent_budget_registry
   :members:
   :show-inheritance:

ralph.recovery.budget_state
~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.recovery.budget_state
   :members:
   :show-inheritance:

ralph.recovery.classified_failure
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.recovery.classified_failure
   :members:
   :show-inheritance:

ralph.recovery.failure_budget
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.recovery.failure_budget
   :members:
   :show-inheritance:

ralph.recovery.failure_category
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.recovery.failure_category
   :members:
   :show-inheritance:

ralph.recovery.failure_details
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.recovery.failure_details
   :members:
   :show-inheritance:

ralph.recovery.retry_prompt
~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.recovery.retry_prompt
   :members:
   :show-inheritance:

ralph.recovery.seed_budget_registry
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.recovery.seed_budget_registry
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

.. Note: ralph.process re-exports from its submodules; using :no-members: avoids
   duplicate object-description warnings.

.. automodule:: ralph.process
   :no-members:

ralph.process.child_liveness
~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.process.child_liveness
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

ralph.process.pty
~~~~~~~~~~~~~~~~~

.. automodule:: ralph.process.pty
   :members:
   :show-inheritance:

ralph.process.mcp_supervisor
~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.process.mcp_supervisor
   :members:
   :show-inheritance:

ralph.process.monitor
~~~~~~~~~~~~~~~~~~~~~

.. Note: ralph.process.monitor re-exports from its submodules; using :no-members:
   avoids duplicate object-description warnings.

.. automodule:: ralph.process.monitor
   :no-members:

ralph.process.teardown
~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.process.teardown
   :members:
   :show-inheritance:

API
---

ralph.api
~~~~~~~~~

.. automodule:: ralph.api
   :no-members:

ralph.api.opencode
~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.api.opencode
   :members:
   :show-inheritance:

ralph.supervising
~~~~~~~~~~~~~~~~~

.. automodule:: ralph.supervising
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

ralph.checkpoint.checkpoint_payload
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.checkpoint.checkpoint_payload
   :members:
   :show-inheritance:

ralph.checkpoint.execution_step
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.checkpoint.execution_step
   :members:
   :show-inheritance:

ralph.checkpoint.size_alert
~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.checkpoint.size_alert
   :members:
   :show-inheritance:

ralph.checkpoint.size_check_result
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.checkpoint.size_check_result
   :members:
   :show-inheritance:

ralph.checkpoint.size_thresholds
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.checkpoint.size_thresholds
   :members:
   :show-inheritance:

ralph.checkpoint.step_outcome
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.checkpoint.step_outcome
   :members:
   :show-inheritance:

ralph.diagnostics
~~~~~~~~~~~~~~~~~

.. automodule:: ralph.diagnostics
   :members:
   :show-inheritance:

ralph.diagnostics.fs_health
~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.diagnostics.fs_health
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

ralph.display.activity_event_kind
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.display.activity_event_kind
   :members:
   :show-inheritance:

ralph.display.activity_provider
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.display.activity_provider
   :members:
   :show-inheritance:

ralph.display.activity_visibility_hint
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.display.activity_visibility_hint
   :members:
   :show-inheritance:

ralph.display.activity_router
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.display.activity_router
   :members:
   :show-inheritance:

ralph.display.agent_event_renderer
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.display.agent_event_renderer
   :members:
   :show-inheritance:

ralph.display.artifact_reader
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.display.artifact_reader
   :members:
   :show-inheritance:

ralph.display.auto_integrate_message
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.display.auto_integrate_message
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
   :members:
   :show-inheritance:

ralph.display.line_sanitizer
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.display.line_sanitizer
   :members:
   :show-inheritance:

ralph.display.log_sink
~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.display.log_sink
   :members:
   :show-inheritance:

ralph.display.vt_normalizer
~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.display.vt_normalizer
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

ralph.display.phase_lifecycle
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.display.phase_lifecycle
   :members:
   :show-inheritance:

ralph.display.phase_status
~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.display.phase_status
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

ralph.display.status_bar
~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.display.status_bar
   :members:
   :show-inheritance:

ralph.display.subscriber
~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.display.subscriber
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

.. Note: ralph.executor re-exports from its submodules; using :no-members: avoids
   duplicate object-description warnings.

.. automodule:: ralph.executor
   :no-members:

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

ralph.interrupt
~~~~~~~~~~~~~~~

.. Note: ralph.interrupt re-exports its submodules; using :no-members: avoids
   duplicate object-description warnings while keeping the package documented.

.. automodule:: ralph.interrupt
   :no-members:

ralph.interrupt.asyncio_bridge
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.interrupt.asyncio_bridge
   :members:
   :show-inheritance:

ralph.interrupt.controller
~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.interrupt.controller
   :members:
   :show-inheritance:

ralph.interrupt.dispatcher
~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.interrupt.dispatcher
   :members:
   :show-inheritance:

ralph.interrupt.state
~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.interrupt.state
   :members:
   :show-inheritance:

ralph.interrupt.signal_getter
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.interrupt.signal_getter
   :members:
   :show-inheritance:

ralph.interrupt.signal_handler
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.interrupt.signal_handler
   :members:
   :show-inheritance:

ralph.interrupt.signal_setter
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.interrupt.signal_setter
   :members:
   :show-inheritance:

ralph.files
~~~~~~~~~~~

.. Note: ralph.files re-exports from its submodules; using :no-members: avoids
   duplicate object-description warnings.

.. automodule:: ralph.files
   :no-members:

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

Project Policy
--------------

This group owns the project-policy-readiness preflight: the versioned,
machine-checkable schema (markers, required headings, RALPH-FACT /
RALPH-COMMAND / RALPH-LANG / RALPH-INAPPLICABLE field markers), the
shared readiness-evidence inventory the validator and the cache share,
the deterministic validator, the change-aware READY cache, the AGENTS.md
/ CLAUDE.md idempotent bootstrap, the bundled starter policies, the
deterministic preflight orchestrator, and the synchronous, bounded,
out-of-graph remediation driver.

ralph.project_policy
~~~~~~~~~~~~~~~~~~~~

.. Note: ralph.project_policy re-exports the public preflight API and the
   submodules; using :no-members: avoids duplicate cross-reference warnings.

.. automodule:: ralph.project_policy
   :no-members:

ralph.project_policy.markers
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.project_policy.markers
   :members:
   :show-inheritance:

ralph.project_policy.schema_state
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.project_policy.schema_state
   :members:
   :show-inheritance:

ralph.project_policy.models
~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.project_policy.models
   :members:
   :show-inheritance:

ralph.project_policy.evidence
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.project_policy.evidence
   :members:
   :show-inheritance:

ralph.project_policy.agents_md
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.project_policy.agents_md
   :members:
   :show-inheritance:

ralph.project_policy.validators
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.project_policy.validators
   :members:
   :show-inheritance:

ralph.project_policy.cache
~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.project_policy.cache
   :members:
   :show-inheritance:

ralph.project_policy.preflight
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.project_policy.preflight
   :members:
   :show-inheritance:

ralph.project_policy.remediation
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.project_policy.remediation
   :members:
   :show-inheritance:

ralph.project_policy.analysis
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.project_policy.analysis
   :members:
   :show-inheritance:

ralph.project_policy.analysis_decision
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.project_policy.analysis_decision
   :members:
   :show-inheritance:

ralph.project_policy.pipeline_route
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.project_policy.pipeline_route
   :members:
   :show-inheritance:

ralph.project_policy.pipeline_phase
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.project_policy.pipeline_phase
   :members:
   :show-inheritance:

ralph.project_policy.pipeline_graph
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.project_policy.pipeline_graph
   :members:
   :show-inheritance:

ralph.project_policy.pipeline_driver
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.project_policy.pipeline_driver
   :members:
   :show-inheritance:

ralph.project_policy.policy_mode
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.project_policy.policy_mode
   :members:
   :show-inheritance:

ralph.project_policy.reset
~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.project_policy.reset
   :members:
   :show-inheritance:

ralph.project_policy.cli_integration
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.project_policy.cli_integration
   :members:
   :show-inheritance:

ralph.project_policy.status_bar
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.project_policy.status_bar
   :members:
   :show-inheritance:

ralph.project_policy.starters
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. Note: ralph.project_policy.starters exposes the bundled starter-policy
   bundle; using :no-members: avoids duplicate cross-reference warnings
   because the bundled markdown content is data, not Python API surface.

.. automodule:: ralph.project_policy.starters
   :no-members:

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

ralph.prompts.materialize_support
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.prompts.materialize_support
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

ralph.testing.pytest_timeout_plugin
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.testing.pytest_timeout_plugin
   :members:
   :show-inheritance:

ralph.testing.audit_artifact_submission_canonical_path
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.testing.audit_artifact_submission_canonical_path
   :members:
   :show-inheritance:

ralph.testing.audit_di_seam
~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.testing.audit_di_seam
   :members:
   :show-inheritance:

ralph.testing.audit_idempotent_write_adoption
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.testing.audit_idempotent_write_adoption
   :members:
   :show-inheritance:

ralph.testing.audit_parallelization_dormant
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.testing.audit_parallelization_dormant
   :members:
   :show-inheritance:

ralph.testing.audit_resource_lifecycle
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.testing.audit_resource_lifecycle
   :members:
   :show-inheritance:

ralph.testing.audit_terminal_escape_containment
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.testing.audit_terminal_escape_containment
   :members:
   :show-inheritance:

ralph.testing.audit_watchdog_drift
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.testing.audit_watchdog_drift
   :members:
   :show-inheritance:

ralph.telemetry
~~~~~~~~~~~~~~~

.. automodule:: ralph.telemetry
   :no-members:

----

Integrations
------------

This group is the engine-side half of the Ralph-Workflow-Pro contract.
It exposes the package and its public API for sphinx cross-references,
the workspace and prompt hooks, the heartbeat / watcher / marker
helpers, and the state-query surface used by an attached Pro Support
session. The detailed contract lives at :doc:`pro-support`; this entry
keeps the module surface discoverable so contributors can land
changes against the Pro Support boundary without reading the full
contract page.

ralph.pro_support
~~~~~~~~~~~~~~~~~

.. Note: ralph.pro_support is the engine-side half of the Ralph-Workflow-Pro
   contract. The detailed contract page is :doc:`pro-support`; this entry
   exposes the package and its public API for sphinx cross-references.

.. automodule:: ralph.pro_support
   :no-members:

ralph.pro_support.env
~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.pro_support.env
   :members:
   :show-inheritance:

ralph.pro_support.workspace
~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.pro_support.workspace
   :members:
   :show-inheritance:

ralph.pro_support.prompt
~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.pro_support.prompt
   :members:
   :show-inheritance:

ralph.pro_support.marker
~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.pro_support.marker
   :members:
   :show-inheritance:

ralph.pro_support.heartbeat
~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.pro_support.heartbeat
   :members:
   :show-inheritance:

ralph.pro_support.watcher
~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.pro_support.watcher
   :members:
   :show-inheritance:

ralph.pro_support.hooks
~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.pro_support.hooks
   :members:
   :show-inheritance:

ralph.pro_support.state_query
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: ralph.pro_support.state_query
   :members:
   :show-inheritance:
