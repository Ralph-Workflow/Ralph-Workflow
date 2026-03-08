import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useAppSelector, useAppDispatch } from "../store";
import { createNewWorktree, fetchWorktrees } from "../store/slices/worktreeSlice";

interface CreateForm {
  branch: string;
  name: string;
}

export function Worktrees() {
  const dispatch = useAppDispatch();
  const navigate = useNavigate();
  const worktrees = useAppSelector((s) => s.worktrees.worktrees);
  const status = useAppSelector((s) => s.worktrees.status);
  const error = useAppSelector((s) => s.worktrees.error);
  const activePath = useAppSelector((s) => s.worktrees.activeWorktreePath);
  const mainWorktree = worktrees.find((wt) => wt.is_main);
  const repoPath = mainWorktree?.path ?? "";

  const [showCreate, setShowCreate] = useState(false);
  const [form, setForm] = useState<CreateForm>({ branch: "", name: "" });
  const [createError, setCreateError] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);

  const namePrefix = "wt-";
  const namePlaceholder = "wt-51-my-feature";

  const handleCreate = async () => {
    if (!repoPath) return;
    if (!form.name || !form.branch) {
      setCreateError("Branch and worktree name are required.");
      return;
    }
    setCreateError(null);
    setCreating(true);
    try {
      await dispatch(
        createNewWorktree({
          repoPath,
          branch: form.branch,
          name: form.name,
        }),
      ).unwrap();
      setShowCreate(false);
      setForm({ branch: "", name: "" });
      void dispatch(fetchWorktrees(repoPath));
    } catch (e: unknown) {
      setCreateError(e instanceof Error ? e.message : String(e));
    } finally {
      setCreating(false);
    }
  };

  if (!repoPath) {
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
            Worktrees
          </h1>
        </div>
        <div className="empty-state">
          <span className="empty-state-icon">⎇</span>
          <div className="empty-state-title">No repository context</div>
          <div className="empty-state-desc">
            Use the context switcher in the sidebar to select a repository.
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="page-content">
      {/* Header */}
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
          Worktrees
        </h1>
        <div style={{ display: "flex", gap: 8 }}>
          {showCreate ? (
            <button
              className="btn btn-ghost"
              onClick={() => {
                setShowCreate(false);
                setCreateError(null);
              }}
            >
              ← Cancel
            </button>
          ) : (
            <button
              className="btn btn-primary"
              onClick={() => setShowCreate(true)}
            >
              + New worktree
            </button>
          )}
        </div>
      </div>

      <div style={{ animation: "fadeIn 200ms ease 40ms both" }}>
        {/* Repo context */}
        <div
          style={{
            display: "flex",
            alignItems: "center",
            gap: 10,
            marginBottom: "var(--space-4)",
          }}
        >
          <div className="section-label" style={{ marginBottom: 0 }}>
            <span className="chip-mono">{repoPath}</span>
          </div>
        </div>

        {/* Create form */}
        {showCreate && (
          <div className="card" style={{ maxWidth: 520, marginBottom: "var(--space-5)" }}>
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
              New worktree
            </div>

            <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
              <div>
                <label className="form-label">Branch name</label>
                <input
                  className="form-input"
                  type="text"
                  placeholder="wt-51-my-feature"
                  value={form.branch}
                  onChange={(e) => setForm((f) => ({ ...f, branch: e.target.value }))}
                  onBlur={() => {
                    if (form.branch && !form.name) {
                      setForm((f) => ({ ...f, name: f.branch }));
                    }
                  }}
                />
                <div
                  style={{
                    marginTop: 4,
                    fontSize: 11,
                    color: "var(--text-muted)",
                    fontFamily: "var(--font-mono)",
                  }}
                >
                  Will be created from HEAD if it doesn't exist
                </div>
              </div>

              <div>
                <label className="form-label">
                  Worktree name{" "}
                  <span
                    style={{
                      color: "var(--text-muted)",
                      fontFamily: "var(--font-mono)",
                      fontSize: 10,
                    }}
                  >
                    ({namePrefix}&#8203;N-slug format required)
                  </span>
                </label>
                <input
                  className="form-input"
                  type="text"
                  placeholder={namePlaceholder}
                  value={form.name}
                  onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))}
                />
              </div>

              {createError && (
                <div
                  style={{
                    padding: "10px 12px",
                    background: "rgba(248,81,73,0.08)",
                    border: "1px solid rgba(248,81,73,0.2)",
                    borderRadius: "var(--radius-md)",
                    color: "var(--status-failed)",
                    fontSize: 12,
                    fontFamily: "var(--font-mono)",
                  }}
                >
                  {createError}
                </div>
              )}

              <button
                className="btn btn-primary"
                onClick={() => void handleCreate()}
                disabled={creating || !form.branch || !form.name}
                style={{ alignSelf: "flex-start" }}
              >
                {creating ? "Creating..." : "Create worktree"}
              </button>
            </div>
          </div>
        )}

        {/* Worktree list */}
        {status === "loading" && (
          <div
            style={{
              padding: "var(--space-8)",
              textAlign: "center",
              color: "var(--text-muted)",
              fontSize: 13,
              fontFamily: "var(--font-mono)",
            }}
          >
            Loading worktrees...
          </div>
        )}

        {status === "failed" && error && (
          <div
            style={{
              padding: "10px 14px",
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

        {status !== "loading" && worktrees.length === 0 && (
          <div className="empty-state">
            <span className="empty-state-icon">⎇</span>
            <div className="empty-state-title">No worktrees</div>
            <div className="empty-state-desc">
              Create a worktree to run parallel agent sessions on separate branches.
            </div>
          </div>
        )}

        {worktrees.length > 0 && (
          <div className="card" style={{ padding: "4px 0" }}>
            {worktrees.map((wt, idx) => {
              const isActive = wt.path === activePath || (activePath === null && wt.is_main);
              return (
                <div
                  key={wt.path}
                  style={{
                    display: "flex",
                    alignItems: "center",
                    gap: 12,
                    padding: "12px 16px",
                    borderBottom:
                      idx < worktrees.length - 1
                        ? "1px solid var(--border-subtle)"
                        : "none",
                    transition: "background var(--transition-fast)",
                  }}
                  onMouseEnter={(e) => {
                    (e.currentTarget as HTMLDivElement).style.background =
                      "var(--bg-elevated)";
                  }}
                  onMouseLeave={(e) => {
                    (e.currentTarget as HTMLDivElement).style.background =
                      "transparent";
                  }}
                >
                  {/* Active indicator */}
                  <div
                    style={{
                      width: 6,
                      height: 6,
                      borderRadius: "50%",
                      background: isActive
                        ? "var(--accent)"
                        : wt.has_active_run
                        ? "var(--status-running)"
                        : "var(--border-default)",
                      flexShrink: 0,
                    }}
                  />

                  {/* Info */}
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div
                      style={{
                        display: "flex",
                        alignItems: "center",
                        gap: 8,
                        marginBottom: 3,
                      }}
                    >
                      <span
                        style={{
                          fontFamily: "var(--font-mono)",
                          fontSize: 13,
                          fontWeight: 500,
                          color: "var(--text-primary)",
                        }}
                      >
                        {wt.name}
                      </span>
                      {wt.is_main && (
                        <span
                          style={{
                            fontSize: 10,
                            fontFamily: "var(--font-mono)",
                            color: "var(--accent)",
                            background: "var(--accent-bg)",
                            padding: "1px 6px",
                            borderRadius: "var(--radius-sm)",
                            border: "1px solid rgba(232,168,56,0.2)",
                          }}
                        >
                          main
                        </span>
                      )}
                      {wt.has_active_run && (
                        <span
                          style={{
                            fontSize: 10,
                            fontFamily: "var(--font-mono)",
                            color: "var(--status-running)",
                            background: "var(--status-running-bg)",
                            padding: "1px 6px",
                            borderRadius: "var(--radius-sm)",
                          }}
                        >
                          active run
                        </span>
                      )}
                    </div>
                    <div
                      style={{
                        display: "flex",
                        alignItems: "center",
                        gap: 10,
                      }}
                    >
                      <span
                        style={{
                          fontFamily: "var(--font-mono)",
                          fontSize: 11,
                          color: "var(--text-secondary)",
                        }}
                      >
                        ⎇ {wt.branch}
                      </span>
                      <span
                        style={{
                          fontFamily: "var(--font-mono)",
                          fontSize: 11,
                          color: "var(--text-muted)",
                          overflow: "hidden",
                          textOverflow: "ellipsis",
                          whiteSpace: "nowrap",
                        }}
                      >
                        {wt.path}
                      </span>
                    </div>
                  </div>

                  {/* Actions */}
                  <div style={{ display: "flex", alignItems: "center", gap: 8, flexShrink: 0 }}>
                    {isActive && (
                      <span
                        style={{
                          fontSize: 10,
                          fontFamily: "var(--font-mono)",
                          color: "var(--accent)",
                          padding: "2px 8px",
                          border: "1px solid rgba(232,168,56,0.3)",
                          borderRadius: "var(--radius-sm)",
                          background: "var(--accent-bg)",
                          letterSpacing: "0.04em",
                        }}
                      >
                        active context
                      </span>
                    )}
                    {!wt.is_main && (
                      <button
                        data-testid={`start-session-${wt.name}`}
                        className="btn btn-secondary"
                        style={{ fontSize: 11, padding: "3px 10px" }}
                        onClick={() =>
                          void navigate(
                            `/sessions?new=true&worktree=${encodeURIComponent(wt.path)}`,
                          )
                        }
                      >
                        Start session
                      </button>
                    )}
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
}
