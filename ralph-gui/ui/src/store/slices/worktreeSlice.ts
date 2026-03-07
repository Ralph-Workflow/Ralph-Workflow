import {
  createAsyncThunk,
  createSlice,
  type PayloadAction,
} from "@reduxjs/toolkit";
import { listWorktrees, createWorktree, switchContext } from "../../api/tauri";
import type { WorktreeInfo } from "../../types";

const LAST_REPO_KEY = "ralph_gui_last_repo";

interface WorktreeState {
  worktrees: WorktreeInfo[];
  status: "idle" | "loading" | "succeeded" | "failed";
  error: string | null;
  activeWorktreePath: string | null;
  lastRepoPath: string | null;
}

const initialState: WorktreeState = {
  worktrees: [],
  status: "idle",
  error: null,
  activeWorktreePath: null,
  lastRepoPath: localStorage.getItem(LAST_REPO_KEY),
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

/// Initialize the app with a selected repository: load worktrees and persist path.
export const initializeRepo = createAsyncThunk(
  "worktrees/initializeRepo",
  async (repoPath: string) => {
    const worktrees = await listWorktrees(repoPath);
    return { repoPath, worktrees };
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
      })
      .addCase(initializeRepo.pending, (state) => {
        state.status = "loading";
        state.error = null;
      })
      .addCase(initializeRepo.fulfilled, (state, action) => {
        state.status = "succeeded";
        state.worktrees = action.payload.worktrees;
        state.lastRepoPath = action.payload.repoPath;
        localStorage.setItem(LAST_REPO_KEY, action.payload.repoPath);
      })
      .addCase(initializeRepo.rejected, (state, action) => {
        state.status = "failed";
        state.error = action.error.message ?? "Unknown error";
      });
  },
});

export const { setActiveWorktree } = worktreeSlice.actions;
export default worktreeSlice.reducer;
