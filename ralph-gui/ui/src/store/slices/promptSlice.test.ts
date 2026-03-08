import { describe, it, expect, vi, beforeEach } from "vitest";
import { configureStore } from "@reduxjs/toolkit";

vi.mock("../../api/tauri", () => ({
  readPromptFile: vi.fn(),
  savePromptFile: vi.fn(),
  reviewPromptWithAi: vi.fn(),
}));

import { readPromptFile, reviewPromptWithAi } from "../../api/tauri";
import { savePromptFile as mockApiSavePromptFile } from "../../api/tauri";
import type { Mock } from "vitest";
import promptReducer, {
  setPromptPath,
  setPromptContent,
  revertPrompt,
  loadPromptFile,
  savePromptFile,
  reviewPrompt,
} from "./promptSlice";

const mockReadPromptFile = readPromptFile as Mock;
const mockSavePromptFile = mockApiSavePromptFile as Mock;
const mockReviewPromptWithAi = reviewPromptWithAi as Mock;

function makeStore() {
  return configureStore({ reducer: { prompt: promptReducer } });
}

beforeEach(() => {
  vi.clearAllMocks();
});

describe("promptSlice", () => {
  it("has correct initial state", () => {
    const store = makeStore();
    const state = store.getState().prompt;
    expect(state.path).toBeNull();
    expect(state.content).toBe("");
    expect(state.isDirty).toBe(false);
    expect(state.reviewStatus).toBe("idle");
    expect(state.reviewResult).toBeNull();
    expect(state.reviewError).toBeNull();
  });

  it("setPromptPath updates path and clears isDirty", () => {
    const store = makeStore();
    store.dispatch(setPromptContent("some content"));
    expect(store.getState().prompt.isDirty).toBe(true);
    store.dispatch(setPromptPath("/my/repo/PROMPT.md"));
    expect(store.getState().prompt.path).toBe("/my/repo/PROMPT.md");
    expect(store.getState().prompt.isDirty).toBe(false);
  });

  it("setPromptPath with null clears path", () => {
    const store = makeStore();
    store.dispatch(setPromptPath("/some/path"));
    store.dispatch(setPromptPath(null));
    expect(store.getState().prompt.path).toBeNull();
  });

  it("setPromptContent updates content and sets isDirty true", () => {
    const store = makeStore();
    store.dispatch(setPromptContent("# My Task\n\nDo the thing."));
    const state = store.getState().prompt;
    expect(state.content).toBe("# My Task\n\nDo the thing.");
    expect(state.isDirty).toBe(true);
  });

  it("revertPrompt resets content and isDirty to initial values", () => {
    const store = makeStore();
    store.dispatch(setPromptContent("# Some content"));
    expect(store.getState().prompt.isDirty).toBe(true);
    store.dispatch(revertPrompt());
    const state = store.getState().prompt;
    expect(state.content).toBe("");
    expect(state.isDirty).toBe(false);
  });

  describe("loadPromptFile thunk", () => {
    it("sets content and clears isDirty on fulfilled", async () => {
      mockReadPromptFile.mockResolvedValueOnce("# Loaded content");
      const store = makeStore();
      store.dispatch(setPromptContent("old content"));
      await store.dispatch(loadPromptFile("/my/repo/PROMPT.md"));
      const state = store.getState().prompt;
      expect(state.content).toBe("# Loaded content");
      expect(state.isDirty).toBe(false);
    });

    it("calls readPromptFile with the given path", async () => {
      mockReadPromptFile.mockResolvedValueOnce("content");
      const store = makeStore();
      await store.dispatch(loadPromptFile("/my/repo/PROMPT.md"));
      expect(mockReadPromptFile).toHaveBeenCalledWith("/my/repo/PROMPT.md");
    });

    it("rejects when readPromptFile throws", async () => {
      mockReadPromptFile.mockRejectedValueOnce(new Error("File not found"));
      const store = makeStore();
      const result = await store.dispatch(loadPromptFile("/missing.md"));
      expect(result.type).toBe("prompt/loadFile/rejected");
    });
  });

  describe("savePromptFile thunk", () => {
    it("clears isDirty on fulfilled", async () => {
      mockSavePromptFile.mockResolvedValueOnce(undefined);
      const store = makeStore();
      store.dispatch(setPromptContent("new content"));
      expect(store.getState().prompt.isDirty).toBe(true);
      await store.dispatch(
        savePromptFile({ path: "/my/repo/PROMPT.md", content: "new content" }),
      );
      expect(store.getState().prompt.isDirty).toBe(false);
    });

    it("calls savePromptFile API with path and content", async () => {
      mockSavePromptFile.mockResolvedValueOnce(undefined);
      const store = makeStore();
      await store.dispatch(
        savePromptFile({ path: "/my/repo/PROMPT.md", content: "the prompt" }),
      );
      expect(mockSavePromptFile).toHaveBeenCalledWith(
        "/my/repo/PROMPT.md",
        "the prompt",
      );
    });

    it("rejects when savePromptFile API throws", async () => {
      mockSavePromptFile.mockRejectedValueOnce(new Error("Permission denied"));
      const store = makeStore();
      const result = await store.dispatch(
        savePromptFile({ path: "/my/repo/PROMPT.md", content: "content" }),
      );
      expect(result.type).toBe("prompt/saveFile/rejected");
    });
  });

  describe("reviewPrompt thunk", () => {
    it("sets reviewStatus to loading on pending", () => {
      mockReviewPromptWithAi.mockReturnValue(new Promise(() => undefined));
      const store = makeStore();
      void store.dispatch(reviewPrompt("# My prompt"));
      expect(store.getState().prompt.reviewStatus).toBe("loading");
      expect(store.getState().prompt.reviewError).toBeNull();
    });

    it("sets reviewResult and reviewStatus to succeeded on fulfilled", async () => {
      const result = {
        suggestions: ["Add acceptance criteria"],
        improved_prompt: "# Improved prompt",
      };
      mockReviewPromptWithAi.mockResolvedValueOnce(result);
      const store = makeStore();
      await store.dispatch(reviewPrompt("# My prompt"));
      const state = store.getState().prompt;
      expect(state.reviewStatus).toBe("succeeded");
      expect(state.reviewResult).toEqual(result);
    });

    it("sets reviewError and reviewStatus to failed on rejected", async () => {
      mockReviewPromptWithAi.mockRejectedValueOnce(new Error("API error"));
      const store = makeStore();
      await store.dispatch(reviewPrompt("# My prompt"));
      const state = store.getState().prompt;
      expect(state.reviewStatus).toBe("failed");
      expect(state.reviewError).toBe("API error");
    });

    it("calls reviewPromptWithAi with the prompt content", async () => {
      mockReviewPromptWithAi.mockResolvedValueOnce({
        suggestions: [],
        improved_prompt: null,
      });
      const store = makeStore();
      await store.dispatch(reviewPrompt("# My task prompt"));
      expect(mockReviewPromptWithAi).toHaveBeenCalledWith("# My task prompt");
    });

    it("clears reviewError when a new review starts", async () => {
      // First review fails
      mockReviewPromptWithAi.mockRejectedValueOnce(new Error("First error"));
      const store = makeStore();
      await store.dispatch(reviewPrompt("# prompt"));
      expect(store.getState().prompt.reviewError).toBe("First error");
      // Second review pending — error should clear
      mockReviewPromptWithAi.mockReturnValue(new Promise(() => undefined));
      void store.dispatch(reviewPrompt("# prompt"));
      expect(store.getState().prompt.reviewError).toBeNull();
    });
  });
});
