import { describe, it, expect, vi, beforeEach } from "vitest";
import { configureStore } from "@reduxjs/toolkit";

vi.mock("../../api/tauri", () => ({
  listAgentProfiles: vi.fn(),
}));

import { listAgentProfiles } from "../../api/tauri";
import type { Mock } from "vitest";
import agentProfileReducer, {
  fetchAgentProfiles,
  selectAgentProfile,
  clearAgentProfileSelection,
} from "./agentProfileSlice";
import type { AgentProfile } from "../../types";

const mockListAgentProfiles = listAgentProfiles as Mock;

const mockProfiles: AgentProfile[] = [
  { name: "fast", developer_agent: "claude-haiku", reviewer_agent: "claude-haiku" },
  { name: "quality", developer_agent: "claude-opus", reviewer_agent: "claude-opus" },
];

function makeStore() {
  return configureStore({ reducer: { agentProfile: agentProfileReducer } });
}

beforeEach(() => {
  vi.clearAllMocks();
});

describe("agentProfileSlice", () => {
  it("has correct initial state", () => {
    const store = makeStore();
    const state = store.getState().agentProfile;
    expect(state.profiles).toEqual([]);
    expect(state.selectedProfile).toBeNull();
    expect(state.status).toBe("idle");
    expect(state.error).toBeNull();
  });

  it("selectAgentProfile sets selectedProfile", () => {
    const store = makeStore();
    store.dispatch(selectAgentProfile("fast"));
    expect(store.getState().agentProfile.selectedProfile).toBe("fast");
  });

  it("clearAgentProfileSelection resets selectedProfile to null", () => {
    const store = makeStore();
    store.dispatch(selectAgentProfile("quality"));
    store.dispatch(clearAgentProfileSelection());
    expect(store.getState().agentProfile.selectedProfile).toBeNull();
  });

  describe("fetchAgentProfiles thunk", () => {
    it("sets status to loading on pending", () => {
      mockListAgentProfiles.mockReturnValue(new Promise(() => undefined));
      const store = makeStore();
      void store.dispatch(fetchAgentProfiles());
      expect(store.getState().agentProfile.status).toBe("loading");
      expect(store.getState().agentProfile.error).toBeNull();
    });

    it("populates profiles and sets status to succeeded on fulfilled", async () => {
      mockListAgentProfiles.mockResolvedValueOnce(mockProfiles);
      const store = makeStore();
      await store.dispatch(fetchAgentProfiles());
      const state = store.getState().agentProfile;
      expect(state.status).toBe("succeeded");
      expect(state.profiles).toHaveLength(2);
      expect(state.profiles[0]?.name).toBe("fast");
      expect(state.profiles[1]?.name).toBe("quality");
    });

    it("sets status to failed with error message on rejected", async () => {
      mockListAgentProfiles.mockRejectedValueOnce(
        new Error("agents.toml not found"),
      );
      const store = makeStore();
      await store.dispatch(fetchAgentProfiles());
      const state = store.getState().agentProfile;
      expect(state.status).toBe("failed");
      expect(state.error).toBe("agents.toml not found");
    });

    it("passes repoPath to listAgentProfiles when provided", async () => {
      mockListAgentProfiles.mockResolvedValueOnce([]);
      const store = makeStore();
      await store.dispatch(fetchAgentProfiles("/my/repo"));
      expect(mockListAgentProfiles).toHaveBeenCalledWith("/my/repo");
    });

    it("passes undefined to listAgentProfiles when repoPath omitted", async () => {
      mockListAgentProfiles.mockResolvedValueOnce([]);
      const store = makeStore();
      await store.dispatch(fetchAgentProfiles());
      expect(mockListAgentProfiles).toHaveBeenCalledWith(undefined);
    });

    it("clears error when a new fetch starts", async () => {
      // First fetch fails
      mockListAgentProfiles.mockRejectedValueOnce(new Error("First error"));
      const store = makeStore();
      await store.dispatch(fetchAgentProfiles());
      expect(store.getState().agentProfile.error).toBe("First error");
      // Second fetch pending — error should clear
      mockListAgentProfiles.mockReturnValue(new Promise(() => undefined));
      void store.dispatch(fetchAgentProfiles());
      expect(store.getState().agentProfile.error).toBeNull();
    });

    it("selectedProfile is preserved across profile list updates", async () => {
      mockListAgentProfiles.mockResolvedValueOnce(mockProfiles);
      const store = makeStore();
      store.dispatch(selectAgentProfile("fast"));
      await store.dispatch(fetchAgentProfiles("/my/repo"));
      expect(store.getState().agentProfile.selectedProfile).toBe("fast");
    });
  });
});
