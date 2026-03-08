import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { configureStore } from "@reduxjs/toolkit";
import { Provider } from "react-redux";
import worktreeReducer from "../../store/slices/worktreeSlice";
import { InlineWorktreeCreate } from "./InlineWorktreeCreate";

vi.mock("../../api/tauri", () => ({
  createWorktree: vi.fn().mockResolvedValue({
    worktree: {
      path: "/my/repo/wt-51-feature",
      branch: "wt-51-feature",
      name: "wt-51-feature",
      has_active_run: false,
      is_main: false,
    },
  }),
  listWorktrees: vi.fn().mockResolvedValue([]),
  switchContext: vi.fn().mockResolvedValue(undefined),
}));

import { createWorktree } from "../../api/tauri";
import type { Mock } from "vitest";

const mockCreateWorktree = createWorktree as Mock;

function makeStore() {
  return configureStore({ reducer: { worktrees: worktreeReducer } });
}

function renderComponent(repoPath: string, onCreated = vi.fn()) {
  const store = makeStore();
  return {
    ...render(
      <Provider store={store}>
        <InlineWorktreeCreate repoPath={repoPath} onCreated={onCreated} />
      </Provider>,
    ),
    onCreated,
  };
}

describe("InlineWorktreeCreate", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockCreateWorktree.mockResolvedValue({
      worktree: {
        path: "/my/repo/wt-51-feature",
        branch: "wt-51-feature",
        name: "wt-51-feature",
        has_active_run: false,
        is_main: false,
      },
    });
  });

  it("shows repo required message and disables inputs when repoPath is empty", () => {
    renderComponent("");
    expect(screen.getByText("Select a repository first")).toBeInTheDocument();
    expect(screen.getByTestId("wt-branch-input")).toBeDisabled();
    expect(screen.getByTestId("wt-name-input")).toBeDisabled();
    expect(screen.getByTestId("wt-create-button")).toBeDisabled();
  });

  it("enables inputs when repoPath is provided", () => {
    renderComponent("/my/repo");
    expect(screen.getByTestId("wt-branch-input")).not.toBeDisabled();
    expect(screen.getByTestId("wt-name-input")).not.toBeDisabled();
  });

  it("auto-fills name from branch on blur when name is empty", () => {
    renderComponent("/my/repo");
    const branchInput = screen.getByTestId("wt-branch-input");
    fireEvent.change(branchInput, { target: { value: "wt-51-feature" } });
    fireEvent.blur(branchInput);
    expect(screen.getByTestId("wt-name-input")).toHaveValue("wt-51-feature");
  });

  it("does not overwrite name on branch blur when name is already set", () => {
    renderComponent("/my/repo");
    const branchInput = screen.getByTestId("wt-branch-input");
    const nameInput = screen.getByTestId("wt-name-input");
    fireEvent.change(nameInput, { target: { value: "custom-name" } });
    fireEvent.change(branchInput, { target: { value: "wt-51-feature" } });
    fireEvent.blur(branchInput);
    expect(nameInput).toHaveValue("custom-name");
  });

  it("does not auto-fill name when branch is empty on blur", () => {
    renderComponent("/my/repo");
    const branchInput = screen.getByTestId("wt-branch-input");
    fireEvent.blur(branchInput);
    expect(screen.getByTestId("wt-name-input")).toHaveValue("");
  });

  it("keeps create button disabled when only branch is filled", () => {
    renderComponent("/my/repo");
    fireEvent.change(screen.getByTestId("wt-branch-input"), {
      target: { value: "wt-51-feature" },
    });
    expect(screen.getByTestId("wt-create-button")).toBeDisabled();
  });

  it("enables create button when both branch and name are filled", () => {
    renderComponent("/my/repo");
    fireEvent.change(screen.getByTestId("wt-branch-input"), {
      target: { value: "wt-51-feature" },
    });
    fireEvent.change(screen.getByTestId("wt-name-input"), {
      target: { value: "wt-51-feature" },
    });
    expect(screen.getByTestId("wt-create-button")).not.toBeDisabled();
  });

  it("shows Creating... and disables button while creation is in progress", async () => {
    let resolveCreate!: (value: { worktree: object }) => void;
    mockCreateWorktree.mockImplementationOnce(
      () =>
        new Promise<{ worktree: object }>((resolve) => {
          resolveCreate = resolve;
        }),
    );
    renderComponent("/my/repo");
    fireEvent.change(screen.getByTestId("wt-branch-input"), {
      target: { value: "wt-51-feature" },
    });
    fireEvent.change(screen.getByTestId("wt-name-input"), {
      target: { value: "wt-51-feature" },
    });
    fireEvent.click(screen.getByTestId("wt-create-button"));
    await waitFor(() =>
      expect(screen.getByTestId("wt-create-button")).toHaveTextContent(
        "Creating...",
      ),
    );
    expect(screen.getByTestId("wt-create-button")).toBeDisabled();
    // Resolve to clean up
    resolveCreate({
      worktree: {
        path: "/my/repo/wt-51-feature",
        branch: "wt-51-feature",
        name: "wt-51-feature",
        has_active_run: false,
        is_main: false,
      },
    });
  });

  it("calls onCreated with worktree on successful creation", async () => {
    const { onCreated } = renderComponent("/my/repo");
    fireEvent.change(screen.getByTestId("wt-branch-input"), {
      target: { value: "wt-51-feature" },
    });
    fireEvent.change(screen.getByTestId("wt-name-input"), {
      target: { value: "wt-51-feature" },
    });
    fireEvent.click(screen.getByTestId("wt-create-button"));
    await waitFor(() => expect(onCreated).toHaveBeenCalledTimes(1));
    expect(onCreated).toHaveBeenCalledWith({
      path: "/my/repo/wt-51-feature",
      branch: "wt-51-feature",
      name: "wt-51-feature",
      has_active_run: false,
      is_main: false,
    });
  });

  it("displays error when createWorktree rejects with Error", async () => {
    mockCreateWorktree.mockRejectedValueOnce(
      new Error("git error: branch exists"),
    );
    renderComponent("/my/repo");
    fireEvent.change(screen.getByTestId("wt-branch-input"), {
      target: { value: "wt-51-feature" },
    });
    fireEvent.change(screen.getByTestId("wt-name-input"), {
      target: { value: "wt-51-feature" },
    });
    fireEvent.click(screen.getByTestId("wt-create-button"));
    await waitFor(() =>
      expect(screen.getByTestId("wt-create-error")).toBeInTheDocument(),
    );
    expect(screen.getByTestId("wt-create-error")).toHaveTextContent(
      "git error: branch exists",
    );
  });

  it("displays error when createWorktree rejects with plain object", async () => {
    mockCreateWorktree.mockRejectedValueOnce({ message: "plain object error" });
    renderComponent("/my/repo");
    fireEvent.change(screen.getByTestId("wt-branch-input"), {
      target: { value: "wt-51-feature" },
    });
    fireEvent.change(screen.getByTestId("wt-name-input"), {
      target: { value: "wt-51-feature" },
    });
    fireEvent.click(screen.getByTestId("wt-create-button"));
    await waitFor(() =>
      expect(screen.getByTestId("wt-create-error")).toBeInTheDocument(),
    );
    expect(screen.getByTestId("wt-create-error")).toHaveTextContent(
      "plain object error",
    );
  });

  it("displays stringified error for unknown rejection type", async () => {
    mockCreateWorktree.mockRejectedValueOnce("raw string error");
    renderComponent("/my/repo");
    fireEvent.change(screen.getByTestId("wt-branch-input"), {
      target: { value: "wt-51-feature" },
    });
    fireEvent.change(screen.getByTestId("wt-name-input"), {
      target: { value: "wt-51-feature" },
    });
    fireEvent.click(screen.getByTestId("wt-create-button"));
    await waitFor(() =>
      expect(screen.getByTestId("wt-create-error")).toBeInTheDocument(),
    );
    expect(screen.getByTestId("wt-create-error")).toHaveTextContent(
      "raw string error",
    );
  });
});
