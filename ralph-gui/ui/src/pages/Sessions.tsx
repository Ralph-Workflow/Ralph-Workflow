import { useState } from "react";
import { useAppSelector } from "../store";
import { SessionList } from "../components/sessions/SessionList";
import { NewSessionWizard } from "../components/sessions/NewSessionWizard";

type View = "list" | "new";

export function Sessions() {
  const [view, setView] = useState<View>("list");
  const worktrees = useAppSelector((s) => s.worktrees.worktrees);
  const activePath = useAppSelector((s) => s.worktrees.activeWorktreePath);
  const mainWorktree = worktrees.find((wt) => wt.is_main);

  const repoPath =
    activePath ?? mainWorktree?.path ?? "";

  return (
    <div className="page-content">
      <div
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          marginBottom: "var(--space-6)",
          animation: "fadeIn 200ms ease",
        }}
      >
        <h1 className="page-title" style={{ marginBottom: 0 }}>
          Sessions
        </h1>
        <div style={{ display: "flex", gap: 8 }}>
          {view === "new" ? (
            <button className="btn btn-ghost" onClick={() => setView("list")}>
              ← Back to list
            </button>
          ) : (
            <button
              className="btn btn-primary"
              onClick={() => setView("new")}
            >
              + New session
            </button>
          )}
        </div>
      </div>

      <div style={{ animation: "fadeIn 200ms ease 40ms both" }}>
        {view === "new" ? (
          <div className="card" style={{ maxWidth: 620 }}>
            <div
              style={{
                fontFamily: "var(--font-display)",
                fontSize: 16,
                fontWeight: 600,
                color: "var(--text-primary)",
                marginBottom: 20,
                letterSpacing: "-0.01em",
              }}
            >
              New session
            </div>
            <NewSessionWizard onClose={() => setView("list")} />
          </div>
        ) : (
          <div>
            <div
              style={{
                display: "flex",
                alignItems: "center",
                gap: 10,
                marginBottom: "var(--space-4)",
              }}
            >
              <div className="section-label" style={{ marginBottom: 0 }}>
                {repoPath ? (
                  <span className="chip-mono">{repoPath}</span>
                ) : (
                  "No repository selected"
                )}
              </div>
            </div>

            {repoPath ? (
              <div className="card" style={{ padding: "4px 0" }}>
                <SessionList repoPath={repoPath} />
              </div>
            ) : (
              <div className="empty-state">
                <span className="empty-state-icon">⎇</span>
                <div className="empty-state-title">No repository context</div>
                <div className="empty-state-desc">
                  Use the context switcher in the sidebar to select a repository.
                </div>
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
