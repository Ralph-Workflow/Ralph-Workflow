import { useEffect } from "react";
import { useAppDispatch, useAppSelector } from "../../store";
import { fetchSessions, setActiveSession } from "../../store/slices/sessionSlice";
import { RunStatusBadge } from "../RunStatusBadge";
import type { RunStatus, SessionSummary } from "../../types";

function sessionStatusToRunStatus(status: string): RunStatus {
  switch (status) {
    case "running": return "Running";
    case "paused":
    case "interrupted": return "Paused";
    case "completed": return "Completed";
    case "failed": return "Failed";
    default: return "NotStarted";
  }
}

interface SessionListProps {
  repoPath: string;
  onResume?: (runId: string) => void;
  /** If non-empty, only sessions with a status in this array are shown. */
  filterStatus?: string[];
  /**
   * If provided, only sessions whose worktree_path matches this value are shown.
   * Pass the empty string "" to show direct-repo sessions (worktree_path === null).
   * Pass null or undefined to disable the filter.
   */
  filterWorktreePath?: string | null;
}

export function SessionList({ repoPath, onResume, filterStatus, filterWorktreePath }: SessionListProps) {
  const dispatch = useAppDispatch();
  const { sessions, status, selectedRunId } = useAppSelector((s) => s.sessions);

  useEffect(() => {
    if (repoPath) {
      void dispatch(fetchSessions(repoPath));
    }
  }, [dispatch, repoPath]);

  if (status === "loading") {
    return (
      <div style={{ padding: "24px", color: "var(--text-muted)", fontSize: 13, fontFamily: "var(--font-mono)" }}>
        Loading sessions…
      </div>
    );
  }

  if (sessions.length === 0) {
    return (
      <div className="empty-state">
        <span className="empty-state-icon">◈</span>
        <div className="empty-state-title">No sessions yet</div>
        <div className="empty-state-desc">Start a new session to begin an unattended Ralph workflow.</div>
      </div>
    );
  }

  // Apply client-side filters
  const hasStatusFilter = filterStatus !== undefined && filterStatus.length > 0;
  const hasWorktreeFilter = filterWorktreePath !== undefined && filterWorktreePath !== null;

  const visible = sessions.filter((s) => {
    if (hasStatusFilter && !filterStatus.includes(s.status)) return false;
    if (hasWorktreeFilter) {
      // "" means direct repo (worktree_path === null); otherwise match path
      if (filterWorktreePath === "") {
        if (s.worktree_path !== null) return false;
      } else {
        if (s.worktree_path !== filterWorktreePath) return false;
      }
    }
    return true;
  });

  if (visible.length === 0) {
    return (
      <div className="empty-state">
        <span className="empty-state-icon">◈</span>
        <div className="empty-state-title">No sessions match the selected filters.</div>
        <div className="empty-state-desc">Try clearing the filters to see all sessions.</div>
      </div>
    );
  }

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 2 }}>
      {visible.map((session) => (
        <SessionRow
          key={session.run_id}
          session={session}
          selected={session.run_id === selectedRunId}
          onSelect={() => dispatch(setActiveSession(session.run_id))}
          onResume={
            (session.status === "paused" || session.status === "interrupted") && onResume
              ? () => onResume(session.run_id)
              : undefined
          }
        />
      ))}
    </div>
  );
}

function SessionRow({
  session,
  selected,
  onSelect,
  onResume,
}: {
  session: SessionSummary;
  selected: boolean;
  onSelect: () => void;
  onResume?: () => void;
}) {
  return (
    <div
      onClick={onSelect}
      style={{
        display: "flex",
        alignItems: "center",
        gap: 12,
        padding: "10px 14px",
        borderRadius: "var(--radius-md)",
        background: selected ? "var(--accent-bg)" : "transparent",
        border: selected ? "1px solid var(--accent-dim)30" : "1px solid transparent",
        cursor: "pointer",
        transition: "all var(--transition-fast)",
      }}
    >
      <RunStatusBadge
        status={sessionStatusToRunStatus(session.status)}
        showLabel={false}
        size="sm"
      />
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ fontFamily: "var(--font-mono)", fontSize: 12, color: "var(--text-primary)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
          {session.run_id.slice(0, 16)}
        </div>
        <div style={{ fontSize: 11, color: "var(--text-muted)", marginTop: 1 }}>
          {session.description} · {session.created_at}
        </div>
      </div>
      {onResume && (
        <button
          className="btn btn-secondary"
          style={{ padding: "3px 10px", fontSize: 11 }}
          onClick={(e) => { e.stopPropagation(); onResume(); }}
        >
          Resume
        </button>
      )}
    </div>
  );
}
