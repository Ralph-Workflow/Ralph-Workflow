import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { configureStore } from "@reduxjs/toolkit";
import { Provider } from "react-redux";
import { MemoryRouter } from "react-router-dom";
import worktreeReducer from "../store/slices/worktreeSlice";
import sessionReducer from "../store/slices/sessionSlice";
import runReducer from "../store/slices/runSlice";
import configReducer from "../store/slices/configSlice";
import { Worktrees } from "./Worktrees";

vi.mock("../api/tauri", () => ({
  listWorktrees: vi.fn(),
  createWorktree: vi.fn(),
  switchContext: vi.fn(),
}));

import { listWorktrees, createWorktree } from "../api/tauri";
import type { Mock } from "vitest";

const mockListWorktrees = listWorktrees as Mock;
const mockCreateWorktree = createWorktree as Mock;

const mainWorktree = {
  path: "/my/repo",
  branch: "main",
  name: "main",
  has_active_run: false,
  is_main: true,
};

function makeStore(preloaded?: object) {
  return configureStore({
    reducer: {
      worktrees: worktreeReducer,
      sessions: sessionReducer,
      runs: runReducer,
      config: configReducer,
    },
    preloadedState: preloaded,
  });
}

function renderWorktrees(store = makeStore()) {
  return render(
    <Provider store={store}>
      <MemoryRouter>
        <Worktrees />
      </MemoryRouter>
    </Provider>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
});

describe("Worktrees", () => {
  it("shows empty state when no repo context is set", () => {
    renderWorktrees();
    expect(screen.getByText("No repository context")).toBeInTheDocument();
  });

  it("shows worktree list when worktrees are loaded", () => {
    const store = makeStore({
      worktrees: {
        worktrees: [
          mainWorktree,
          {
            path: "/my/wt-50",
            branch: "wt-50",
            name: "wt-50-feature",
            has_active_run: false,
            is_main: false,
          },
        ],
        status: "succeeded",
        error: null,
        activeWorktreePath: null,
        lastRepoPath: "/my/repo",
      },
    });
    renderWorktrees(store);
    expect(screen.getByText("wt-50-feature")).toBeInTheDocument();
  });

  it("shows New worktree button when repo is loaded", () => {
    const store = makeStore({
      worktrees: {
        worktrees: [mainWorktree],
        status: "succeeded",
        error: null,
        activeWorktreePath: null,
        lastRepoPath: "/my/repo",
      },
    });
    renderWorktrees(store);
    expect(screen.getByText(/New worktree/)).toBeInTheDocument();
  });

  it("shows create form when New worktree is clicked", () => {
    const store = makeStore({
      worktrees: {
        worktrees: [mainWorktree],
        status: "succeeded",
        error: null,
        activeWorktreePath: null,
        lastRepoPath: "/my/repo",
      },
    });
    renderWorktrees(store);
    fireEvent.click(screen.getByText(/\+ New worktree/));
    expect(screen.getByText("Branch name")).toBeInTheDocument();
  });

  it("shows error when create fails", async () => {
    mockCreateWorktree.mockRejectedValueOnce(
      new Error("does not match convention"),
    );
    mockListWorktrees.mockResolvedValue([mainWorktree]);
    const store = makeStore({
      worktrees: {
        worktrees: [mainWorktree],
        status: "succeeded",
        error: null,
        activeWorktreePath: null,
        lastRepoPath: "/my/repo",
      },
    });
    renderWorktrees(store);
    fireEvent.click(screen.getByText(/\+ New worktree/));
    const inputs = screen.getAllByRole("textbox");
    const firstInput = inputs.at(0);
    const secondInput = inputs.at(1);
    if (!firstInput || !secondInput) throw new Error("Expected two textbox inputs");
    fireEvent.change(firstInput, { target: { value: "bad-branch" } });
    fireEvent.change(secondInput, { target: { value: "bad-name" } });
    fireEvent.click(screen.getByText("Create worktree"));
    await waitFor(() => {
      const errorEl = document.querySelector('[style*="status-failed"]');
      expect(errorEl).toBeInTheDocument();
    });
  });
});
