// NOTE: localStorage is cleared in beforeEach to prevent preset cross-contamination.
// All preset-related tests use ralph_gui_presets as the storage key.
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { configureStore } from "@reduxjs/toolkit";
import { Provider } from "react-redux";
import worktreeReducer from "../../store/slices/worktreeSlice";
import sessionReducer from "../../store/slices/sessionSlice";
import runReducer from "../../store/slices/runSlice";
import configReducer from "../../store/slices/configSlice";
import promptReducer from "../../store/slices/promptSlice";
import agentProfileReducer from "../../store/slices/agentProfileSlice";
import { NewSessionWizard } from "./NewSessionWizard";

vi.mock("../../api/tauri", () => ({
  listAgentProfiles: vi.fn().mockResolvedValue([]),
  savePromptFile: vi.fn().mockResolvedValue(undefined),
  getSessions: vi.fn().mockResolvedValue([]),
  createSession: vi.fn().mockResolvedValue({
    run_id: "test-run-id",
    status: "pending",
    repo_path: "/my/repo",
    worktree_path: null,
    created_at: "2024-01-01",
    description: "test",
    developer_agent: "",
    reviewer_agent: "",
    phase: "Pending",
  }),
  resumeSession: vi.fn(),
  resumeRalphSession: vi.fn(),
  getSessionDetail: vi.fn(),
  launchRalphSession: vi.fn().mockResolvedValue("test-run-id-launched"),
  createWorktree: vi.fn().mockResolvedValue({
    worktree: {
      path: "/my/wt-51-feature",
      branch: "wt-51-feature",
      name: "wt-51-feature",
      has_active_run: false,
      is_main: false,
    },
  }),
  listWorktrees: vi.fn().mockResolvedValue([]),
  switchContext: vi.fn().mockResolvedValue(undefined),
}));

import { launchRalphSession, createWorktree } from "../../api/tauri";
import type { Mock } from "vitest";

const mockLaunchRalphSession = launchRalphSession as Mock;
const mockCreateWorktree = createWorktree as Mock;

const mainWorktree = {
  path: "/my/repo",
  branch: "main",
  name: "main",
  has_active_run: false,
  is_main: true,
};

function makeStore() {
  return configureStore({
    reducer: {
      worktrees: worktreeReducer,
      sessions: sessionReducer,
      runs: runReducer,
      config: configReducer,
      prompt: promptReducer,
      agentProfile: agentProfileReducer,
    },
  });
}

function makeStoreWithWorktrees() {
  return configureStore({
    reducer: {
      worktrees: worktreeReducer,
      sessions: sessionReducer,
      runs: runReducer,
      config: configReducer,
      prompt: promptReducer,
      agentProfile: agentProfileReducer,
    },
    preloadedState: {
      worktrees: {
        worktrees: [mainWorktree],
        status: "succeeded" as const,
        error: null,
        activeWorktreePath: null,
        lastRepoPath: "/my/repo",
      },
    },
  });
}

function renderWizard(onClose = vi.fn()) {
  const store = makeStore();
  return {
    store,
    ...render(
      <Provider store={store}>
        <NewSessionWizard onClose={onClose} />
      </Provider>,
    ),
  };
}

function renderWizardWithWorktrees(onClose = vi.fn()) {
  const store = makeStoreWithWorktrees();
  return {
    store,
    ...render(
      <Provider store={store}>
        <NewSessionWizard onClose={onClose} />
      </Provider>,
    ),
  };
}

beforeEach(() => {
  vi.clearAllMocks();
  localStorage.clear();
});

