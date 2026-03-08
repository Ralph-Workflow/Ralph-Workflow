import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { configureStore } from "@reduxjs/toolkit";
import { Provider } from "react-redux";
import { MemoryRouter } from "react-router-dom";
import worktreeReducer from "../store/slices/worktreeSlice";
import sessionReducer from "../store/slices/sessionSlice";
import runReducer from "../store/slices/runSlice";
import configReducer from "../store/slices/configSlice";
import { Layout } from "./Layout";

vi.mock("../api/tauri", () => ({
  listWorktrees: vi.fn().mockResolvedValue([]),
  switchContext: vi.fn().mockResolvedValue(undefined),
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

function renderLayout(children = <div data-testid="child">Content</div>) {
  return render(
    <Provider store={makeStore()}>
      <MemoryRouter>
        <Layout>{children}</Layout>
      </MemoryRouter>
    </Provider>,
  );
}

describe("Layout", () => {
  it("renders children inside main content area", () => {
    renderLayout();
    expect(screen.getByTestId("child")).toBeInTheDocument();
    expect(screen.getByText("Content")).toBeInTheDocument();
  });

  it("renders the Ralph brand name in the sidebar", () => {
    renderLayout();
    expect(screen.getByText("Ralph")).toBeInTheDocument();
  });

  it("renders the workflow label", () => {
    renderLayout();
    expect(screen.getByText("workflow")).toBeInTheDocument();
  });
});
