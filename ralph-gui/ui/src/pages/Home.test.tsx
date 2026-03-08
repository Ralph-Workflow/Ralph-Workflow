import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { configureStore } from "@reduxjs/toolkit";
import { Provider } from "react-redux";
import { MemoryRouter } from "react-router-dom";
import type * as ReactRouterDom from "react-router-dom";
import runReducer from "../store/slices/runSlice";
import sessionReducer from "../store/slices/sessionSlice";
import worktreeReducer from "../store/slices/worktreeSlice";
import configReducer from "../store/slices/configSlice";
import { Home } from "./Home";

const mockNavigate = vi.fn();

vi.mock("react-router-dom", async () => {
  const actual = await vi.importActual<typeof ReactRouterDom>("react-router-dom");
  return {
    ...actual,
    useNavigate: () => mockNavigate,
  };
});

vi.mock("../api/tauri", () => ({
  listWorktrees: vi.fn().mockResolvedValue([]),
  getResumableRuns: vi.fn().mockResolvedValue([]),
  switchContext: vi.fn().mockResolvedValue(undefined),
}));

const mainWorktree = {
  path: "/my/repo",
  branch: "main",
  name: "main",
  has_active_run: false,
  is_main: true,
};

const resumableRun = {
  run_id: "run-abc-123",
  status: "Paused" as const,
  current_phase: "Review",
  last_checkpoint: "2024-01-01 12:00:00",
  agent_profile: "claude/codex",
  repo_path: "/my/repo",
  worktree_path: null,
  created_at: "2024-01-01 10:00:00",
  description: "Test run",
};

function makeStore(preloaded?: object) {
  return configureStore({
    reducer: {
      runs: runReducer,
      sessions: sessionReducer,
      worktrees: worktreeReducer,
      config: configReducer,
    },
    preloadedState: preloaded,
  });
}

function renderHome(store = makeStore()) {
  return render(
    <Provider store={store}>
      <MemoryRouter>
        <Home />
      </MemoryRouter>
    </Provider>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
});

describe("Home", () => {
  it("shows welcome screen when no worktrees and no resumable runs", () => {
    renderHome();
    expect(
      screen.getByText(/Welcome to Ralph Workflow/),
    ).toBeInTheDocument();
  });

  it("shows stat cards with correct counts when worktrees and runs are loaded", () => {
    const store = makeStore({
      worktrees: {
        worktrees: [mainWorktree],
        status: "succeeded",
        error: null,
        activeWorktreePath: null,
        lastRepoPath: "/my/repo",
      },
      runs: {
        resumableRuns: [resumableRun],
        runDetail: null,
        status: "idle",
        error: null,
        pollingStatus: null,
      },
    });
    renderHome(store);
    expect(screen.getByText("Active worktrees")).toBeInTheDocument();
    expect(screen.getByText("Resumable runs")).toBeInTheDocument();
  });

  it("navigates to /runs/:runId when View button is clicked on interrupted run", () => {
    const store = makeStore({
      worktrees: {
        worktrees: [mainWorktree],
        status: "succeeded",
        error: null,
        activeWorktreePath: null,
        lastRepoPath: "/my/repo",
      },
      runs: {
        resumableRuns: [resumableRun],
        runDetail: null,
        status: "idle",
        error: null,
        pollingStatus: null,
      },
    });
    renderHome(store);
    fireEvent.click(screen.getByText("View"));
    expect(mockNavigate).toHaveBeenCalledWith(`/runs/${resumableRun.run_id}`);
  });

  it("navigates to /sessions when New session button is clicked", () => {
    const store = makeStore({
      worktrees: {
        worktrees: [mainWorktree],
        status: "succeeded",
        error: null,
        activeWorktreePath: null,
        lastRepoPath: "/my/repo",
      },
      runs: {
        resumableRuns: [],
        runDetail: null,
        status: "idle",
        error: null,
        pollingStatus: null,
      },
    });
    renderHome(store);
    fireEvent.click(screen.getByText("New session"));
    expect(mockNavigate).toHaveBeenCalledWith("/sessions");
  });

  it("navigates to /worktrees when Worktrees button is clicked", () => {
    const store = makeStore({
      worktrees: {
        worktrees: [mainWorktree],
        status: "succeeded",
        error: null,
        activeWorktreePath: null,
        lastRepoPath: "/my/repo",
      },
      runs: {
        resumableRuns: [],
        runDetail: null,
        status: "idle",
        error: null,
        pollingStatus: null,
      },
    });
    renderHome(store);
    fireEvent.click(screen.getByText("Worktrees"));
    expect(mockNavigate).toHaveBeenCalledWith("/worktrees");
  });
});