describe("NewSessionWizard", () => {
  it("starts on the template step", () => {
    renderWizard();
    expect(screen.getByTestId("wizard-template-step")).toBeInTheDocument();
    expect(screen.getByText("Choose a starting template")).toBeInTheDocument();
  });

  it("advances to config step when a template is selected", () => {
    renderWizard();
    fireEvent.click(screen.getByTestId("template-feature"));
    expect(screen.getByTestId("wizard-config-step")).toBeInTheDocument();
  });

  it("shows Review with AI toggle button on config step", () => {
    renderWizard();
    fireEvent.click(screen.getByTestId("template-blank"));
    expect(screen.getByTestId("toggle-review-panel")).toBeInTheDocument();
  });

  it("shows review panel when Review with AI is clicked", () => {
    renderWizard();
    fireEvent.click(screen.getByTestId("template-feature"));
    fireEvent.click(screen.getByTestId("toggle-review-panel"));
    expect(screen.getByTestId("prompt-review-panel")).toBeInTheDocument();
  });

  it("can navigate back to template step from config", () => {
    renderWizard();
    fireEvent.click(screen.getByTestId("template-feature"));
    expect(screen.getByTestId("wizard-config-step")).toBeInTheDocument();
    fireEvent.click(screen.getByText("← Templates"));
    expect(screen.getByTestId("wizard-template-step")).toBeInTheDocument();
  });

  it("advances to preflight step and shows Launch session button", async () => {
    const { store } = renderWizard();
    fireEvent.click(screen.getByTestId("template-feature"));
    // Set a valid repo path
    fireEvent.change(screen.getByPlaceholderText("/path/to/your/repo"), {
      target: { value: "/my/repo" },
    });
    // Proceed to preflight
    await waitFor(() => {
      expect(screen.getByTestId("review-launch-button")).not.toBeDisabled();
    });
    fireEvent.click(screen.getByTestId("review-launch-button"));
    // Advancing to preflight shows the "Launch session" confirm button
    await waitFor(() => {
      expect(screen.getByText("Launch session")).toBeInTheDocument();
    });
    // Sessions state should eventually have the new session
    const state = store.getState();
    expect(state).toBeDefined();
  });

  it("calls launchRalphSession on confirm from preflight step", async () => {
    renderWizard();
    fireEvent.click(screen.getByTestId("template-feature"));
    fireEvent.change(screen.getByPlaceholderText("/path/to/your/repo"), {
      target: { value: "/my/repo" },
    });
    fireEvent.click(screen.getByTestId("review-launch-button"));
    await waitFor(() =>
      expect(screen.getByText("Launch session")).toBeInTheDocument(),
    );
    fireEvent.click(screen.getByText("Launch session"));
    await waitFor(() => expect(mockLaunchRalphSession).toHaveBeenCalledOnce());
  });

  // --- Preset save/load/delete tests ---

  it("shows Save as preset button on config step", () => {
    renderWizard();
    fireEvent.click(screen.getByTestId("template-feature"));
    expect(screen.getByTestId("save-preset-button")).toBeInTheDocument();
  });

  it("saving a preset writes to localStorage under ralph_gui_presets", () => {
    renderWizard();
    fireEvent.click(screen.getByTestId("template-feature"));
    // Enter a preset name and save
    const nameInput = screen.getByTestId("preset-name-input");
    fireEvent.change(nameInput, { target: { value: "My Preset" } });
    fireEvent.click(screen.getByTestId("save-preset-button"));
    const stored = JSON.parse(localStorage.getItem("ralph_gui_presets") ?? "[]") as unknown[];
    expect(stored).toHaveLength(1);
    expect((stored[0] as { name: string }).name).toBe("My Preset");
  });

  it("loads a saved preset and populates dev iterations field", () => {
    // Seed a preset in localStorage
    localStorage.setItem(
      "ralph_gui_presets",
      JSON.stringify([
        {
          name: "Quick Run",
          developerIterations: 3,
          reviewerPasses: 1,
          agentProfile: null,
        },
      ]),
    );
    renderWizard();
    fireEvent.click(screen.getByTestId("template-feature"));
    // Open the load dropdown and select the preset
    const loadSelect = screen.getByTestId("load-preset-select");
    fireEvent.change(loadSelect, { target: { value: "Quick Run" } });
    // Dev iterations input should be updated to 3
    const devIterInput = screen.getByDisplayValue("3");
    expect(devIterInput).toBeInTheDocument();
  });

  it("deletes a saved preset from localStorage", () => {
    localStorage.setItem(
      "ralph_gui_presets",
      JSON.stringify([
        {
          name: "To Delete",
          developerIterations: 5,
          reviewerPasses: 2,
          agentProfile: null,
        },
      ]),
    );
    renderWizard();
    fireEvent.click(screen.getByTestId("template-feature"));
    // Delete button should be visible for the saved preset
    const deleteBtn = screen.getByTestId("delete-preset-To Delete");
    fireEvent.click(deleteBtn);
    const stored = JSON.parse(localStorage.getItem("ralph_gui_presets") ?? "[]") as unknown[];
    expect(stored).toHaveLength(0);
  });

  it("preset name input validation prevents saving blank names", () => {
    renderWizard();
    fireEvent.click(screen.getByTestId("template-feature"));
    // Save button should be disabled when name is blank
    const saveBtn = screen.getByTestId("save-preset-button");
    expect(saveBtn).toBeDisabled();
    // Enter a whitespace-only name
    const nameInput = screen.getByTestId("preset-name-input");
    fireEvent.change(nameInput, { target: { value: "   " } });
    expect(saveBtn).toBeDisabled();
    // Enter a real name to confirm button enables
    fireEvent.change(nameInput, { target: { value: "Valid Name" } });
    expect(saveBtn).not.toBeDisabled();
  });

  it("launch button is disabled while launch is in progress", async () => {
    // Keep the promise unresolved to simulate the loading state
    let resolvelaunch!: (value: string) => void;
    mockLaunchRalphSession.mockImplementation(
      () => new Promise<string>((resolve) => { resolvelaunch = resolve; }),
    );
    renderWizard();
    fireEvent.click(screen.getByTestId("template-feature"));
    fireEvent.change(screen.getByPlaceholderText("/path/to/your/repo"), {
      target: { value: "/my/repo" },
    });
    fireEvent.click(screen.getByTestId("review-launch-button"));
    await waitFor(() =>
      expect(screen.getByText("Launch session")).toBeInTheDocument(),
    );
    fireEvent.click(screen.getByText("Launch session"));
    await waitFor(() =>
      expect(screen.getByText("Launching…")).toBeInTheDocument(),
    );
    expect(screen.getByText("Launching…")).toBeDisabled();
    // Resolve to clean up
    resolvelaunch("test-run-id");
  });

  it("displays launch error message when launchRalphSession rejects", async () => {
    mockLaunchRalphSession.mockRejectedValueOnce(new Error("Ralph process failed to start"));
    renderWizard();
    fireEvent.click(screen.getByTestId("template-feature"));
    fireEvent.change(screen.getByPlaceholderText("/path/to/your/repo"), {
      target: { value: "/my/repo" },
    });
    fireEvent.click(screen.getByTestId("review-launch-button"));
    await waitFor(() =>
      expect(screen.getByText("Launch session")).toBeInTheDocument(),
    );
    fireEvent.click(screen.getByText("Launch session"));
    await waitFor(() =>
      expect(screen.getByTestId("launch-error")).toBeInTheDocument(),
    );
    expect(screen.getByTestId("launch-error")).toHaveTextContent(
      "Ralph process failed to start",
    );
  });

  it("repo-path input has an accessible label via htmlFor association", () => {
    renderWizard();
    fireEvent.click(screen.getByTestId("template-feature"));
    // getByLabelText verifies the htmlFor/id association is correct
    const repoInput = screen.getByLabelText(/repository path/i);
    expect(repoInput).toBeInTheDocument();
    expect(repoInput).toHaveAttribute("id", "repo-path");
  });

  // --- Inline worktree creation sub-flow tests ---

  it("shows create worktree toggle in config step when worktrees exist", () => {
    renderWizardWithWorktrees();
    fireEvent.click(screen.getByTestId("template-feature"));
    expect(screen.getByTestId("create-worktree-toggle")).toBeInTheDocument();
  });

  it("expanding create worktree toggle shows branch and name inputs", () => {
    renderWizardWithWorktrees();
    fireEvent.click(screen.getByTestId("template-feature"));
    fireEvent.click(screen.getByTestId("create-worktree-toggle"));
    expect(screen.getByTestId("wt-branch-input")).toBeInTheDocument();
    expect(screen.getByTestId("wt-name-input")).toBeInTheDocument();
  });

  it("create worktree form calls createWorktree with branch and name", async () => {
    renderWizardWithWorktrees();
    fireEvent.click(screen.getByTestId("template-feature"));
    fireEvent.click(screen.getByTestId("create-worktree-toggle"));
    fireEvent.change(screen.getByTestId("wt-branch-input"), {
      target: { value: "wt-51-feature" },
    });
    fireEvent.change(screen.getByTestId("wt-name-input"), {
      target: { value: "wt-51-feature" },
    });
    fireEvent.click(screen.getByTestId("wt-create-button"));
    await waitFor(() =>
      expect(mockCreateWorktree).toHaveBeenCalledWith(
        "/my/repo",
        "wt-51-feature",
        "wt-51-feature",
        undefined,
      ),
    );
  });

  it("successful worktree creation collapses the create form", async () => {
    renderWizardWithWorktrees();
    fireEvent.click(screen.getByTestId("template-feature"));
    fireEvent.click(screen.getByTestId("create-worktree-toggle"));
    fireEvent.change(screen.getByTestId("wt-branch-input"), {
      target: { value: "wt-51-feature" },
    });
    fireEvent.change(screen.getByTestId("wt-name-input"), {
      target: { value: "wt-51-feature" },
    });
    fireEvent.click(screen.getByTestId("wt-create-button"));
    await waitFor(() =>
      expect(screen.queryByTestId("wt-branch-input")).not.toBeInTheDocument(),
    );
  });

  it("worktree creation error shows inline error message", async () => {
    mockCreateWorktree.mockRejectedValueOnce(new Error("Branch already exists"));
    renderWizardWithWorktrees();
    fireEvent.click(screen.getByTestId("template-feature"));
    fireEvent.click(screen.getByTestId("create-worktree-toggle"));
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
      "Branch already exists",
    );
  });

  it("blurring branch input autofills name when name is empty", () => {
    renderWizardWithWorktrees();
    fireEvent.click(screen.getByTestId("template-feature"));
    fireEvent.click(screen.getByTestId("create-worktree-toggle"));
    fireEvent.change(screen.getByTestId("wt-branch-input"), {
      target: { value: "wt-51-my-feature" },
    });
    fireEvent.blur(screen.getByTestId("wt-branch-input"));
    expect(screen.getByTestId("wt-name-input")).toHaveValue("wt-51-my-feature");
  });
});
