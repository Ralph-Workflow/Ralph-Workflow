import {
  createAsyncThunk,
  createSlice,
  type PayloadAction,
} from "@reduxjs/toolkit";
import {
  readPromptFile as apiReadPromptFile,
  savePromptFile as apiSavePromptFile,
  reviewPromptWithAi,
} from "../../api/tauri";
import type { PromptReviewResult } from "../../types";

interface PromptState {
  path: string | null;
  content: string;
  isDirty: boolean;
  reviewStatus: "idle" | "loading" | "succeeded" | "failed";
  reviewResult: PromptReviewResult | null;
  reviewError: string | null;
}

const initialState: PromptState = {
  path: null,
  content: "",
  isDirty: false,
  reviewStatus: "idle",
  reviewResult: null,
  reviewError: null,
};

export const loadPromptFile = createAsyncThunk(
  "prompt/loadFile",
  async (path: string) => apiReadPromptFile(path),
);

export const savePromptFile = createAsyncThunk(
  "prompt/saveFile",
  async ({ path, content }: { path: string; content: string }) =>
    apiSavePromptFile(path, content),
);

export const reviewPrompt = createAsyncThunk(
  "prompt/review",
  async (content: string) => reviewPromptWithAi(content),
);

const promptSlice = createSlice({
  name: "prompt",
  initialState,
  reducers: {
    setPromptPath(state, action: PayloadAction<string | null>) {
      state.path = action.payload;
      state.isDirty = false;
    },
    setPromptContent(state, action: PayloadAction<string>) {
      state.content = action.payload;
      state.isDirty = true;
    },
    revertPrompt(state) {
      state.content = "";
      state.isDirty = false;
    },
  },
  extraReducers: (builder) => {
    builder
      .addCase(loadPromptFile.fulfilled, (state, action) => {
        state.content = action.payload;
        state.isDirty = false;
      })
      .addCase(savePromptFile.fulfilled, (state) => {
        state.isDirty = false;
      })
      .addCase(reviewPrompt.pending, (state) => {
        state.reviewStatus = "loading";
        state.reviewError = null;
      })
      .addCase(reviewPrompt.fulfilled, (state, action) => {
        state.reviewStatus = "succeeded";
        state.reviewResult = action.payload;
      })
      .addCase(reviewPrompt.rejected, (state, action) => {
        state.reviewStatus = "failed";
        state.reviewError = action.error.message ?? "Review failed";
      });
  },
});

export const { setPromptPath, setPromptContent, revertPrompt } =
  promptSlice.actions;
export default promptSlice.reducer;
