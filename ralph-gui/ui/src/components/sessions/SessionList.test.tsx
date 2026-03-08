import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { configureStore } from "@reduxjs/toolkit";
import { Provider } from "react-redux";
import sessionReducer from "../../store/slices/sessionSlice";
import worktreeReducer from "../../store/slices/worktreeSlice";
import runReducer from "../../store/slices/runSlice";
import configReducer from "../../store/slices/configSlice";
import { SessionList } from "./SessionList";

vi.mock("../../api/tauri", () => ({
  getSessions: vi.fn(),
  resumeSession: vi.fn(),
  resumeRalphSession: vi.fn(),
  getSessionDetail: vi.fn(),
}));

import { getSessions } from "../../api/tauri";
import type { Mock } from "vitest";

const mockGetSessions = getSessions as Mock;

function makeStore() {
  return configureStore({
    reducer: {
      sessions: sessionReducer,
      worktrees: worktreeReducer,
      runs: runReducer,
      config: configReducer,
    },
  });
}

function renderList(repoPath = "/my/repo", onResume = vi.fn()) {
  return render(
    <Provider store={makeStore()}>
      <SessionList repoPath={repoPath} onResume={onResume} />
    </Provider>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
});

describe("SessionList", () => {
  it("shows empty state when no sessions exist", async () => {
    mockGetSessions.mockResolvedValueOnce([]);
    renderList();
    await waitFor(() =>
      expect(screen.getByText("No sessions yet")).toBeInTheDocument(),
    );
  });

  it("shows loading state while fetching", () => {
    mockGetSessions.mockReturnValueOnce(new Promise(() => undefined));
    renderList();
    expect(screen.getByText(/Loading sessions/)).toBeInTheDocument();
  });

  it("renders session rows when sessions exist", async () => {
    mockGetSessions.mockResolvedValueOnce([
      {
        run_id: "abc-123",
        status: "paused",
        repo_path: "/my/repo",
        worktree_path: null,
        created_at: "2024-01-01",
        description: "Test session",
        developer_agent: "claude",
        reviewer_agent: "codex",
        phase: "Review",
      },
    ]);
    renderList();
    await waitFor(() =>
      expect(screen.getByText(/abc-123/)).toBeInTheDocument(),
    );
  });

  it("shows Resume button for paused sessions", async () => {
    mockGetSessions.mockResolvedValueOnce([
      {
        run_id: "abc-123",
        status: "paused",
        repo_path: "/my/repo",
        worktree_path: null,
        created_at: "2024-01-01",
        description: "Paused",
        developer_agent: "",
        reviewer_agent: "",
        phase: "Review",
      },
    ]);
    const onResume = vi.fn();
    renderList("/my/repo", onResume);
    await waitFor(() =>
      expect(screen.getByText("Resume")).toBeInTheDocument(),
    );
  });

  it("does not show Resume button for completed sessions", async () => {
    mockGetSessions.mockResolvedValueOnce([
      {
        run_id: "abc-456",
        status: "completed",
        repo_path: "/my/repo",
        worktree_path: null,
        created_at: "2024-01-01",
        description: "Done",
        developer_agent: "",
        reviewer_agent: "",
        phase: "Complete",
      },
    ]);
    renderList();
    await waitFor(() =>
      expect(screen.queryByText("Resume")).not.toBeInTheDocument(),
    );
  });

  it("shows empty filter message when filterStatus excludes all sessions", async () => {
    mockGetSessions.mockResolvedValueOnce([
      {
        run_id: "abc-123",
        status: "completed",
        repo_path: "/my/repo",
        worktree_path: null,
        created_at: "2024-01-01",
        description: "Done",
        developer_agent: "",
        reviewer_agent: "",
        phase: "Complete",
      },
    ]);
    render(
      <Provider store={makeStore()}>
        <SessionList
          repoPath="/my/repo"
          filterStatus={["running"]}
        />
      </Provider>,
    );
    await waitFor(() =>
      expect(screen.getByText(/No sessions match/)).toBeInTheDocument(),
    );
  });

  it("filters sessions by status, showing only matching rows", async () => {
    mockGetSessions.mockResolvedValueOnce([
      {
        run_id: "run-paused",
        status: "paused",
        repo_path: "/my/repo",
        worktree_path: null,
        created_at: "2024-01-01",
        description: "Paused run",
        developer_agent: "",
        reviewer_agent: "",
        phase: "Review",
      },
      {
        run_id: "run-completed",
        status: "completed",
        repo_path: "/my/repo",
        worktree_path: null,
        created_at: "2024-01-01",
        description: "Done run",
        developer_agent: "",
        reviewer_agent: "",
        phase: "Complete",
      },
    ]);
    render(
      <Provider store={makeStore()}>
        <SessionList
          repoPath="/my/repo"
          filterStatus={["paused"]}
        />
      </Provider>,
    );
    await waitFor(() =>
      expect(screen.getByText(/run-paused/)).toBeInTheDocument(),
    );
    expect(screen.queryByText(/run-completed/)).not.toBeInTheDocument();
  });

  it("shows all sessions when filterStatus is empty (no filter applied)", async () => {
    mockGetSessions.mockResolvedValueOnce([
      {
        run_id: "run-paused",
        status: "paused",
        repo_path: "/my/repo",
        worktree_path: null,
        created_at: "2024-01-01",
        description: "Paused run",
        developer_agent: "",
        reviewer_agent: "",
        phase: "Review",
      },
      {
        run_id: "run-completed",
        status: "completed",
        repo_path: "/my/repo",
        worktree_path: null,
        created_at: "2024-01-01",
        description: "Done run",
        developer_agent: "",
        reviewer_agent: "",
        phase: "Complete",
      },
    ]);
    render(
      <Provider store={makeStore()}>
        <SessionList
          repoPath="/my/repo"
          filterStatus={[]}
        />
      </Provider>,
    );
    await waitFor(() =>
      expect(screen.getByText(/run-paused/)).toBeInTheDocument(),
    );
    expect(screen.getByText(/run-completed/)).toBeInTheDocument();
  });

  it("filters sessions by worktree path", async () => {
    mockGetSessions.mockResolvedValueOnce([
      {
        run_id: "wt-run",
        status: "running",
        repo_path: "/my/repo",
        worktree_path: "/my/wt-50",
        created_at: "2024-01-01",
        description: "Worktree run",
        developer_agent: "",
        reviewer_agent: "",
        phase: "Development",
      },
      {
        run_id: "direct-run",
        status: "running",
        repo_path: "/my/repo",
        worktree_path: null,
        created_at: "2024-01-01",
        description: "Direct run",
        developer_agent: "",
        reviewer_agent: "",
        phase: "Development",
      },
    ]);
    render(
      <Provider store={makeStore()}>
        <SessionList
          repoPath="/my/repo"
          filterWorktreePath="/my/wt-50"
        />
      </Provider>,
    );
    await waitFor(() =>
      expect(screen.getByText(/wt-run/)).toBeInTheDocument(),
    );
    expect(screen.queryByText(/direct-run/)).not.toBeInTheDocument();
  });
});
