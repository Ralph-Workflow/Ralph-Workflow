import { describe, it, expect, vi, beforeEach } from "vitest";
import { configureStore } from "@reduxjs/toolkit";
import sessionReducer, {
  fetchSessions,
  createNewSession,
  setActiveSession,
  resumeInterruptedSession,
} from "./sessionSlice";
import type { SessionSummary } from "../../types";

vi.mock("../../api/tauri", () => ({
  getSessions: vi.fn(),
  createSession: vi.fn(),
  resumeSession: vi.fn(),
  resumeRalphSession: vi.fn(),
  getSessionDetail: vi.fn(),
}));

import {
  getSessions,
  createSession,
  resumeRalphSession,
  getSessionDetail,
} from "../../api/tauri";
import type { Mock } from "vitest";

const mockGetSessions = getSessions as Mock;
const mockCreateSession = createSession as Mock;
const mockResumeRalphSession = resumeRalphSession as Mock;
const mockGetSessionDetail = getSessionDetail as Mock;

function makeStore() {
  return configureStore({
    reducer: { sessions: sessionReducer },
  });
}

const mockSession: SessionSummary = {
  run_id: "run-abc-123",
  status: "paused",
  repo_path: "/my/repo",
  worktree_path: null,
  created_at: "2024-01-01 12:00:00",
  description: "Test session",
  developer_agent: "claude",
  reviewer_agent: "codex",
  phase: "Development",
};

beforeEach(() => {
  vi.clearAllMocks();
});

describe("sessionSlice", () => {
  it("initial state has empty sessions array and status idle", () => {
    const store = makeStore();
    const state = store.getState().sessions;
    expect(state.sessions).toEqual([]);
    expect(state.status).toBe("idle");
    expect(state.selectedRunId).toBeNull();
    expect(state.error).toBeNull();
  });

  it("fetchSessions.pending sets status to loading", () => {
    mockGetSessions.mockReturnValue(new Promise(() => undefined));
    const store = makeStore();
    void store.dispatch(fetchSessions("/my/repo"));
    expect(store.getState().sessions.status).toBe("loading");
  });

  it("fetchSessions.fulfilled populates sessions and sets status succeeded", async () => {
    mockGetSessions.mockResolvedValueOnce([mockSession]);
    const store = makeStore();
    await store.dispatch(fetchSessions("/my/repo"));
    const state = store.getState().sessions;
    expect(state.status).toBe("succeeded");
    expect(state.sessions).toHaveLength(1);
    expect(state.sessions[0]?.run_id).toBe("run-abc-123");
  });

  it("fetchSessions.rejected sets status failed with error message", async () => {
    mockGetSessions.mockRejectedValueOnce(new Error("Repository not found"));
    const store = makeStore();
    await store.dispatch(fetchSessions("/my/repo"));
    const state = store.getState().sessions;
    expect(state.status).toBe("failed");
    expect(state.error).toBe("Repository not found");
  });

  it("createNewSession.fulfilled adds new session to sessions array", async () => {
    mockCreateSession.mockResolvedValueOnce(mockSession);
    const store = makeStore();
    await store.dispatch(
      createNewSession({
        repo_path: "/my/repo",
        worktree_path: null,
        prompt_path: "/my/repo/PROMPT.md",
        developer_iterations: 3,
        reviewer_passes: 2,
      }),
    );
    const state = store.getState().sessions;
    expect(state.sessions).toHaveLength(1);
    expect(state.sessions[0]?.run_id).toBe("run-abc-123");
  });

  it("setActiveSession action updates selectedRunId", () => {
    const store = makeStore();
    store.dispatch(setActiveSession("run-abc-123"));
    expect(store.getState().sessions.selectedRunId).toBe("run-abc-123");
  });

  it("setActiveSession with null clears selectedRunId", () => {
    const store = makeStore();
    store.dispatch(setActiveSession("run-abc-123"));
    store.dispatch(setActiveSession(null));
    expect(store.getState().sessions.selectedRunId).toBeNull();
  });

  it("clearSessionError clears the error field", async () => {
    const { clearSessionError } = await import("./sessionSlice");
    mockGetSessions.mockRejectedValueOnce(new Error("Some error"));
    const store = makeStore();
    await store.dispatch(fetchSessions("/my/repo"));
    expect(store.getState().sessions.error).toBe("Some error");
    store.dispatch(clearSessionError());
    expect(store.getState().sessions.error).toBeNull();
  });

  it("resumeInterruptedSession calls resumeRalphSession then updates session", async () => {
    const updatedSession = { ...mockSession, status: "running" as const };
    mockResumeRalphSession.mockResolvedValueOnce(undefined);
    mockGetSessionDetail.mockResolvedValueOnce(updatedSession);
    const store = configureStore({
      reducer: { sessions: sessionReducer },
      preloadedState: {
        sessions: {
          sessions: [mockSession],
          status: "succeeded" as const,
          error: null,
          selectedRunId: null,
        },
      },
    });
    await store.dispatch(
      resumeInterruptedSession({ runId: "run-abc-123", repoPath: "/my/repo" }),
    );
    expect(store.getState().sessions.sessions[0]?.status).toBe("running");
  });
});
