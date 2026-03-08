import { createAsyncThunk, createSlice } from "@reduxjs/toolkit";
import {
  getResumableRuns,
  getRunDetail,
  getRunStatus,
  notifyRunStatusChange,
} from "../../api/tauri";
import type { RunDetail, RunStatus, RunStatusSummary } from "../../types";

// Module-level polling interval handle (non-serializable, kept outside Redux state)
let pollingIntervalId: ReturnType<typeof setInterval> | null = null;

// Track the previous polling status for transition detection (non-serializable)
let previousPollingStatus: RunStatus | null = null;

const POLL_INTERVAL_MS = 5000;

/// Status transitions that warrant a notification.
/// Running→anything means the run has concluded or changed state.
const NOTIFY_TRANSITIONS = new Set<RunStatus>(["Paused", "Failed", "Completed"]);

interface RunState {
  runDetail: RunDetail | null;
  resumableRuns: RunDetail[];
  status: "idle" | "loading" | "succeeded" | "failed";
  error: string | null;
  pollingStatus: RunStatusSummary | null;
}

const initialState: RunState = {
  runDetail: null,
  resumableRuns: [],
  status: "idle",
  error: null,
  pollingStatus: null,
};

export const fetchRunDetail = createAsyncThunk(
  "runs/fetchRunDetail",
  async (runId: string) => {
    return getRunDetail(runId);
  },
);

export const fetchResumableRuns = createAsyncThunk(
  "runs/fetchResumableRuns",
  async (repoPath: string) => {
    return getResumableRuns(repoPath);
  },
);

export const pollRunStatus = createAsyncThunk(
  "runs/pollRunStatus",
  async (args: { repoPath: string; worktreePath: string | null }) => {
    return getRunStatus(args.repoPath, args.worktreePath);
  },
);

const runSlice = createSlice({
  name: "runs",
  initialState,
  reducers: {
    clearRunDetail: (state) => {
      state.runDetail = null;
    },
    startPolling: (
      _state,
      action: { payload: { repoPath: string; worktreePath: string | null } },
    ) => {
      // Guard against double-start
      if (pollingIntervalId !== null) return;
      const { repoPath, worktreePath } = action.payload;
      // The actual dispatch happens in the component via thunk; this action
      // stores the config so middleware can schedule polls.
      // We use a module-level interval here, kicked off when reducer runs.
      // NOTE: This action is handled by a side-effect in the component that
      // calls startPolling — see Sessions.tsx for the useEffect pattern.
      void repoPath;
      void worktreePath;
    },
    stopPolling: () => {
      if (pollingIntervalId !== null) {
        clearInterval(pollingIntervalId);
        pollingIntervalId = null;
      }
      previousPollingStatus = null;
    },
  },
  extraReducers: (builder) => {
    builder
      .addCase(fetchRunDetail.pending, (state) => {
        state.status = "loading";
        state.error = null;
      })
      .addCase(fetchRunDetail.fulfilled, (state, action) => {
        state.status = "succeeded";
        state.runDetail = action.payload;
      })
      .addCase(fetchRunDetail.rejected, (state, action) => {
        state.status = "failed";
        state.error = action.error.message ?? "Unknown error";
      })
      .addCase(fetchResumableRuns.fulfilled, (state, action) => {
        state.resumableRuns = action.payload;
      })
      .addCase(pollRunStatus.fulfilled, (state, action) => {
        const newStatus = action.payload.status;
        const prev = previousPollingStatus;

        // Detect status transition: if previous was Running and new is a terminal state,
        // fire a desktop notification (non-serializable side effect via module-level call).
        if (prev === "Running" && NOTIFY_TRANSITIONS.has(newStatus)) {
          const runId = action.payload.run_id ?? "unknown";
          const context = state.runDetail?.repo_path ?? state.runDetail?.worktree_path ?? "";
          // Fire and forget — notifications are non-critical
          void notifyRunStatusChange(newStatus, runId, context);
        }

        previousPollingStatus = newStatus;
        state.pollingStatus = action.payload;
      });
  },
});

export const { clearRunDetail, startPolling, stopPolling } = runSlice.actions;
export default runSlice.reducer;

/// Helper to schedule a polling interval that dispatches pollRunStatus.
/// Call startPollingInterval from a React component; clear with stopPollingInterval.
export function startPollingInterval(
  dispatch: (action: ReturnType<typeof pollRunStatus>) => void,
  repoPath: string,
  worktreePath: string | null,
): void {
  if (pollingIntervalId !== null) return;
  pollingIntervalId = setInterval(() => {
    void dispatch(pollRunStatus({ repoPath, worktreePath }));
  }, POLL_INTERVAL_MS);
}

export function stopPollingInterval(): void {
  if (pollingIntervalId !== null) {
    clearInterval(pollingIntervalId);
    pollingIntervalId = null;
  }
  previousPollingStatus = null;
}
