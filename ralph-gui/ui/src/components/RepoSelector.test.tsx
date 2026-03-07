import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { configureStore } from "@reduxjs/toolkit";
import { Provider } from "react-redux";
import worktreeReducer from "../store/slices/worktreeSlice";
import sessionReducer from "../store/slices/sessionSlice";
import runReducer from "../store/slices/runSlice";
import configReducer from "../store/slices/configSlice";
import { RepoSelector } from "./RepoSelector";

vi.mock("../api/tauri", () => ({
  listWorktrees: vi.fn(),
  createWorktree: vi.fn(),
  switchContext: vi.fn(),
}));

import { listWorktrees } from "../api/tauri";
import type { Mock } from "vitest";

const mockListWorktrees = listWorktrees as Mock;

function makeStore() {
  return configureStore({
    reducer: {
      worktrees: worktreeReducer,
      sessions: sessionReducer,
      runs: runReducer,
      config: configReducer,
    },
  });
}

function renderSelector(onSelected = vi.fn()) {
  const store = makeStore();
  return {
    store,
    ...render(
      <Provider store={store}>
        <RepoSelector onRepoSelected={onSelected} />
      </Provider>,
    ),
  };
}

beforeEach(() => {
  vi.clearAllMocks();
  localStorage.clear();
});

describe("RepoSelector", () => {
  it("renders input and Open button", () => {
    renderSelector();
    expect(screen.getByTestId("repo-path-input")).toBeInTheDocument();
    expect(screen.getByTestId("open-repo-button")).toBeInTheDocument();
  });

  it("Open button is disabled when input is empty", () => {
    renderSelector();
    expect(screen.getByTestId("open-repo-button")).toBeDisabled();
  });

  it("dispatches initializeRepo when Open is clicked with a valid path", async () => {
    mockListWorktrees.mockResolvedValueOnce([
      {
        path: "/my/repo",
        branch: "main",
        name: "main",
        has_active_run: false,
        is_main: true,
      },
    ]);
    const onSelected = vi.fn();
    const { store } = renderSelector(onSelected);
    fireEvent.change(screen.getByTestId("repo-path-input"), {
      target: { value: "/my/repo" },
    });
    fireEvent.click(screen.getByTestId("open-repo-button"));
    await waitFor(() => {
      expect(store.getState().worktrees.worktrees).toHaveLength(1);
    });
    expect(onSelected).toHaveBeenCalledWith("/my/repo");
  });

  it("shows error when initializeRepo fails", async () => {
    mockListWorktrees.mockRejectedValueOnce(new Error("Not a git repo"));
    renderSelector();
    fireEvent.change(screen.getByTestId("repo-path-input"), {
      target: { value: "/bad/path" },
    });
    fireEvent.click(screen.getByTestId("open-repo-button"));
    await waitFor(() => {
      expect(screen.getByTestId("repo-error")).toBeInTheDocument();
    });
    expect(screen.getByText("Not a git repo")).toBeInTheDocument();
  });
});
