import {
  createAsyncThunk,
  createSlice,
  type PayloadAction,
} from "@reduxjs/toolkit";
import { listWorktrees, createWorktree, switchContext } from "../../api/tauri";
import type { WorktreeInfo } from "../../types";

interface WorktreeState {
  worktrees: WorktreeInfo[];
  status: "idle" | "loading" | "succeeded" | "failed";
  error: string | null;
  activeWorktreePath: string | null;
}

const initialState: WorktreeState = {
  worktrees: [],
  status: "idle",
  error: null,
  activeWorktreePath: null,
};

export const fetchWorktrees = createAsyncThunk(
  "worktrees/fetchWorktrees",
  async (repoPath: string) => {
    return listWorktrees(repoPath);
  },
);

export const createNewWorktree = createAsyncThunk(
  "worktrees/createWorktree",
  async (args: {
    repoPath: string;
    branch: string;
    name: string;
    basePath?: string;
  }) => {
    const result = await createWorktree(
      args.repoPath,
      args.branch,
      args.name,
      args.basePath,
    );
    return result.worktree;
  },
);

export const switchActiveContext = createAsyncThunk(
  "worktrees/switchContext",
  async (args: { repoPath: string; worktreePath: string | null }) => {
    await switchContext(args.repoPath, args.worktreePath);
    return args.worktreePath;
  },
);

const worktreeSlice = createSlice({
  name: "worktrees",
  initialState,
  reducers: {
    setActiveWorktree: (state, action: PayloadAction<string | null>) => {
      state.activeWorktreePath = action.payload;
    },
  },
  extraReducers: (builder) => {
    builder
      .addCase(fetchWorktrees.pending, (state) => {
        state.status = "loading";
        state.error = null;
      })
      .addCase(fetchWorktrees.fulfilled, (state, action) => {
        state.status = "succeeded";
        state.worktrees = action.payload;
      })
      .addCase(fetchWorktrees.rejected, (state, action) => {
        state.status = "failed";
        state.error = action.error.message ?? "Unknown error";
      })
      .addCase(createNewWorktree.fulfilled, (state, action) => {
        state.worktrees.push(action.payload);
      })
      .addCase(switchActiveContext.fulfilled, (state, action) => {
        state.activeWorktreePath = action.payload;
      });
  },
});

export const { setActiveWorktree } = worktreeSlice.actions;
export default worktreeSlice.reducer;
