import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { configureStore } from "@reduxjs/toolkit";
import { Provider } from "react-redux";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import runReducer from "../store/slices/runSlice";
import sessionReducer from "../store/slices/sessionSlice";
import worktreeReducer from "../store/slices/worktreeSlice";
import configReducer from "../store/slices/configSlice";
import { RunDetail } from "./RunDetail";

vi.mock("../api/tauri", () => ({
  getRunDetail: vi.fn(),
  getRunStatus: vi.fn(),
  resumeRalphSession: vi.fn(),
  getSessionDetail: vi.fn(),
  resumeSession: vi.fn(),
}));

import { getRunDetail } from "../api/tauri";
import type { Mock } from "vitest";

const mockGetRunDetail = getRunDetail as Mock;

const mockRun = {
  run_id: "run-detail-123",
  status: "Paused" as const,
  current_phase: "Review",
  last_checkpoint: "2024-01-01 12:00:00",
  agent_profile: "claude/codex",
  repo_path: "/my/repo",
  worktree_path: null,
  created_at: "2024-01-01 10:00:00",
  description: "Test run",
};

function makeStore() {
  return configureStore({
    reducer: {
      runs: runReducer,
      sessions: sessionReducer,
      worktrees: worktreeReducer,
      config: configReducer,
    },
  });
}

function renderDetail(runId = "run-detail-123") {
  return render(
    <Provider store={makeStore()}>
      <MemoryRouter initialEntries={[`/runs/${runId}`]}>
        <Routes>
          <Route path="/runs/:runId" element={<RunDetail />} />
        </Routes>
      </MemoryRouter>
    </Provider>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
});

describe("RunDetail", () => {
  it("shows loading state initially", () => {
    mockGetRunDetail.mockReturnValueOnce(new Promise(() => undefined));
    renderDetail();
    expect(screen.getByText(/Loading run/)).toBeInTheDocument();
  });

  it("renders run ID and phase after load", async () => {
    mockGetRunDetail.mockResolvedValueOnce(mockRun);
    renderDetail();
    await waitFor(() =>
      expect(screen.getByText("run-detail-123")).toBeInTheDocument(),
    );
    expect(screen.getByText("Review")).toBeInTheDocument();
  });

  it("shows Resume button for Paused run", async () => {
    mockGetRunDetail.mockResolvedValueOnce(mockRun);
    renderDetail();
    await waitFor(() =>
      expect(screen.getByText("Resume")).toBeInTheDocument(),
    );
  });

  it("does not show Resume for Completed run", async () => {
    mockGetRunDetail.mockResolvedValueOnce({
      ...mockRun,
      status: "Completed" as const,
    });
    renderDetail();
    await waitFor(() =>
      expect(screen.queryByText("Resume")).not.toBeInTheDocument(),
    );
  });

  it("shows error state when run is not found", async () => {
    mockGetRunDetail.mockRejectedValueOnce(new Error("Run not found"));
    renderDetail("nonexistent");
    await waitFor(() => {
      const matches = screen.getAllByText("Run not found");
      expect(matches.length).toBeGreaterThan(0);
    });
  });
});
