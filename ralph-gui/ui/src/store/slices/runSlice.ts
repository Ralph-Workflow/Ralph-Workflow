import { createAsyncThunk, createSlice } from "@reduxjs/toolkit";
import { getResumableRuns, getRunDetail } from "../../api/tauri";
import type { RunDetail } from "../../types";

interface RunState {
  runDetail: RunDetail | null;
  resumableRuns: RunDetail[];
  status: "idle" | "loading" | "succeeded" | "failed";
  error: string | null;
}

const initialState: RunState = {
  runDetail: null,
  resumableRuns: [],
  status: "idle",
  error: null,
};

export const fetchRunDetail = createAsyncThunk(
  "runs/fetchRunDetail",
  async (runId: string) => {
    return getRunDetail(runId);
  },
);

export const fetchResumableRuns = createAsyncThunk(
  "runs/fetchResumableRuns",
  async (repoPath: string) => {
    return getResumableRuns(repoPath);
  },
);

const runSlice = createSlice({
  name: "runs",
  initialState,
  reducers: {
    clearRunDetail: (state) => {
      state.runDetail = null;
    },
  },
  extraReducers: (builder) => {
    builder
      .addCase(fetchRunDetail.pending, (state) => {
        state.status = "loading";
        state.error = null;
      })
      .addCase(fetchRunDetail.fulfilled, (state, action) => {
        state.status = "succeeded";
        state.runDetail = action.payload;
      })
      .addCase(fetchRunDetail.rejected, (state, action) => {
        state.status = "failed";
        state.error = action.error.message ?? "Unknown error";
      })
      .addCase(fetchResumableRuns.fulfilled, (state, action) => {
        state.resumableRuns = action.payload;
      });
  },
});

export const { clearRunDetail } = runSlice.actions;
export default runSlice.reducer;
