import {
  createAsyncThunk,
  createSlice,
  type PayloadAction,
} from "@reduxjs/toolkit";
import { listAgentProfiles } from "../../api/tauri";
import type { AgentProfile } from "../../types";

interface AgentProfileState {
  profiles: AgentProfile[];
  selectedProfile: string | null;
  status: "idle" | "loading" | "succeeded" | "failed";
  error: string | null;
}

const initialState: AgentProfileState = {
  profiles: [],
  selectedProfile: null,
  status: "idle",
  error: null,
};

export const fetchAgentProfiles = createAsyncThunk(
  "agentProfile/fetchAll",
  async (repoPath?: string) => listAgentProfiles(repoPath),
);

const agentProfileSlice = createSlice({
  name: "agentProfile",
  initialState,
  reducers: {
    selectAgentProfile(state, action: PayloadAction<string>) {
      state.selectedProfile = action.payload;
    },
    clearAgentProfileSelection(state) {
      state.selectedProfile = null;
    },
  },
  extraReducers: (builder) => {
    builder
      .addCase(fetchAgentProfiles.pending, (state) => {
        state.status = "loading";
        state.error = null;
      })
      .addCase(fetchAgentProfiles.fulfilled, (state, action) => {
        state.status = "succeeded";
        state.profiles = action.payload;
      })
      .addCase(fetchAgentProfiles.rejected, (state, action) => {
        state.status = "failed";
        state.error =
          action.error.message ?? "Failed to load agent profiles";
      });
  },
});

export const { selectAgentProfile, clearAgentProfileSelection } =
  agentProfileSlice.actions;
export default agentProfileSlice.reducer;
