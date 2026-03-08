import { describe, it, expect, vi, beforeEach } from "vitest";
import { configureStore } from "@reduxjs/toolkit";
import configReducer, {
  fetchGlobalConfig,
  fetchEffectiveConfig,
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
}));

import {
  getGlobalConfig,
  getEffectiveConfig,
  saveGlobalConfig,
} from "../../api/tauri";
import type { Mock } from "vitest";

const mockGetGlobalConfig = getGlobalConfig as Mock;
const mockGetEffectiveConfig = getEffectiveConfig as Mock;
const mockSaveGlobalConfig = saveGlobalConfig as Mock;

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

  it("setDirty marks the config as dirty", () => {
    const store = makeStore();
    store.dispatch(setDirty(true));
    expect(store.getState().config.isDirty).toBe(true);
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
});
