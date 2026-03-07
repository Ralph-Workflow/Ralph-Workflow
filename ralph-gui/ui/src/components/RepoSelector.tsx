import { useState } from "react";
import { useAppDispatch } from "../store";
import { initializeRepo } from "../store/slices/worktreeSlice";

interface RepoSelectorProps {
  onRepoSelected?: (path: string) => void;
}

export function RepoSelector({ onRepoSelected }: RepoSelectorProps) {
  const dispatch = useAppDispatch();
  const [repoPath, setRepoPath] = useState(
    () => localStorage.getItem("ralph_gui_last_repo") ?? "",
  );
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleConfirm() {
    const path = repoPath.trim();
    if (!path) return;
    setLoading(true);
    setError(null);
    try {
      await dispatch(initializeRepo(path)).unwrap();
      localStorage.setItem("ralph_gui_last_repo", path);
      onRepoSelected?.(path);
    } catch (e: unknown) {
      const msg =
        e instanceof Error
          ? e.message
          : e != null && typeof e === "object" && "message" in e
            ? String((e as { message: unknown }).message)
            : String(e);
      setError(msg);
    } finally {
      setLoading(false);
    }
  }

  return (
    <div
      data-testid="repo-selector"
      style={{ display: "flex", flexDirection: "column", gap: 12, maxWidth: 480 }}
    >
      <div style={{ display: "flex", gap: 8 }}>
        <input
          data-testid="repo-path-input"
          className="input input-mono"
          style={{ flex: 1 }}
          value={repoPath}
          onChange={(e) => setRepoPath(e.target.value)}
          placeholder="/path/to/your/git/repository"
          onKeyDown={(e) => {
            if (e.key === "Enter") void handleConfirm();
          }}
        />
        <button
          data-testid="open-repo-button"
          className="btn btn-primary"
          style={{ flexShrink: 0 }}
          onClick={() => void handleConfirm()}
          disabled={loading || !repoPath.trim()}
        >
          {loading ? "Opening…" : "Open"}
        </button>
      </div>
      {error && (
        <div
          data-testid="repo-error"
          style={{
            padding: "8px 12px",
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
    </div>
  );
}
