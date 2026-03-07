import {
  createAsyncThunk,
  createSlice,
  type PayloadAction,
} from "@reduxjs/toolkit";
import { getSessions, createSession, resumeSession } from "../../api/tauri";
import type { CreateSessionRequest, SessionSummary } from "../../types";

export type LoadingStatus = "idle" | "loading" | "succeeded" | "failed";

interface SessionState {
  sessions: SessionSummary[];
  status: LoadingStatus;
  error: string | null;
  selectedRunId: string | null;
}

const initialState: SessionState = {
  sessions: [],
  status: "idle",
  error: null,
  selectedRunId: null,
};

export const fetchSessions = createAsyncThunk(
  "sessions/fetchSessions",
  async (repoPath: string) => {
    return getSessions(repoPath);
  },
);

export const createNewSession = createAsyncThunk(
  "sessions/createNewSession",
  async (request: CreateSessionRequest) => {
    return createSession(request);
  },
);

export const resumeInterruptedSession = createAsyncThunk(
  "sessions/resumeSession",
  async (runId: string) => {
    return resumeSession(runId);
  },
);

const sessionSlice = createSlice({
  name: "sessions",
  initialState,
  reducers: {
    setActiveSession: (state, action: PayloadAction<string | null>) => {
      state.selectedRunId = action.payload;
    },
    clearSessionError: (state) => {
      state.error = null;
    },
  },
  extraReducers: (builder) => {
    builder
      .addCase(fetchSessions.pending, (state) => {
        state.status = "loading";
        state.error = null;
      })
      .addCase(fetchSessions.fulfilled, (state, action) => {
        state.status = "succeeded";
        state.sessions = action.payload;
      })
      .addCase(fetchSessions.rejected, (state, action) => {
        state.status = "failed";
        state.error = action.error.message ?? "Unknown error";
      })
      .addCase(createNewSession.fulfilled, (state, action) => {
        state.sessions.push(action.payload);
      })
      .addCase(resumeInterruptedSession.fulfilled, (state, action) => {
        const index = state.sessions.findIndex(
          (s) => s.run_id === action.payload.run_id,
        );
        if (index !== -1) {
          state.sessions[index] = action.payload;
        }
      });
  },
});

export const { setActiveSession, clearSessionError } = sessionSlice.actions;
export default sessionSlice.reducer;
