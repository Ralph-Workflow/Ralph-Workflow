import { describe, it, expect, vi, beforeEach } from "vitest";
import { configureStore } from "@reduxjs/toolkit";
import configReducer, {
  fetchGlobalConfig,
  fetchEffectiveConfig,
  fetchAiApiKey,
  saveAiApiKeyThunk,
  saveGlobal,
  setDirty,
} from "./configSlice";
import type { ConfigView } from "../../types";

vi.mock("../../api/tauri", () => ({
  getGlobalConfig: vi.fn(),
  getProjectConfig: vi.fn(),
  getEffectiveConfig: vi.fn(),
  saveGlobalConfig: vi.fn(),
  saveProjectConfig: vi.fn(),
  getAiApiKey: vi.fn(),
  saveAiApiKey: vi.fn(),
}));

import {
  getGlobalConfig,
  getEffectiveConfig,
  saveGlobalConfig,
  getAiApiKey,
  saveAiApiKey,
} from "../../api/tauri";
import type { Mock } from "vitest";

const mockGetGlobalConfig = getGlobalConfig as Mock;
const mockGetEffectiveConfig = getEffectiveConfig as Mock;
const mockSaveGlobalConfig = saveGlobalConfig as Mock;
const mockGetAiApiKey = getAiApiKey as Mock;
const mockSaveAiApiKey = saveAiApiKey as Mock;

function makeStore() {
  return configureStore({
    reducer: { config: configReducer },
  });
}

const mockConfig: ConfigView = {
  verbosity: 2,
  developer_iters: 5,
  reviewer_reviews: 2,
  checkpoint_enabled: true,
  isolation_mode: true,
  interactive: true,
  review_depth: "standard",
  max_dev_continuations: 2,
};

beforeEach(() => {
  vi.clearAllMocks();
});

