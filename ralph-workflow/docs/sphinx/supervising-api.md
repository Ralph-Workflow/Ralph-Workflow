# Supervising API

Ralph Workflow is a free and open-source AI agent orchestrator built around a simple core loop inspired by the original Ralph loop.
That simple core composes into a stronger workflow system for serious repo work, and the default workflow is already strong enough to start with before you customize anything.


This page is for developers and integrators building orchestrators that supervise one or more Ralph Workflow instances.

If you only need to run Ralph Workflow interactively, this page is not relevant to your use case.

## Overview

The supervising API exposes a stable, read-only view of a running workflow instance for orchestration and monitoring.

Use it to inspect:

- the stable instance identity,
- the optional runtime run identity,
- the lifecycle status,
- the current pipeline stage,
- recent operational activity.

## InstanceStatus

`InstanceStatus` describes the observable lifecycle state of a workflow instance:

- `not_started` — no snapshot has been received yet; the tracker holds a stable pre-start identity
- `active` — the pipeline is currently executing a stage
- `waiting` — the pipeline is active but waiting on child work
- `completed` — the instance reached a successful terminal state
- `failed` — the instance failed or was interrupted

## WorkflowInstanceView

`WorkflowInstanceView` is the immutable snapshot surface for orchestration.

Fields:

- `instance_id`: Stable orchestration identity assigned at `WorkflowInstanceTracker` construction.
  This is the primary identity an orchestrator uses to track this instance.
  Unlike ``run_id``, it is fixed before the workflow starts and never changes.
- `run_id`: Optional runtime identifier copied from the live pipeline snapshot.
  This is separate from ``instance_id`` so that a supervising orchestrator can
  track the same instance across restarts or reconnects without confusion.
  It is ``None`` before startup and when the underlying system does not assign one.
- `lifecycle_status`: One of `InstanceStatus`
- `current_stage`: Active pipeline stage name, or ``None``
- `recent_activity`: Recent operational output, ordered oldest to newest

## WorkflowInstanceTracker

``WorkflowInstanceTracker`` owns the stable orchestration identity and updates
the immutable view from live snapshots.

### Constructor

```python
WorkflowInstanceTracker(instance_id: str)
```

Initialize the tracker with a stable ``instance_id`` assigned by the orchestrator.
The tracker starts at ``InstanceStatus.NOT_STARTED`` with no ``run_id``,
``current_stage=None``, and empty ``recent_activity``.

### Properties

- ``view``: Returns the latest immutable `WorkflowInstanceView`.
  Always reflects the most recent snapshot while preserving the stable ``instance_id``.

### Methods

- ``update_from_snapshot(snapshot: PipelineSnapshot) -> WorkflowInstanceView``:
  Updates the view from a live pipeline snapshot. Preserves the stable ``instance_id``
  assigned at construction and copies ``snapshot.run_id`` into the view's ``run_id`` field.
  Returns the updated view.

## Wiring

Connect the supervising view to a live workflow through ``PipelineSubscriber.__init__(..., on_snapshot=...)``:

```python
from ralph.supervising import WorkflowInstanceTracker

tracker = WorkflowInstanceTracker(instance_id="work-42")
subscriber = PipelineSubscriber(
    ...,
    on_snapshot=tracker.update_from_snapshot,
)
# Inspect current state:
view = tracker.view
```

The ``on_snapshot`` callback is invoked after every ``notify()``, ``record_waiting_status()``,
or ``record_activity()`` call with the latest snapshot. The tracker's ``view`` property
always returns the most recent immutable snapshot while keeping the stable ``instance_id``
from construction time.

## Direct Snapshot Projection

For cases where you only need to project a snapshot without maintaining a tracker:

```python
from ralph.supervising import instance_view_from_snapshot

view = instance_view_from_snapshot(snapshot)
```

In this form, ``view.instance_id`` is taken directly from ``snapshot.run_id``.
This is suitable when the runtime identity is the orchestrator-facing identity and
``snapshot.run_id`` is not ``None``.

If ``snapshot.run_id`` is ``None`` and no override is provided, a ``ValueError`` is
raised because the supervising contract requires a stable orchestrator-facing identity.
For tracker-based supervision, use ``WorkflowInstanceTracker.update_from_snapshot`` instead.

## Stage Semantics

``current_stage`` is ``None`` in these situations:

- ``lifecycle_status`` is ``not_started`` (before any snapshot)
- ``lifecycle_status`` is ``completed`` or ``failed`` (terminal states)
- ``phase`` is ``None`` or the ``__unset__`` sentinel (active but no named stage)

This distinction is intentional: ``None`` means "no active stage" and is not an
unknown state. A supervising orchestrator can use ``lifecycle_status`` to
determine whether the instance is still running, and ``current_stage=None``
with ``lifecycle_status=active`` correctly indicates an active instance that
has not yet entered a named pipeline stage.

## Out of scope

This page does not define transport, storage, authentication, scheduling,
fleet orchestration, or protocol details.
