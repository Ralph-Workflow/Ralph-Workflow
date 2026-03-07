import {
  createAsyncThunk,
  createSlice,
  type PayloadAction,
} from "@reduxjs/toolkit";
import {
  getGlobalConfig,
  getEffectiveConfig,
  saveGlobalConfig,
  saveProjectConfig,
} from "../../api/tauri";
import type { ConfigView } from "../../types";

interface ConfigState {
  globalConfig: ConfigView | null;
  projectConfig: ConfigView | null;
  effectiveConfig: ConfigView | null;
  globalStatus: "idle" | "loading" | "succeeded" | "failed";
  projectStatus: "idle" | "loading" | "succeeded" | "failed";
  error: string | null;
  isDirty: boolean;
}

const initialState: ConfigState = {
  globalConfig: null,
  projectConfig: null,
  effectiveConfig: null,
  globalStatus: "idle",
  projectStatus: "idle",
  error: null,
  isDirty: false,
};

export const fetchGlobalConfig = createAsyncThunk(
  "config/fetchGlobal",
  async () => {
    return getGlobalConfig();
  },
);

export const fetchEffectiveConfig = createAsyncThunk(
  "config/fetchEffective",
  async (repoPath: string) => {
    return getEffectiveConfig(repoPath);
  },
);

export const saveGlobal = createAsyncThunk(
  "config/saveGlobal",
  async (configToml: string) => {
    await saveGlobalConfig(configToml);
    return getGlobalConfig();
  },
);

export const saveProject = createAsyncThunk(
  "config/saveProject",
  async (args: { repoPath: string; configToml: string }) => {
    await saveProjectConfig(args.repoPath, args.configToml);
  },
);

const configSlice = createSlice({
  name: "config",
  initialState,
  reducers: {
    setDirty: (state, action: PayloadAction<boolean>) => {
      state.isDirty = action.payload;
    },
    clearConfigError: (state) => {
      state.error = null;
    },
  },
  extraReducers: (builder) => {
    builder
      .addCase(fetchGlobalConfig.pending, (state) => {
        state.globalStatus = "loading";
        state.error = null;
      })
      .addCase(fetchGlobalConfig.fulfilled, (state, action) => {
        state.globalStatus = "succeeded";
        state.globalConfig = action.payload;
      })
      .addCase(fetchGlobalConfig.rejected, (state, action) => {
        state.globalStatus = "failed";
        state.error = action.error.message ?? "Unknown error";
      })
      .addCase(fetchEffectiveConfig.fulfilled, (state, action) => {
        state.effectiveConfig = action.payload;
      })
      .addCase(saveGlobal.fulfilled, (state, action) => {
        state.globalConfig = action.payload;
        state.isDirty = false;
      })
      .addCase(saveProject.fulfilled, (state) => {
        state.isDirty = false;
      });
  },
});

export const { setDirty, clearConfigError } = configSlice.actions;
export default configSlice.reducer;
