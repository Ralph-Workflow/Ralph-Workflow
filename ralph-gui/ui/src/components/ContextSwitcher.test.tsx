import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { configureStore } from "@reduxjs/toolkit";
import { Provider } from "react-redux";
import { MemoryRouter } from "react-router-dom";
import worktreeReducer from "../store/slices/worktreeSlice";
import sessionReducer from "../store/slices/sessionSlice";
import runReducer from "../store/slices/runSlice";
import configReducer from "../store/slices/configSlice";
import { ContextSwitcher } from "./ContextSwitcher";

vi.mock("../api/tauri", () => ({
  switchContext: vi.fn().mockResolvedValue(undefined),
  listWorktrees: vi.fn().mockResolvedValue([]),
}));

import { switchContext } from "../api/tauri";
import type { Mock } from "vitest";

const mockSwitchContext = switchContext as Mock;

const mainWorktree = {
  path: "/my/repo",
  branch: "main",
  name: "main",
  has_active_run: false,
  is_main: true,
};

const linkedWorktree = {
  path: "/my/wt-50-feature",
  branch: "wt-50-feature",
  name: "wt-50-feature",
  has_active_run: false,
  is_main: false,
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

function renderContextSwitcher(store = makeStore()) {
  return render(
    <Provider store={store}>
      <MemoryRouter>
        <ContextSwitcher />
      </MemoryRouter>
    </Provider>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
});

describe("ContextSwitcher", () => {
  it("shows 'no context' label when worktrees array is empty", () => {
    renderContextSwitcher();
    expect(screen.getByText("no context")).toBeInTheDocument();
  });

  it("shows 'direct repo' label when main worktree exists and activeWorktreePath is null", () => {
    const store = makeStore({
      worktrees: {
        worktrees: [mainWorktree],
        status: "succeeded",
        error: null,
        activeWorktreePath: null,
        lastRepoPath: "/my/repo",
      },
    });
    renderContextSwitcher(store);
    expect(screen.getByText("direct repo")).toBeInTheDocument();
  });

  it("dropdown is closed by default", () => {
    const store = makeStore({
      worktrees: {
        worktrees: [mainWorktree],
        status: "succeeded",
        error: null,
        activeWorktreePath: null,
        lastRepoPath: "/my/repo",
      },
    });
    renderContextSwitcher(store);
    expect(screen.queryByText("Direct repository")).not.toBeInTheDocument();
  });

  it("clicking context button opens dropdown with Direct repository option", () => {
    const store = makeStore({
      worktrees: {
        worktrees: [mainWorktree],
        status: "succeeded",
        error: null,
        activeWorktreePath: null,
        lastRepoPath: "/my/repo",
      },
    });
    renderContextSwitcher(store);
    fireEvent.click(screen.getByText("direct repo"));
    expect(screen.getByText("Direct repository")).toBeInTheDocument();
  });

  it("dropdown shows linked worktree names from store state", () => {
    const store = makeStore({
      worktrees: {
        worktrees: [mainWorktree, linkedWorktree],
        status: "succeeded",
        error: null,
        activeWorktreePath: null,
        lastRepoPath: "/my/repo",
      },
    });
    renderContextSwitcher(store);
    fireEvent.click(screen.getByText("direct repo"));
    // Multiple spans may show "wt-50-feature" (context label, branch, dropdown option)
    expect(screen.getAllByText("wt-50-feature").length).toBeGreaterThan(0);
  });

  it("selecting a worktree option calls switchContext and updates state", () => {
    const store = makeStore({
      worktrees: {
        worktrees: [mainWorktree, linkedWorktree],
        status: "succeeded",
        error: null,
        activeWorktreePath: null,
        lastRepoPath: "/my/repo",
      },
    });
    renderContextSwitcher(store);
    fireEvent.click(screen.getByText("direct repo"));
    // The dropdown now shows "wt-50-feature" as a dropdown option.
    // Use getAllByText to avoid ambiguity if multiple spans show the same text.
    const wtOptions = screen.getAllByText("wt-50-feature");
    const lastOption = wtOptions.at(-1);
    if (!lastOption) throw new Error("Expected at least one wt-50-feature element");
    fireEvent.click(lastOption);
    expect(mockSwitchContext).toHaveBeenCalledWith(
      mainWorktree.path,
      linkedWorktree.path,
    );
  });

  it("selecting Direct repository option sets activeWorktreePath to null", () => {
    const store = makeStore({
      worktrees: {
        worktrees: [mainWorktree, linkedWorktree],
        status: "succeeded",
        error: null,
        activeWorktreePath: linkedWorktree.path,
        lastRepoPath: "/my/repo",
      },
    });
    renderContextSwitcher(store);
    // Open dropdown - the label shows the worktree name since activePath is set.
    // The main button may render contextLabel and contextBranch with the same text,
    // so click the first occurrence to open the dropdown.
    const firstEl = screen.getAllByText("wt-50-feature").at(0);
    if (!firstEl) throw new Error("Expected at least one wt-50-feature element");
    fireEvent.click(firstEl);
    fireEvent.click(screen.getByText("Direct repository"));
    expect(mockSwitchContext).toHaveBeenCalledWith(mainWorktree.path, null);
  });

  it("outside click closes the dropdown", () => {
    const store = makeStore({
      worktrees: {
        worktrees: [mainWorktree],
        status: "succeeded",
        error: null,
        activeWorktreePath: null,
        lastRepoPath: "/my/repo",
      },
    });
    renderContextSwitcher(store);
    fireEvent.click(screen.getByText("direct repo"));
    expect(screen.getByText("Direct repository")).toBeInTheDocument();
    // Use mouseDown (camelCase) as per @testing-library/dom convention
    fireEvent.mouseDown(document.body);
    expect(screen.queryByText("Direct repository")).not.toBeInTheDocument();
  });

  it("shows search input when dropdown is open and worktrees exist", () => {
    const store = makeStore({
      worktrees: {
        worktrees: [mainWorktree, linkedWorktree],
        status: "succeeded",
        error: null,
        activeWorktreePath: null,
        lastRepoPath: "/my/repo",
      },
    });
    renderContextSwitcher(store);
    fireEvent.click(screen.getByText("direct repo"));
    expect(screen.getByTestId("context-search")).toBeInTheDocument();
  });

  it("typing in search filters worktrees by name (case-insensitive)", () => {
    const secondWorktree = {
      path: "/my/wt-other",
      branch: "wt-other",
      name: "wt-other-feature",
      has_active_run: false,
      is_main: false,
    };
    const store = makeStore({
      worktrees: {
        worktrees: [mainWorktree, linkedWorktree, secondWorktree],
        status: "succeeded",
        error: null,
        activeWorktreePath: null,
        lastRepoPath: "/my/repo",
      },
    });
    renderContextSwitcher(store);
    fireEvent.click(screen.getByText("direct repo"));
    const searchInput = screen.getByTestId("context-search");
    fireEvent.change(searchInput, { target: { value: "WT-50" } });
    expect(screen.queryAllByText("wt-50-feature").length).toBeGreaterThan(0);
    expect(screen.queryByText("wt-other-feature")).not.toBeInTheDocument();
  });

  it("Direct repository option is always visible regardless of search text", () => {
    const store = makeStore({
      worktrees: {
        worktrees: [mainWorktree, linkedWorktree],
        status: "succeeded",
        error: null,
        activeWorktreePath: null,
        lastRepoPath: "/my/repo",
      },
    });
    renderContextSwitcher(store);
    fireEvent.click(screen.getByText("direct repo"));
    const searchInput = screen.getByTestId("context-search");
    fireEvent.change(searchInput, { target: { value: "zzznomatch" } });
    expect(screen.getByText("Direct repository")).toBeInTheDocument();
  });

  it("clearing the search input shows all worktrees again", () => {
    const secondWorktree = {
      path: "/my/wt-other",
      branch: "wt-other",
      name: "wt-other-feature",
      has_active_run: false,
      is_main: false,
    };
    const store = makeStore({
      worktrees: {
        worktrees: [mainWorktree, linkedWorktree, secondWorktree],
        status: "succeeded",
        error: null,
        activeWorktreePath: null,
        lastRepoPath: "/my/repo",
      },
    });
    renderContextSwitcher(store);
    fireEvent.click(screen.getByText("direct repo"));
    const searchInput = screen.getByTestId("context-search");
    fireEvent.change(searchInput, { target: { value: "WT-50" } });
    expect(screen.queryByText("wt-other-feature")).not.toBeInTheDocument();
    fireEvent.change(searchInput, { target: { value: "" } });
    expect(screen.getByText("wt-other-feature")).toBeInTheDocument();
  });

  it("worktrees with has_active_run=true show an active-run indicator", () => {
    const activeWorktree = {
      path: "/my/wt-active",
      branch: "wt-active",
      name: "wt-active-run",
      has_active_run: true,
      is_main: false,
    };
    const store = makeStore({
      worktrees: {
        worktrees: [mainWorktree, activeWorktree],
        status: "succeeded",
        error: null,
        activeWorktreePath: null,
        lastRepoPath: "/my/repo",
      },
    });
    renderContextSwitcher(store);
    fireEvent.click(screen.getByText("direct repo"));
    expect(screen.getByText("wt-active-run")).toBeInTheDocument();
    expect(screen.getByTestId("active-run-badge-/my/wt-active")).toBeInTheDocument();
  });

  it("context trigger button has accessible label for screen readers", () => {
    const store = makeStore({
      worktrees: {
        worktrees: [mainWorktree],
        status: "succeeded",
        error: null,
        activeWorktreePath: null,
        lastRepoPath: "/my/repo",
      },
    });
    renderContextSwitcher(store);
    // The trigger button must have an aria-label so screen readers can identify it
    const triggerBtn = screen.getByRole("button", { name: /switch active context/i });
    expect(triggerBtn).toBeInTheDocument();
  });
});
