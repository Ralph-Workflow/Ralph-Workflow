import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { configureStore } from "@reduxjs/toolkit";
import runReducer, {
  fetchRunDetail,
  fetchResumableRuns,
  clearRunDetail,
  startPolling,
  stopPolling,
  startPollingInterval,
  stopPollingInterval,
  pollRunStatus,
} from "./runSlice";
import type { RunDetail } from "../../types";

vi.mock("../../api/tauri", () => ({
  getResumableRuns: vi.fn(),
  getRunDetail: vi.fn(),
  getRunStatus: vi.fn(),
}));

import { getResumableRuns, getRunDetail } from "../../api/tauri";
import type { Mock } from "vitest";

const mockGetResumableRuns = getResumableRuns as Mock;
const mockGetRunDetail = getRunDetail as Mock;

function makeStore() {
  return configureStore({
    reducer: { runs: runReducer },
  });
}

const mockRunDetail: RunDetail = {
  run_id: "run-abc-123",
  status: "Paused",
  current_phase: "Development",
  last_checkpoint: "2024-01-01 12:00:00",
  agent_profile: "claude/codex",
  repo_path: "/my/repo",
  worktree_path: null,
  created_at: "2024-01-01 10:00:00",
  description: "Interrupted at Development",
};

beforeEach(() => {
  vi.clearAllMocks();
  vi.useFakeTimers();
  // Ensure polling is stopped between tests
  stopPollingInterval();
});

afterEach(() => {
  stopPollingInterval();
  vi.useRealTimers();
});

describe("runSlice", () => {
  it("initial state has no active run and empty run list", () => {
    const store = makeStore();
    const state = store.getState().runs;
    expect(state.runDetail).toBeNull();
    expect(state.resumableRuns).toEqual([]);
    expect(state.status).toBe("idle");
  });

  it("fetchRunDetail.fulfilled populates runDetail", async () => {
    mockGetRunDetail.mockResolvedValueOnce(mockRunDetail);
    const store = makeStore();
    await store.dispatch(fetchRunDetail("run-abc-123"));
    const state = store.getState().runs;
    expect(state.status).toBe("succeeded");
    expect(state.runDetail).not.toBeNull();
    expect(state.runDetail?.run_id).toBe("run-abc-123");
    expect(state.runDetail?.current_phase).toBe("Development");
    expect(state.runDetail?.agent_profile).toBe("claude/codex");
  });

  it("fetchRunDetail.rejected sets error", async () => {
    mockGetRunDetail.mockRejectedValueOnce(new Error("Run not found"));
    const store = makeStore();
    await store.dispatch(fetchRunDetail("nonexistent"));
    const state = store.getState().runs;
    expect(state.status).toBe("failed");
    expect(state.error).toBe("Run not found");
  });

  it("fetchResumableRuns.fulfilled populates resumable list", async () => {
    mockGetResumableRuns.mockResolvedValueOnce([mockRunDetail]);
    const store = makeStore();
    await store.dispatch(fetchResumableRuns("/my/repo"));
    const state = store.getState().runs;
    expect(state.resumableRuns).toHaveLength(1);
    expect(state.resumableRuns[0]?.run_id).toBe("run-abc-123");
  });

  it("fetchResumableRuns returns empty list when no runs", async () => {
    mockGetResumableRuns.mockResolvedValueOnce([]);
    const store = makeStore();
    await store.dispatch(fetchResumableRuns("/my/repo"));
    expect(store.getState().runs.resumableRuns).toHaveLength(0);
  });

  it("status transitions are correctly modeled", async () => {
    mockGetRunDetail.mockResolvedValueOnce({ ...mockRunDetail, status: "Running" });
    const store = makeStore();
    await store.dispatch(fetchRunDetail("run-abc-123"));
    expect(store.getState().runs.runDetail?.status).toBe("Running");
  });

  it("clearRunDetail removes runDetail from state", async () => {
    mockGetRunDetail.mockResolvedValueOnce(mockRunDetail);
    const store = makeStore();
    await store.dispatch(fetchRunDetail("run-abc-123"));
    store.dispatch(clearRunDetail());
    expect(store.getState().runs.runDetail).toBeNull();
  });

  it("startPolling and stopPolling reducer actions execute without error", () => {
    const store = makeStore();
    store.dispatch(startPolling({ repoPath: "/my/repo", worktreePath: null }));
    store.dispatch(stopPolling());
    // startPolling is a no-op in the reducer (side effect is external interval).
    // stopPolling clears the interval. Both should not throw.
    expect(store.getState().runs.status).toBe("idle");
  });

  it("startPollingInterval starts interval that dispatches pollRunStatus", async () => {
    const mockStatus = {
      status: "Running" as const,
      run_id: "run-123",
      current_phase: "Development",
      last_checkpoint: null,
    };
    const { getRunStatus } = await import("../../api/tauri");
    (getRunStatus as ReturnType<typeof vi.fn>).mockResolvedValue(mockStatus);

    const store = makeStore();
    const dispatch = vi.fn((thunk) => store.dispatch(thunk as ReturnType<typeof pollRunStatus>));

    startPollingInterval(dispatch as Parameters<typeof startPollingInterval>[0], "/my/repo", null);
    vi.advanceTimersByTime(5001);
    expect(dispatch).toHaveBeenCalled();
    stopPollingInterval();
  });

  it("startPollingInterval is idempotent — second call does not start a second interval", () => {
    const dispatch = vi.fn();
    startPollingInterval(dispatch as Parameters<typeof startPollingInterval>[0], "/my/repo", null);
    startPollingInterval(dispatch as Parameters<typeof startPollingInterval>[0], "/my/repo", null);
    vi.advanceTimersByTime(5001);
    // Even if two calls are made, only one interval should have been created
    // so dispatch is called at most once per interval tick, not twice.
    expect(dispatch.mock.calls.length).toBeLessThanOrEqual(1);
    stopPollingInterval();
  });

  it("stopPollingInterval clears the interval and is idempotent", () => {
    const dispatch = vi.fn();
    startPollingInterval(dispatch as Parameters<typeof startPollingInterval>[0], "/my/repo", null);
    stopPollingInterval();
    stopPollingInterval(); // second call should be safe
    vi.advanceTimersByTime(10000);
    expect(dispatch).not.toHaveBeenCalled();
  });
});
