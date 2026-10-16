# status_display_state_and_diagnostic_reporting

## Authoritative owner
- `ralph.display.integration_display_label.IntegrationDisplayLabel` is
the single closed vocabulary for the seven integration display labels;
`ralph.display.auto_integrate_message.OperationDisplayState` is the
single closed vocabulary for the eight pipeline-state projections
(`display/integration_display_label.py:12`,
`display/auto_integrate_message.py:22`).

## Writers
- `ralph.display.auto_integrate_message.OperationDisplayState` is
  set on the durable `RebaseState.operation_display_state` field by
  the `RebaseState` post-validation population hook and by the run
  loop / runner intervention paths (`rebase_state.py:375-385`,
  `run_loop.py:2042,2109`, `runner.py:1902,1924`).
- `ralph.display.integration_display_label.DISPLAY_LABEL_MAP` is
  the producer-side string-to-label routing table, consumed by
  `display/auto_integrate_message.py:15,288`
  (`display/integration_display_label.py:25`).

## Readers
- `ralph.pipeline.runner` reads the `operation_display_state` to
  render the run-loop boundary messages via
  `format_auto_integrate_message` (`runner.py:833-845`);
  `run_loop.py` gates on it at `run_loop.py:599-612`.

## Derived projections
- `OperationDisplayState` is a closed 8-value enum
  (`display/auto_integrate_message.py:22`).
- `IntegrationDisplayLabel` is a closed 7-value enum
  (`display/integration_display_label.py:12`).

## Valid transitions
- The display states are derived projections, not transitions;
  they reflect the typed authority output of the run loop, the
  diagnostic path, the publication state machine, and the
  fan-out authority.

## Restart behaviour
On restart, the run loop rebuilds the `OperationDisplayState` from
the integration record + `RebaseState`; the display is a pure
projection and never gates dispatch or terminal success
(`display/auto_integrate_message.py`).

## UNKNOWN representation
- An unknown or new producer-side string falls through to the
  default label (the `DISPLAY_LABEL_MAP.get` default).
- `rebase_state.last_outcome is None` and
  `rebase_state.last_fan_out_decision is None` mean the
  corresponding authority has not yet decided; the display
  reflects this as "no projection yet" rather than
  misrepresenting it as a known state.

## What blocks dispatch / terminal success
- Nothing: the display state is a pure projection and never gates
  dispatch or terminal success. Display labels are the operator
  surface, not control flow.
