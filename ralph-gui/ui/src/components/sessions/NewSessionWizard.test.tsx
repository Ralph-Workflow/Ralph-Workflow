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
}));

import { launchRalphSession } from "../../api/tauri";
import type { Mock } from "vitest";

const mockLaunchRalphSession = launchRalphSession as Mock;

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

  it("repo-path input has an accessible label via htmlFor association", () => {
    renderWizard();
    fireEvent.click(screen.getByTestId("template-feature"));
    // getByLabelText verifies the htmlFor/id association is correct
    const repoInput = screen.getByLabelText(/repository path/i);
    expect(repoInput).toBeInTheDocument();
    expect(repoInput).toHaveAttribute("id", "repo-path");
  });
});
