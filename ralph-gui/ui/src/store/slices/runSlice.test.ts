import { describe, it, expect, vi, beforeEach } from "vitest";
import { configureStore } from "@reduxjs/toolkit";
import runReducer, {
  fetchRunDetail,
  fetchResumableRuns,
  clearRunDetail,
} from "./runSlice";
import type { RunDetail } from "../../types";

vi.mock("../../api/tauri", () => ({
  getResumableRuns: vi.fn(),
  getRunDetail: vi.fn(),
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
});
