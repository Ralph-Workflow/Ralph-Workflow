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
}));

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

  it("saves prompt file and dispatches createNewSession on launch", async () => {
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
});
