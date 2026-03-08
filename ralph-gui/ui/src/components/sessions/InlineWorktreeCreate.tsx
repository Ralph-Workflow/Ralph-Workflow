import { useState } from "react";
import { useAppDispatch } from "../../store";
import { createNewWorktree } from "../../store/slices/worktreeSlice";
import type { WorktreeInfo } from "../../types";

interface InlineWorktreeCreateProps {
  repoPath: string;
  onCreated: (worktree: WorktreeInfo) => void;
}

export function InlineWorktreeCreate({
  repoPath,
  onCreated,
}: InlineWorktreeCreateProps) {
  const dispatch = useAppDispatch();
  const [branch, setBranch] = useState("");
  const [name, setName] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);

  const handleBranchBlur = () => {
    if (branch && !name) {
      setName(branch);
    }
  };

  const handleCreate = async () => {
    if (!repoPath || !branch || !name) return;
    setCreating(true);
    setError(null);
    try {
      const worktree = await dispatch(
        createNewWorktree({ repoPath, branch, name }),
      ).unwrap();
      onCreated(worktree);
    } catch (e: unknown) {
      if (e instanceof Error) {
        setError(e.message);
      } else if (typeof e === "object" && e !== null && "message" in e) {
        setError(String((e as { message: unknown }).message));
      } else {
        setError(String(e));
      }
    } finally {
      setCreating(false);
    }
  };

  return (
    <div
      data-testid="inline-worktree-create"
      style={{
        display: "flex",
        flexDirection: "column",
        gap: 10,
        marginTop: 8,
        padding: "12px 14px",
        background: "var(--bg-elevated)",
        border: "1px solid var(--border-subtle)",
        borderRadius: "var(--radius-md)",
      }}
    >
      <div>
        <label className="section-label" htmlFor="wt-branch">
          Branch name
        </label>
        <input
          id="wt-branch"
          data-testid="wt-branch-input"
          className="input input-mono"
          placeholder="wt-51-my-feature"
          value={branch}
          onChange={(e) => setBranch(e.target.value)}
          onBlur={handleBranchBlur}
          disabled={!repoPath}
        />
        <div
          style={{
            marginTop: 4,
            fontSize: 11,
            color: "var(--text-muted)",
            fontFamily: "var(--font-mono)",
          }}
        >
          Will be created from HEAD if it does not exist
        </div>
      </div>

      <div>
        <label className="section-label" htmlFor="wt-name">
          Worktree name{" "}
          <span
            style={{
              color: "var(--text-muted)",
              fontFamily: "var(--font-mono)",
              fontSize: 10,
            }}
          >
            (wt-N-slug format)
          </span>
        </label>
        <input
          id="wt-name"
          data-testid="wt-name-input"
          className="input input-mono"
          placeholder="wt-51-my-feature"
          value={name}
          onChange={(e) => setName(e.target.value)}
          disabled={!repoPath}
        />
      </div>

      {!repoPath && (
        <div
          style={{
            fontSize: 11,
            color: "var(--text-muted)",
            fontFamily: "var(--font-mono)",
          }}
        >
          Select a repository first
        </div>
      )}

      {error && (
        <div
          data-testid="wt-create-error"
          style={{
            padding: "6px 10px",
            background: "rgba(248,81,73,0.08)",
            border: "1px solid rgba(248,81,73,0.2)",
            borderRadius: "var(--radius-md)",
            color: "var(--status-failed)",
            fontSize: 12,
            fontFamily: "var(--font-mono)",
          }}
        >
          {error}
        </div>
      )}

      <button
        data-testid="wt-create-button"
        className="btn btn-secondary"
        onClick={() => void handleCreate()}
        disabled={creating || !branch || !name || !repoPath}
        style={{ alignSelf: "flex-start" }}
      >
        {creating ? "Creating..." : "Create worktree"}
      </button>
    </div>
  );
}
