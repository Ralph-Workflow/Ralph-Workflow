import { useState, useEffect } from "react";
import { useSearchParams } from "react-router-dom";
import { useAppSelector } from "../store";
import { SessionList } from "../components/sessions/SessionList";
import { NewSessionWizard } from "../components/sessions/NewSessionWizard";

type View = "list" | "new";

// Status filter options shown as chips in the filter toolbar
const STATUS_CHIPS = [
  { label: "Running", value: "running" },
  { label: "Paused", value: "paused" },
  { label: "Completed", value: "completed" },
  { label: "Failed", value: "failed" },
] as const;

export function Sessions() {
  const [searchParams, setSearchParams] = useSearchParams();
  const [view, setView] = useState<View>(() =>
    searchParams.get("new") === "true" ? "new" : "list",
  );
  const preselectedWorktree = searchParams.get("worktree");
  const worktrees = useAppSelector((s) => s.worktrees.worktrees);
  const activePath = useAppSelector((s) => s.worktrees.activeWorktreePath);
  const mainWorktree = worktrees.find((wt) => wt.is_main);

  // Filter state — transient UI state, not stored in Redux
  const [activeStatusFilters, setActiveStatusFilters] = useState<string[]>([]);
  const [contextFilter, setContextFilter] = useState<string>("all");

  // Clear query params once consumed so back-navigation stays clean
  useEffect(() => {
    if (searchParams.get("new") === "true") {
      setSearchParams({}, { replace: true });
    }
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const repoPath =
    activePath ?? mainWorktree?.path ?? "";

  function toggleStatusFilter(value: string) {
    setActiveStatusFilters((prev) =>
      prev.includes(value) ? prev.filter((v) => v !== value) : [...prev, value],
    );
  }

  // Derive the filterWorktreePath to pass to SessionList from the context dropdown value
  const filterWorktreePath: string | null | undefined =
    contextFilter === "all"
      ? undefined
      : contextFilter === "direct"
      ? ""
      : contextFilter; // a specific worktree path

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
            <NewSessionWizard
              onClose={() => setView("list")}
              preselectedWorktreePath={preselectedWorktree}
            />
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
              <>
                {/* Filter toolbar */}
                <div
                  data-testid="filter-toolbar"
                  style={{
                    display: "flex",
                    alignItems: "center",
                    gap: 8,
                    marginBottom: "var(--space-3)",
                    flexWrap: "wrap",
                  }}
                >
                  <span
                    style={{
                      fontSize: 11,
                      color: "var(--text-muted)",
                      fontFamily: "var(--font-mono)",
                      marginRight: 4,
                    }}
                  >
                    Filter:
                  </span>
                  {STATUS_CHIPS.map(({ label, value }) => {
                    const active = activeStatusFilters.includes(value);
                    return (
                      <button
                        key={value}
                        data-testid={`filter-chip-${value}`}
                        onClick={() => toggleStatusFilter(value)}
                        style={{
                          padding: "2px 10px",
                          borderRadius: 100,
                          fontSize: 11,
                          fontWeight: 500,
                          cursor: "pointer",
                          border: active
                            ? "1px solid var(--accent)"
                            : "1px solid var(--border-default)",
                          background: active ? "var(--accent-bg)" : "transparent",
                          color: active ? "var(--accent)" : "var(--text-muted)",
                          transition: "all var(--transition-fast)",
                        }}
                      >
                        {label}
                      </button>
                    );
                  })}
                  {/* Context filter dropdown */}
                  <select
                    data-testid="filter-context"
                    value={contextFilter}
                    onChange={(e) => setContextFilter(e.target.value)}
                    style={{
                      marginLeft: 8,
                      padding: "2px 8px",
                      borderRadius: "var(--radius-sm)",
                      border: "1px solid var(--border-default)",
                      background: "var(--bg-surface)",
                      color: "var(--text-muted)",
                      fontSize: 11,
                      fontFamily: "var(--font-mono)",
                      cursor: "pointer",
                    }}
                  >
                    <option value="all">All contexts</option>
                    <option value="direct">Direct repo</option>
                    {worktrees
                      .filter((wt) => !wt.is_main)
                      .map((wt) => (
                        <option key={wt.path} value={wt.path}>
                          {wt.name}
                        </option>
                      ))}
                  </select>
                  {/* Clear filters */}
                  {(activeStatusFilters.length > 0 || contextFilter !== "all") && (
                    <button
                      data-testid="clear-filters"
                      className="btn btn-ghost"
                      style={{ fontSize: 11, padding: "2px 8px" }}
                      onClick={() => {
                        setActiveStatusFilters([]);
                        setContextFilter("all");
                      }}
                    >
                      Clear
                    </button>
                  )}
                </div>

                <div className="card" style={{ padding: "4px 0" }}>
                  <SessionList
                    repoPath={repoPath}
                    filterStatus={activeStatusFilters}
                    filterWorktreePath={filterWorktreePath}
                  />
                </div>
              </>
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
