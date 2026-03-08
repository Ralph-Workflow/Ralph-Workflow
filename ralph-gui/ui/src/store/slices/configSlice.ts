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
  getAiApiKey,
  saveAiApiKey,
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
  aiApiKey: string;
  aiApiKeyStatus: "idle" | "loading" | "succeeded" | "failed";
  aiApiKeySaveStatus: "idle" | "saving" | "saved" | "failed";
  aiApiKeyError: string | null;
}

const initialState: ConfigState = {
  globalConfig: null,
  projectConfig: null,
  effectiveConfig: null,
  globalStatus: "idle",
  projectStatus: "idle",
  error: null,
  isDirty: false,
  aiApiKey: "",
  aiApiKeyStatus: "idle",
  aiApiKeySaveStatus: "idle",
  aiApiKeyError: null,
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

export const fetchAiApiKey = createAsyncThunk(
  "config/fetchAiApiKey",
  async () => {
    return getAiApiKey();
  },
);

export const saveAiApiKeyThunk = createAsyncThunk(
  "config/saveAiApiKey",
  async (apiKey: string) => {
    await saveAiApiKey(apiKey);
    return apiKey;
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
    clearAiApiKeyError: (state) => {
      state.aiApiKeyError = null;
    },
    resetAiApiKeySaveStatus: (state) => {
      state.aiApiKeySaveStatus = "idle";
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
      })
      .addCase(fetchAiApiKey.pending, (state) => {
        state.aiApiKeyStatus = "loading";
        state.aiApiKeyError = null;
      })
      .addCase(fetchAiApiKey.fulfilled, (state, action) => {
        state.aiApiKeyStatus = "succeeded";
        state.aiApiKey = action.payload;
      })
      .addCase(fetchAiApiKey.rejected, (state, action) => {
        state.aiApiKeyStatus = "failed";
        state.aiApiKeyError = action.error.message ?? "Unknown error";
      })
      .addCase(saveAiApiKeyThunk.pending, (state) => {
        state.aiApiKeySaveStatus = "saving";
        state.aiApiKeyError = null;
      })
      .addCase(saveAiApiKeyThunk.fulfilled, (state, action) => {
        state.aiApiKeySaveStatus = "saved";
        state.aiApiKey = action.payload;
      })
      .addCase(saveAiApiKeyThunk.rejected, (state, action) => {
        state.aiApiKeySaveStatus = "failed";
        state.aiApiKeyError = action.error.message ?? "Unknown error";
      });
  },
});

export const { setDirty, clearConfigError, clearAiApiKeyError, resetAiApiKeySaveStatus } =
  configSlice.actions;
export default configSlice.reducer;
