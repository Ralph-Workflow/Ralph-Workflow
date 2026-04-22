--- runner.py.broken
+++ runner.py
@@ -856,14 +856,3 @@
         except asyncio.CancelledError:
             pass

-from ralph.recovery.connectivity import ConnectivityMonitor
-import asyncio
-        if isinstance(evt, ConnectivityEvent):
-            nonlocal state
-            state = state.copy_with(last_connectivity_state=evt.state.value)
-
-    _monitor.add_listener(_on_connectivity_change)
-
-    exit_code = 0
-    _prev_phase = state.phase
