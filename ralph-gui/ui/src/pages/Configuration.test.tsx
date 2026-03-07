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
}));

function makeStore() {
  return configureStore({
    reducer: {
      config: configReducer,
      worktrees: worktreeReducer,
      sessions: sessionReducer,
      runs: runReducer,
    },
  });
}

function renderConfig() {
  return render(
    <Provider store={makeStore()}>
      <MemoryRouter>
        <Configuration />
      </MemoryRouter>
    </Provider>,
  );
}

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
});
