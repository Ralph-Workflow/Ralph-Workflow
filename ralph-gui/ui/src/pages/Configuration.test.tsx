import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { configureStore } from "@reduxjs/toolkit";
import { Provider } from "react-redux";
import { MemoryRouter } from "react-router-dom";
import configReducer from "../store/slices/configSlice";
import worktreeReducer from "../store/slices/worktreeSlice";
import sessionReducer from "../store/slices/sessionSlice";
import runReducer from "../store/slices/runSlice";
import { Configuration } from "./Configuration";

vi.mock("../api/tauri", () => ({
  getGlobalConfig: vi.fn().mockResolvedValue({
    verbosity: 1,
    developer_iters: 3,
    reviewer_reviews: 2,
    checkpoint_enabled: true,
    isolation_mode: false,
    interactive: false,
    review_depth: "standard",
    max_dev_continuations: 2,
  }),
  getProjectConfig: vi.fn().mockResolvedValue(null),
  getEffectiveConfig: vi.fn().mockResolvedValue({
    verbosity: 1,
    developer_iters: 3,
    reviewer_reviews: 2,
    checkpoint_enabled: true,
    isolation_mode: false,
    interactive: false,
    review_depth: "standard",
    max_dev_continuations: 2,
  }),
  saveGlobalConfig: vi.fn().mockResolvedValue(undefined),
  saveProjectConfig: vi.fn().mockResolvedValue(undefined),
  getRawGlobalConfigToml: vi.fn().mockResolvedValue(""),
  getRawProjectConfigToml: vi.fn().mockResolvedValue(""),
  getAiApiKey: vi.fn().mockResolvedValue(""),
  saveAiApiKey: vi.fn().mockResolvedValue(undefined),
  validateConfigToml: vi.fn().mockResolvedValue(null),
}));

import {
  saveGlobalConfig,
  getRawGlobalConfigToml,
  getRawProjectConfigToml,
  getAiApiKey,
  saveAiApiKey,
  validateConfigToml,
} from "../api/tauri";
import type { Mock } from "vitest";

const mockSaveGlobalConfig = saveGlobalConfig as Mock;
const mockGetRawGlobalConfigToml = getRawGlobalConfigToml as Mock;
const mockGetRawProjectConfigToml = getRawProjectConfigToml as Mock;
const mockGetAiApiKey = getAiApiKey as Mock;
const mockSaveAiApiKey = saveAiApiKey as Mock;
const mockValidateConfigToml = validateConfigToml as Mock;

function makeStore(preloaded?: object) {
  return configureStore({
    reducer: {
      config: configReducer,
      worktrees: worktreeReducer,
      sessions: sessionReducer,
      runs: runReducer,
    },
    preloadedState: preloaded,
  });
}

function renderConfig(store = makeStore()) {
  return render(
    <Provider store={store}>
      <MemoryRouter>
        <Configuration />
      </MemoryRouter>
    </Provider>,
  );
}

const mainWorktree = {
  path: "/my/repo",
  branch: "main",
  name: "main",
  has_active_run: false,
  is_main: true,
};

beforeEach(() => {
  vi.clearAllMocks();
});