describe("configSlice", () => {
  it("initial state distinguishes global and project config slots", () => {
    const store = makeStore();
    const state = store.getState().config;
    expect(state.globalConfig).toBeNull();
    expect(state.projectConfig).toBeNull();
    expect(state.effectiveConfig).toBeNull();
    expect(state.isDirty).toBe(false);
  });

  it("initial state has empty aiApiKey and idle statuses", () => {
    const store = makeStore();
    const state = store.getState().config;
    expect(state.aiApiKey).toBe("");
    expect(state.aiApiKeyStatus).toBe("idle");
    expect(state.aiApiKeySaveStatus).toBe("idle");
    expect(state.aiApiKeyError).toBeNull();
  });

  it("fetchGlobalConfig.pending sets status to loading", () => {
    mockGetGlobalConfig.mockReturnValue(new Promise(() => undefined));
    const store = makeStore();
    void store.dispatch(fetchGlobalConfig());
    expect(store.getState().config.globalStatus).toBe("loading");
  });

  it("fetchGlobalConfig.fulfilled populates globalConfig", async () => {
    mockGetGlobalConfig.mockResolvedValueOnce(mockConfig);
    const store = makeStore();
    await store.dispatch(fetchGlobalConfig());
    const state = store.getState().config;
    expect(state.globalStatus).toBe("succeeded");
    expect(state.globalConfig).toEqual(mockConfig);
  });

  it("fetchGlobalConfig.rejected sets error", async () => {
    mockGetGlobalConfig.mockRejectedValueOnce(new Error("Config read error"));
    const store = makeStore();
    await store.dispatch(fetchGlobalConfig());
    const state = store.getState().config;
    expect(state.globalStatus).toBe("failed");
    expect(state.error).toBe("Config read error");
  });

  it("fetchEffectiveConfig.fulfilled populates effectiveConfig", async () => {
    const effectiveConfig = { ...mockConfig, verbosity: 3 };
    mockGetEffectiveConfig.mockResolvedValueOnce(effectiveConfig);
    const store = makeStore();
    await store.dispatch(fetchEffectiveConfig("/my/repo"));
    expect(store.getState().config.effectiveConfig?.verbosity).toBe(3);
  });

  it("saveGlobal.fulfilled updates globalConfig and resets dirty flag", async () => {
    mockSaveGlobalConfig.mockResolvedValueOnce(undefined);
    mockGetGlobalConfig.mockResolvedValueOnce({ ...mockConfig, verbosity: 4 });
    const store = makeStore();
    store.dispatch(setDirty(true));
    expect(store.getState().config.isDirty).toBe(true);
    await store.dispatch(saveGlobal("[general]\nverbosity = 4\n"));
    const state = store.getState().config;
    expect(state.globalConfig?.verbosity).toBe(4);
    expect(state.isDirty).toBe(false);
  });

  it("saveGlobal.rejected stores error and preserves isDirty true", async () => {
    mockSaveGlobalConfig.mockRejectedValueOnce(new Error("Disk write failed"));
    const store = makeStore();
    store.dispatch(setDirty(true));
    await store.dispatch(saveGlobal("[general]\nverbosity = 9\n"));
    const state = store.getState().config;
    expect(state.isDirty).toBe(true);
    expect(state.error).toBe("Disk write failed");
  });

  it("setDirty marks the config as dirty", () => {
    const store = makeStore();
    store.dispatch(setDirty(true));
    expect(store.getState().config.isDirty).toBe(true);
    store.dispatch(setDirty(false));
    expect(store.getState().config.isDirty).toBe(false);
  });

  it("revert action dispatch (setDirty false) clears dirty flag after user edits", () => {
    // Simulates the TomlEditor handleRevert flow: user edits TOML → isDirty=true,
    // then clicks Revert → dispatches setDirty(false) to clear the dirty flag.
    const store = makeStore();
    // Simulate user editing the TOML
    store.dispatch(setDirty(true));
    expect(store.getState().config.isDirty).toBe(true);
    // Simulate the revert action dispatch (Configuration.tsx handleRevert calls setDirty(false))
    store.dispatch(setDirty(false));
    expect(store.getState().config.isDirty).toBe(false);
  });

  it("clearConfigError clears the error field", async () => {
    const { clearConfigError } = await import("./configSlice");
    mockGetGlobalConfig.mockRejectedValueOnce(new Error("Read failed"));
    const store = makeStore();
    await store.dispatch(fetchGlobalConfig());
    expect(store.getState().config.error).toBe("Read failed");
    store.dispatch(clearConfigError());
    expect(store.getState().config.error).toBeNull();
  });

  it("saveProject.fulfilled sets isDirty to false", async () => {
    const { saveProject } = await import("./configSlice");
    const { saveProjectConfig } = await import("../../api/tauri");
    (saveProjectConfig as ReturnType<typeof vi.fn>).mockResolvedValueOnce(undefined);
    const store = makeStore();
    store.dispatch(setDirty(true));
    await store.dispatch(saveProject({ repoPath: "/my/repo", configToml: "[general]\n" }));
    expect(store.getState().config.isDirty).toBe(false);
  });

  // --- AI API key thunk tests ---

  it("fetchAiApiKey.pending sets aiApiKeyStatus to loading", () => {
    mockGetAiApiKey.mockReturnValue(new Promise(() => undefined));
    const store = makeStore();
    void store.dispatch(fetchAiApiKey());
    expect(store.getState().config.aiApiKeyStatus).toBe("loading");
  });

  it("fetchAiApiKey.fulfilled populates aiApiKey", async () => {
    mockGetAiApiKey.mockResolvedValueOnce("sk-test-key-abc");
    const store = makeStore();
    await store.dispatch(fetchAiApiKey());
    const state = store.getState().config;
    expect(state.aiApiKeyStatus).toBe("succeeded");
    expect(state.aiApiKey).toBe("sk-test-key-abc");
  });

  it("fetchAiApiKey.fulfilled with empty string keeps aiApiKey empty", async () => {
    mockGetAiApiKey.mockResolvedValueOnce("");
    const store = makeStore();
    await store.dispatch(fetchAiApiKey());
    expect(store.getState().config.aiApiKey).toBe("");
  });

  it("fetchAiApiKey.rejected sets aiApiKeyError", async () => {
    mockGetAiApiKey.mockRejectedValueOnce(new Error("Config read failed"));
    const store = makeStore();
    await store.dispatch(fetchAiApiKey());
    const state = store.getState().config;
    expect(state.aiApiKeyStatus).toBe("failed");
    expect(state.aiApiKeyError).toBe("Config read failed");
  });

  it("saveAiApiKeyThunk.pending sets aiApiKeySaveStatus to saving", () => {
    mockSaveAiApiKey.mockReturnValue(new Promise(() => undefined));
    const store = makeStore();
    void store.dispatch(saveAiApiKeyThunk("sk-new-key"));
    expect(store.getState().config.aiApiKeySaveStatus).toBe("saving");
  });

  it("saveAiApiKeyThunk.fulfilled updates aiApiKey and sets status to saved", async () => {
    mockSaveAiApiKey.mockResolvedValueOnce(undefined);
    const store = makeStore();
    await store.dispatch(saveAiApiKeyThunk("sk-saved-key"));
    const state = store.getState().config;
    expect(state.aiApiKeySaveStatus).toBe("saved");
    expect(state.aiApiKey).toBe("sk-saved-key");
  });

  it("saveAiApiKeyThunk.rejected sets aiApiKeyError", async () => {
    mockSaveAiApiKey.mockRejectedValueOnce(new Error("API key must not be empty"));
    const store = makeStore();
    await store.dispatch(saveAiApiKeyThunk(""));
    const state = store.getState().config;
    expect(state.aiApiKeySaveStatus).toBe("failed");
    expect(state.aiApiKeyError).toBe("API key must not be empty");
  });

  it("clearAiApiKeyError clears the aiApiKeyError field", async () => {
    const { clearAiApiKeyError } = await import("./configSlice");
    mockGetAiApiKey.mockRejectedValueOnce(new Error("test error"));
    const store = makeStore();
    await store.dispatch(fetchAiApiKey());
    expect(store.getState().config.aiApiKeyError).toBe("test error");
    store.dispatch(clearAiApiKeyError());
    expect(store.getState().config.aiApiKeyError).toBeNull();
  });

  it("resetAiApiKeySaveStatus resets save status to idle", async () => {
    const { resetAiApiKeySaveStatus } = await import("./configSlice");
    mockSaveAiApiKey.mockResolvedValueOnce(undefined);
    const store = makeStore();
    await store.dispatch(saveAiApiKeyThunk("sk-key"));
    expect(store.getState().config.aiApiKeySaveStatus).toBe("saved");
    store.dispatch(resetAiApiKeySaveStatus());
    expect(store.getState().config.aiApiKeySaveStatus).toBe("idle");
  });
});
