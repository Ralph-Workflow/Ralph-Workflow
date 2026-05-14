# Supervising API

This page is for developers and integrators building orchestrators that supervise one or more Ralph Workflow instances.

If you only need to run Ralph Workflow interactively, this page is not relevant to your use case.

## Overview

The supervising API exposes a stable, read-only view of a running workflow instance for orchestration and monitoring.

Use it to inspect:

- the instance identity,
- the lifecycle status,
- the current pipeline stage,
- recent operational activity.

## InstanceStatus

`InstanceStatus` describes the observable lifecycle state of a workflow instance:

- `not_started` — no run identity has been assigned yet
- `active` — the pipeline is currently executing a stage
- `waiting` — the pipeline is active but waiting on child work
- `completed` — the instance reached a successful terminal state
- `failed` — the instance failed or was interrupted

## WorkflowInstanceView

`WorkflowInstanceView` is the immutable snapshot surface for orchestration.

Fields:

- `instance_id`: stable instance identity, or `None` before startup
- `lifecycle_status`: one of `InstanceStatus`
- `current_stage`: active pipeline stage name, or `None`
- `recent_activity`: recent operational output, ordered oldest to newest

## Wiring

Connect the supervising view to a live workflow through `PipelineSubscriber.__init__(..., on_snapshot=...)`.

```python
from ralph.supervising import instance_view_from_snapshot

subscriber = PipelineSubscriber(
    ...,
    on_snapshot=lambda snap: on_new_view(instance_view_from_snapshot(snap)),
)
```

Every call to `subscriber.notify()`, `record_waiting_status()`, or `record_activity()` publishes the latest snapshot through that callback.

## Out of scope

This page does not define transport, storage, authentication, scheduling, or fleet orchestration.
