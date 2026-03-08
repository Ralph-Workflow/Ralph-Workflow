import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { configureStore } from "@reduxjs/toolkit";
import worktreeReducer, {
  fetchWorktrees,
  createNewWorktree,
  switchActiveContext,
  setActiveWorktree,
  initializeRepo,
} from "./worktreeSlice";
import type { WorktreeInfo } from "../../types";

vi.mock("../../api/tauri", () => ({
  listWorktrees: vi.fn(),
  createWorktree: vi.fn(),
  switchContext: vi.fn(),
}));

import { listWorktrees, createWorktree, switchContext } from "../../api/tauri";
import type { Mock } from "vitest";

const mockListWorktrees = listWorktrees as Mock;
const mockCreateWorktree = createWorktree as Mock;
const mockSwitchContext = switchContext as Mock;

function makeStore() {
  return configureStore({
    reducer: { worktrees: worktreeReducer },
  });
}

const mockWorktree: WorktreeInfo = {
  path: "/my/wt-50-feature",
  branch: "wt-50-feature",
  name: "wt-50-feature",
  has_active_run: false,
  is_main: false,
};

const mockMainWorktree: WorktreeInfo = {
  path: "/my/repo",
  branch: "main",
  name: "main",
  has_active_run: false,
  is_main: true,
};

beforeEach(() => {
  vi.clearAllMocks();
  localStorage.clear();
});

afterEach(() => {
  localStorage.clear();
});

describe("worktreeSlice", () => {
  it("initial state has empty worktrees array", () => {
    const store = makeStore();
    const state = store.getState().worktrees;
    expect(state.worktrees).toEqual([]);
    expect(state.status).toBe("idle");
    expect(state.activeWorktreePath).toBeNull();
  });

  it("fetchWorktrees.fulfilled populates worktrees", async () => {
    mockListWorktrees.mockResolvedValueOnce([mockMainWorktree, mockWorktree]);
    const store = makeStore();
    await store.dispatch(fetchWorktrees("/my/repo"));
    const state = store.getState().worktrees;
    expect(state.status).toBe("succeeded");
    expect(state.worktrees).toHaveLength(2);
    expect(state.worktrees[0]?.is_main).toBe(true);
  });

  it("fetchWorktrees.rejected sets error", async () => {
    mockListWorktrees.mockRejectedValueOnce(new Error("Not a git repo"));
    const store = makeStore();
    await store.dispatch(fetchWorktrees("/not/a/repo"));
    const state = store.getState().worktrees;
    expect(state.status).toBe("failed");
    expect(state.error).toBe("Not a git repo");
  });

  it("setActiveWorktree updates activeWorktreePath in state", () => {
    const store = makeStore();
    store.dispatch(setActiveWorktree("/my/wt-50-feature"));
    expect(store.getState().worktrees.activeWorktreePath).toBe(
      "/my/wt-50-feature",
    );
  });

  it("setActiveWorktree with null switches to direct-repo mode", () => {
    const store = makeStore();
    store.dispatch(setActiveWorktree("/my/wt-50"));
    store.dispatch(setActiveWorktree(null));
    expect(store.getState().worktrees.activeWorktreePath).toBeNull();
  });

  it("createNewWorktree.fulfilled appends new worktree to list", async () => {
    mockCreateWorktree.mockResolvedValueOnce({ worktree: mockWorktree });
    const store = makeStore();
    await store.dispatch(
      createNewWorktree({
        repoPath: "/my/repo",
        branch: "wt-50-feature",
        name: "wt-50-feature",
      }),
    );
    const state = store.getState().worktrees;
    expect(state.worktrees).toHaveLength(1);
    expect(state.worktrees[0]?.name).toBe("wt-50-feature");
  });

  it("switchActiveContext.fulfilled updates activeWorktreePath", async () => {
    mockSwitchContext.mockResolvedValueOnce(undefined);
    const store = makeStore();
    await store.dispatch(
      switchActiveContext({
        repoPath: "/my/repo",
        worktreePath: "/my/wt-50-feature",
      }),
    );
    expect(store.getState().worktrees.activeWorktreePath).toBe(
      "/my/wt-50-feature",
    );
  });

  it("fetchWorktrees.pending sets status to loading", () => {
    mockListWorktrees.mockReturnValue(new Promise(() => undefined));
    const store = makeStore();
    void store.dispatch(fetchWorktrees("/my/repo"));
    expect(store.getState().worktrees.status).toBe("loading");
  });

  // --- initializeRepo tests ---

  it("initializeRepo.pending sets status to loading", () => {
    mockListWorktrees.mockReturnValue(new Promise(() => undefined));
    const store = makeStore();
    void store.dispatch(initializeRepo("/my/repo"));
    expect(store.getState().worktrees.status).toBe("loading");
  });

  it("initializeRepo.fulfilled sets worktrees and lastRepoPath", async () => {
    mockListWorktrees.mockResolvedValueOnce([mockMainWorktree, mockWorktree]);
    const store = makeStore();
    await store.dispatch(initializeRepo("/my/repo"));
    const state = store.getState().worktrees;
    expect(state.status).toBe("succeeded");
    expect(state.worktrees).toHaveLength(2);
    expect(state.lastRepoPath).toBe("/my/repo");
  });

  it("initializeRepo.fulfilled persists lastRepoPath to localStorage", async () => {
    mockListWorktrees.mockResolvedValueOnce([mockMainWorktree]);
    const store = makeStore();
    await store.dispatch(initializeRepo("/my/repo"));
    expect(localStorage.getItem("ralph_gui_last_repo")).toBe("/my/repo");
  });

  it("initializeRepo.rejected sets status to failed with error", async () => {
    mockListWorktrees.mockRejectedValueOnce(new Error("Not a git repo"));
    const store = makeStore();
    await store.dispatch(initializeRepo("/bad/path"));
    const state = store.getState().worktrees;
    expect(state.status).toBe("failed");
    expect(state.error).toBe("Not a git repo");
  });

  it("createNewWorktree.rejected stores error and leaves worktrees unchanged", async () => {
    mockCreateWorktree.mockRejectedValueOnce(new Error("Branch already exists"));
    const store = makeStore();
    await store.dispatch(
      createNewWorktree({
        repoPath: "/my/repo",
        branch: "existing-branch",
        name: "existing-branch",
      }),
    );
    const state = store.getState().worktrees;
    expect(state.worktrees).toHaveLength(0);
    expect(state.error).toBe("Branch already exists");
  });

  it("switchActiveContext with null worktreePath sets activeWorktreePath to null", async () => {
    mockSwitchContext.mockResolvedValueOnce(undefined);
    const store = makeStore();
    // First switch to a worktree
    await store.dispatch(
      switchActiveContext({ repoPath: "/my/repo", worktreePath: "/my/wt-50" }),
    );
    expect(store.getState().worktrees.activeWorktreePath).toBe("/my/wt-50");
    // Then switch back to direct repo
    mockSwitchContext.mockResolvedValueOnce(undefined);
    await store.dispatch(
      switchActiveContext({ repoPath: "/my/repo", worktreePath: null }),
    );
    expect(store.getState().worktrees.activeWorktreePath).toBeNull();
  });
});
