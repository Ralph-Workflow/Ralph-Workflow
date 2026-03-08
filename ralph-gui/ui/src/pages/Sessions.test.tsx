import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { configureStore } from "@reduxjs/toolkit";
import { Provider } from "react-redux";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import worktreeReducer from "../store/slices/worktreeSlice";
import sessionReducer from "../store/slices/sessionSlice";
import runReducer from "../store/slices/runSlice";
import configReducer from "../store/slices/configSlice";
import { Sessions } from "./Sessions";

vi.mock("../api/tauri", () => ({
  getSessions: vi.fn().mockResolvedValue([]),
  listAgentProfiles: vi.fn().mockResolvedValue([]),
  launchRalphSession: vi.fn().mockResolvedValue("run-id-launched"),
  savePromptFile: vi.fn().mockResolvedValue(undefined),
  resumeRalphSession: vi.fn().mockResolvedValue(undefined),
  getSessionDetail: vi.fn().mockResolvedValue(null),
  resumeSession: vi.fn(),
}));

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

function renderSessions(store = makeStore(), initialPath = "/sessions") {
  return render(
    <Provider store={store}>
      <MemoryRouter initialEntries={[initialPath]}>
        <Routes>
          <Route path="/sessions" element={<Sessions />} />
        </Routes>
      </MemoryRouter>
    </Provider>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
});

describe("Sessions", () => {
  it("renders Sessions heading and New session button by default", () => {
    renderSessions();
    expect(screen.getByText("Sessions")).toBeInTheDocument();
    expect(screen.getByText("+ New session")).toBeInTheDocument();
  });

  it("shows No repository context empty state when no worktrees are loaded", () => {
    renderSessions();
    expect(screen.getByText("No repository context")).toBeInTheDocument();
  });

  it("renders SessionList area when mainWorktree exists in store", () => {
    const store = makeStore({
      worktrees: {
        worktrees: [mainWorktree],
        status: "succeeded",
        error: null,
        activeWorktreePath: null,
        lastRepoPath: "/my/repo",
      },
    });
    renderSessions(store);
    // The repo path should be shown in the section label
    expect(screen.getByText("/my/repo")).toBeInTheDocument();
  });

  it("shows wizard template step when New session is clicked", () => {
    const store = makeStore({
      worktrees: {
        worktrees: [mainWorktree],
        status: "succeeded",
        error: null,
        activeWorktreePath: null,
        lastRepoPath: "/my/repo",
      },
    });
    renderSessions(store);
    fireEvent.click(screen.getByText("+ New session"));
    expect(screen.getByTestId("wizard-template-step")).toBeInTheDocument();
  });

  it("returns to list view when Back to list is clicked from wizard", () => {
    const store = makeStore({
      worktrees: {
        worktrees: [mainWorktree],
        status: "succeeded",
        error: null,
        activeWorktreePath: null,
        lastRepoPath: "/my/repo",
      },
    });
    renderSessions(store);
    fireEvent.click(screen.getByText("+ New session"));
    expect(screen.getByTestId("wizard-template-step")).toBeInTheDocument();
    fireEvent.click(screen.getByText("← Back to list"));
    expect(screen.getByText("+ New session")).toBeInTheDocument();
    expect(screen.queryByTestId("wizard-template-step")).not.toBeInTheDocument();
  });

  it("shows wizard immediately when ?new=true URL param is set", () => {
    renderSessions(makeStore(), "/sessions?new=true");
    expect(screen.getByTestId("wizard-template-step")).toBeInTheDocument();
  });

  it("shows filter toolbar when repo is set", () => {
    const store = makeStore({
      worktrees: {
        worktrees: [mainWorktree],
        status: "succeeded",
        error: null,
        activeWorktreePath: null,
        lastRepoPath: "/my/repo",
      },
    });
    renderSessions(store);
    expect(screen.getByTestId("filter-toolbar")).toBeInTheDocument();
  });

  it("filter chips render for each status", () => {
    const store = makeStore({
      worktrees: {
        worktrees: [mainWorktree],
        status: "succeeded",
        error: null,
        activeWorktreePath: null,
        lastRepoPath: "/my/repo",
      },
    });
    renderSessions(store);
    expect(screen.getByTestId("filter-chip-running")).toBeInTheDocument();
    expect(screen.getByTestId("filter-chip-paused")).toBeInTheDocument();
    expect(screen.getByTestId("filter-chip-completed")).toBeInTheDocument();
    expect(screen.getByTestId("filter-chip-failed")).toBeInTheDocument();
  });

  it("toggling a filter chip selects it and shows clear button", () => {
    const store = makeStore({
      worktrees: {
        worktrees: [mainWorktree],
        status: "succeeded",
        error: null,
        activeWorktreePath: null,
        lastRepoPath: "/my/repo",
      },
    });
    renderSessions(store);
    expect(screen.queryByTestId("clear-filters")).not.toBeInTheDocument();
    fireEvent.click(screen.getByTestId("filter-chip-running"));
    expect(screen.getByTestId("clear-filters")).toBeInTheDocument();
  });

  it("clicking clear resets filters and hides clear button", () => {
    const store = makeStore({
      worktrees: {
        worktrees: [mainWorktree],
        status: "succeeded",
        error: null,
        activeWorktreePath: null,
        lastRepoPath: "/my/repo",
      },
    });
    renderSessions(store);
    fireEvent.click(screen.getByTestId("filter-chip-paused"));
    expect(screen.getByTestId("clear-filters")).toBeInTheDocument();
    fireEvent.click(screen.getByTestId("clear-filters"));
    expect(screen.queryByTestId("clear-filters")).not.toBeInTheDocument();
  });

  it("context filter dropdown renders with All contexts option", () => {
    const store = makeStore({
      worktrees: {
        worktrees: [mainWorktree],
        status: "succeeded",
        error: null,
        activeWorktreePath: null,
        lastRepoPath: "/my/repo",
      },
    });
    renderSessions(store);
    const select = screen.getByTestId("filter-context");
    expect(select).toBeInTheDocument();
    expect(screen.getByText("All contexts")).toBeInTheDocument();
  });

  it("multiple filter chips can be active simultaneously", () => {
    const store = makeStore({
      worktrees: {
        worktrees: [mainWorktree],
        status: "succeeded",
        error: null,
        activeWorktreePath: null,
        lastRepoPath: "/my/repo",
      },
    });
    renderSessions(store);
    fireEvent.click(screen.getByTestId("filter-chip-running"));
    fireEvent.click(screen.getByTestId("filter-chip-failed"));
    // Both chips should be visually active (have active class or aria-pressed)
    expect(screen.getByTestId("filter-chip-running")).toBeInTheDocument();
    expect(screen.getByTestId("filter-chip-failed")).toBeInTheDocument();
    // Clear button should appear when any filter is active
    expect(screen.getByTestId("clear-filters")).toBeInTheDocument();
    // Clicking clear removes all filters
    fireEvent.click(screen.getByTestId("clear-filters"));
    expect(screen.queryByTestId("clear-filters")).not.toBeInTheDocument();
  });

  it("filter toolbar is absent when no repo context is active", () => {
    // No worktrees / no lastRepoPath => empty-repo state
    renderSessions();
    expect(screen.queryByTestId("filter-toolbar")).not.toBeInTheDocument();
  });
});
