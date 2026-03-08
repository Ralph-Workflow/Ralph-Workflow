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
}));

import { saveGlobalConfig, getRawGlobalConfigToml, getRawProjectConfigToml } from "../api/tauri";
import type { Mock } from "vitest";

const mockSaveGlobalConfig = saveGlobalConfig as Mock;
const mockGetRawGlobalConfigToml = getRawGlobalConfigToml as Mock;
const mockGetRawProjectConfigToml = getRawProjectConfigToml as Mock;

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
});