describe("Configuration", () => {
  it("renders three tabs: Effective, Global, Project", () => {
    renderConfig();
    expect(screen.getByText("Effective")).toBeInTheDocument();
    expect(screen.getByText("Global")).toBeInTheDocument();
    expect(screen.getByText("Project")).toBeInTheDocument();
  });

  it("shows read-only config table on Effective tab by default", async () => {
    renderConfig();
    await waitFor(() =>
      expect(screen.getByText("verbosity")).toBeInTheDocument(),
    );
  });

  it("shows TOML editor on Global tab", () => {
    renderConfig();
    fireEvent.click(screen.getByText("Global"));
    const textareas = document.querySelectorAll("textarea");
    expect(textareas.length).toBeGreaterThanOrEqual(1);
  });

  it("shows TOML editor on Project tab", () => {
    renderConfig();
    fireEvent.click(screen.getByText("Project"));
    const textareas = document.querySelectorAll("textarea");
    expect(textareas.length).toBeGreaterThanOrEqual(1);
  });

  it("Global tab shows scope label mentioning global config path", () => {
    renderConfig();
    fireEvent.click(screen.getByText("Global"));
    expect(
      screen.getByText(/~\/.config\/ralph-workflow\.toml/i),
    ).toBeInTheDocument();
  });

  it("Project tab shows warning when no repo path is set (scope mistake prevention)", () => {
    renderConfig();
    fireEvent.click(screen.getByText("Project"));
    expect(
      screen.getByText(/Select a repository context to edit project config/i),
    ).toBeInTheDocument();
  });

  it("Project tab shows correct path label when repo is set", () => {
    const store = makeStore({
      worktrees: {
        worktrees: [mainWorktree],
        status: "succeeded",
        error: null,
        activeWorktreePath: null,
        lastRepoPath: "/my/repo",
      },
    });
    renderConfig(store);
    fireEvent.click(screen.getByText("Project"));
    expect(screen.getByText(/\/my\/repo\/.ralph\/config\.toml/)).toBeInTheDocument();
  });

  it("Save button in Project tab is disabled when no repo path is set", () => {
    renderConfig();
    fireEvent.click(screen.getByText("Project"));
    const saveBtn = screen.getByText("Save");
    expect(saveBtn).toBeDisabled();
  });

  it("Save button in Global tab calls saveGlobalConfig", async () => {
    renderConfig();
    fireEvent.click(screen.getByText("Global"));
    const saveBtn = screen.getByText("Save");
    fireEvent.click(saveBtn);
    await waitFor(() => {
      expect(mockSaveGlobalConfig).toHaveBeenCalled();
    });
  });

  it("shows error message from backend when saveGlobalConfig rejects", async () => {
    mockSaveGlobalConfig.mockRejectedValueOnce(new Error("Invalid TOML syntax"));
    renderConfig();
    fireEvent.click(screen.getByText("Global"));
    const saveBtn = screen.getByText("Save");
    fireEvent.click(saveBtn);
    await waitFor(() => {
      expect(screen.getByText("Invalid TOML syntax")).toBeInTheDocument();
    });
  });

  it("Global tab preloads existing TOML content in the textarea", async () => {
    mockGetRawGlobalConfigToml.mockResolvedValueOnce("[general]\nverbosity = 2\n");
    renderConfig();
    fireEvent.click(screen.getByText("Global"));
    await waitFor(() => {
      const textarea = document.querySelector("textarea");
      expect(textarea).not.toBeNull();
      expect(textarea?.value).toContain("verbosity = 2");
    });
  });

  it("Project tab preloads existing TOML content when repo path is set", async () => {
    mockGetRawProjectConfigToml.mockResolvedValueOnce("[general]\ndeveloper_iters = 5\n");
    const store = makeStore({
      worktrees: {
        worktrees: [mainWorktree],
        status: "succeeded",
        error: null,
        activeWorktreePath: null,
        lastRepoPath: "/my/repo",
      },
    });
    renderConfig(store);
    fireEvent.click(screen.getByText("Project"));
    await waitFor(() => {
      const textarea = document.querySelector("textarea");
      expect(textarea).not.toBeNull();
      expect(textarea?.value).toContain("developer_iters = 5");
    });
  });

  // --- AI Settings section tests ---

  it("Global tab shows AI Integration section heading", async () => {
    renderConfig();
    fireEvent.click(screen.getByText("Global"));
    await waitFor(() => {
      expect(screen.getByText("AI Integration")).toBeInTheDocument();
    });
  });

  it("Global tab renders AI API key input field", async () => {
    renderConfig();
    fireEvent.click(screen.getByText("Global"));
    await waitFor(() => {
      expect(screen.getByPlaceholderText(/enter openai api key/i)).toBeInTheDocument();
    });
  });

  it("AI API key input is masked (type=password) by default", async () => {
    renderConfig();
    fireEvent.click(screen.getByText("Global"));
    await waitFor(() => {
      const input = screen.getByPlaceholderText(/enter openai api key/i);
      expect(input).toHaveAttribute("type", "password");
    });
  });

  it("show/hide toggle reveals and masks the API key", async () => {
    renderConfig();
    fireEvent.click(screen.getByText("Global"));
    await waitFor(() => {
      const input = screen.getByPlaceholderText(/enter openai api key/i);
      expect(input).toHaveAttribute("type", "password");
    });
    const toggleBtn = screen.getByRole("button", { name: /show/i });
    fireEvent.click(toggleBtn);
    const input = screen.getByPlaceholderText(/enter openai api key/i);
    expect(input).toHaveAttribute("type", "text");
    fireEvent.click(screen.getByRole("button", { name: /hide/i }));
    expect(input).toHaveAttribute("type", "password");
  });

  it("fetchAiApiKey is called on mount when Global tab opens", async () => {
    renderConfig();
    fireEvent.click(screen.getByText("Global"));
    await waitFor(() => {
      expect(mockGetAiApiKey).toHaveBeenCalled();
    });
  });

  it("pre-populates AI key input with saved key from backend", async () => {
    mockGetAiApiKey.mockResolvedValueOnce("sk-existing-key");
    renderConfig();
    fireEvent.click(screen.getByText("Global"));
    await waitFor(() => {
      const input = screen.getByPlaceholderText(/enter openai api key/i);
      expect((input as HTMLInputElement).value).toBe("sk-existing-key");
    });
  });

  it("Save API Key button calls saveAiApiKey with the entered value", async () => {
    renderConfig();
    fireEvent.click(screen.getByText("Global"));
    await waitFor(() => {
      expect(screen.getByPlaceholderText(/enter openai api key/i)).toBeInTheDocument();
    });
    const input = screen.getByPlaceholderText(/enter openai api key/i);
    fireEvent.change(input, { target: { value: "sk-my-new-key" } });
    const saveKeyBtn = screen.getByRole("button", { name: /save api key/i });
    fireEvent.click(saveKeyBtn);
    await waitFor(() => {
      expect(mockSaveAiApiKey).toHaveBeenCalledWith("sk-my-new-key");
    });
  });

  it("shows success feedback after saving AI API key", async () => {
    mockSaveAiApiKey.mockResolvedValueOnce(undefined);
    renderConfig();
    fireEvent.click(screen.getByText("Global"));
    await waitFor(() => {
      expect(screen.getByPlaceholderText(/enter openai api key/i)).toBeInTheDocument();
    });
    const input = screen.getByPlaceholderText(/enter openai api key/i);
    fireEvent.change(input, { target: { value: "sk-key" } });
    const saveKeyBtn = screen.getByRole("button", { name: /save api key/i });
    fireEvent.click(saveKeyBtn);
    await waitFor(() => {
      expect(screen.getByText(/saved/i)).toBeInTheDocument();
    });
  });

  it("shows error feedback when saveAiApiKey backend rejects", async () => {
    mockSaveAiApiKey.mockRejectedValueOnce(new Error("API key must not be empty"));
    renderConfig();
    fireEvent.click(screen.getByText("Global"));
    await waitFor(() => {
      expect(screen.getByPlaceholderText(/enter openai api key/i)).toBeInTheDocument();
    });
    const saveKeyBtn = screen.getByRole("button", { name: /save api key/i });
    fireEvent.click(saveKeyBtn);
    await waitFor(() => {
      expect(screen.getByText(/API key must not be empty/i)).toBeInTheDocument();
    });
  });

  // --- TOML validation tests ---

  it("Save button in Global tab is enabled when validateConfigToml returns null (valid)", async () => {
    mockValidateConfigToml.mockResolvedValue(null);
    renderConfig();
    fireEvent.click(screen.getByText("Global"));
    await waitFor(() => {
      const saveBtn = screen.getByText("Save");
      expect(saveBtn).not.toBeDisabled();
    });
  });

  it("Save button in Global tab is disabled when validateConfigToml returns an error string", async () => {
    mockValidateConfigToml.mockResolvedValue("TOML parse error: unclosed bracket");
    renderConfig();
    fireEvent.click(screen.getByText("Global"));
    // Trigger validation by changing textarea content
    await waitFor(() => {
      expect(document.querySelector("textarea")).not.toBeNull();
    });
    const textarea = document.querySelector("textarea") as HTMLTextAreaElement;
    fireEvent.change(textarea, { target: { value: "[unclosed" } });
    await waitFor(() => {
      expect(screen.getByText(/TOML parse error/i)).toBeInTheDocument();
    });
    const saveBtn = screen.getByText("Save");
    expect(saveBtn).toBeDisabled();
  });

  it("inline validation error disappears when TOML is corrected", async () => {
    mockValidateConfigToml
      .mockResolvedValueOnce("Parse error: bad syntax")
      .mockResolvedValue(null);
    renderConfig();
    fireEvent.click(screen.getByText("Global"));
    await waitFor(() => {
      expect(document.querySelector("textarea")).not.toBeNull();
    });
    const textarea = document.querySelector("textarea") as HTMLTextAreaElement;
    // Enter bad TOML
    fireEvent.change(textarea, { target: { value: "[bad" } });
    await waitFor(() => {
      expect(screen.getByText(/Parse error/i)).toBeInTheDocument();
    });
    // Fix the TOML
    fireEvent.change(textarea, { target: { value: "[general]\nverbosity = 1\n" } });
    await waitFor(() => {
      expect(screen.queryByText(/Parse error/i)).not.toBeInTheDocument();
    });
    const saveBtn = screen.getByText("Save");
    expect(saveBtn).not.toBeDisabled();
  });

  it("scope badge is visible next to Save button in Global tab", () => {
    renderConfig();
    fireEvent.click(screen.getByText("Global"));
    // There should be a scope badge with "global" or "Global" label near the save button
    expect(screen.getByTestId("scope-badge")).toBeInTheDocument();
  });

  it("scope badge shows 'project' in Project tab", () => {
    const store = makeStore({
      worktrees: {
        worktrees: [mainWorktree],
        status: "succeeded",
        error: null,
        activeWorktreePath: null,
        lastRepoPath: "/my/repo",
      },
    });
    renderConfig(store);
    fireEvent.click(screen.getByText("Project"));
    expect(screen.getByTestId("scope-badge")).toBeInTheDocument();
    expect(screen.getByTestId("scope-badge").textContent?.toLowerCase()).toContain("project");
  });

  // --- Revert button tests ---

  it("shows Revert button in Global tab editor", () => {
    renderConfig();
    fireEvent.click(screen.getByText("Global"));
    expect(screen.getByTestId("revert-config-button")).toBeInTheDocument();
  });

  it("Revert button reloads content from backend and clears dirty flag", async () => {
    mockGetRawGlobalConfigToml.mockResolvedValue("original-content");
    renderConfig();
    fireEvent.click(screen.getByText("Global"));
    // Wait for initial load
    await waitFor(() => {
      const textarea = document.querySelector("textarea");
      expect(textarea?.value).toContain("original-content");
    });
    // Simulate user editing
    const textarea = document.querySelector("textarea") as HTMLTextAreaElement;
    fireEvent.change(textarea, { target: { value: "modified-content" } });
    expect(textarea.value).toContain("modified-content");
    // Click Revert
    const revertBtn = screen.getByTestId("revert-config-button");
    fireEvent.click(revertBtn);
    // Content should revert to the saved value
    await waitFor(() => {
      expect(textarea.value).toContain("original-content");
    });
  });
});
