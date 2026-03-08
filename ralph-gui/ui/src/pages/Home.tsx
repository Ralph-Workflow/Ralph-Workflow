import { useEffect } from "react";
import { useNavigate } from "react-router-dom";
import { useAppDispatch, useAppSelector } from "../store";
import { fetchWorktrees } from "../store/slices/worktreeSlice";
import { fetchResumableRuns } from "../store/slices/runSlice";
import { RunStatusBadge } from "../components/RunStatusBadge";
import { RepoSelector } from "../components/RepoSelector";

export function Home() {
  const dispatch = useAppDispatch();
  const navigate = useNavigate();
  const worktrees = useAppSelector((s) => s.worktrees.worktrees);
  const resumableRuns = useAppSelector((s) => s.runs.resumableRuns);
  const mainWorktree = worktrees.find((wt) => wt.is_main);

  useEffect(() => {
    if (mainWorktree) {
      void dispatch(fetchResumableRuns(mainWorktree.path));
    }
  }, [dispatch, mainWorktree]);

  useEffect(() => {
    if (mainWorktree) {
      void dispatch(fetchWorktrees(mainWorktree.path));
    }
  }, [dispatch, mainWorktree]);

  const hasContent = worktrees.length > 0 || resumableRuns.length > 0;

  if (!hasContent) {
    return (
      <div className="page-content" style={{ display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center" }}>
        <div style={{ maxWidth: 480, textAlign: "center", animation: "fadeIn 300ms ease" }}>
          <div style={{ fontSize: 48, marginBottom: 16, opacity: 0.2 }}>◈</div>
          <h1 style={{ fontFamily: "var(--font-display)", fontSize: 24, fontWeight: 600, color: "var(--text-primary)", letterSpacing: "-0.02em", marginBottom: 8 }}>
            Welcome to Ralph Workflow
          </h1>
          <p style={{ fontSize: 14, color: "var(--text-secondary)", lineHeight: 1.6, marginBottom: 24 }}>
            An unattended AI orchestration platform for long-running development and review cycles.
          </p>
          <div style={{ textAlign: "left", marginBottom: 16 }}>
            <div style={{ fontSize: 12, color: "var(--text-muted)", fontFamily: "var(--font-mono)", marginBottom: 8 }}>
              Enter your repository path to get started:
            </div>
            <RepoSelector onRepoSelected={() => void navigate("/")} />
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="page-content">
      <h1 className="page-title" style={{ animation: "fadeIn 200ms ease" }}>Home</h1>

      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 16, marginBottom: 24, animation: "fadeIn 200ms ease 40ms both" }}>
        <StatCard label="Active worktrees" value={worktrees.filter((wt) => !wt.is_main).length} />
        <StatCard label="Resumable runs" value={resumableRuns.length} accent={resumableRuns.length > 0} />
      </div>

      {resumableRuns.length > 0 && (
        <section style={{ marginBottom: 24, animation: "fadeIn 200ms ease 80ms both" }}>
          <div className="section-label">Interrupted runs — action needed</div>
          <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
            {resumableRuns.map((run) => (
              <div
                key={run.run_id}
                className="card card-elevated"
                style={{ display: "flex", alignItems: "center", gap: 12, padding: "12px 16px" }}
              >
                <RunStatusBadge status="Paused" showLabel={false} isDegraded={run.is_degraded} />
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{ fontFamily: "var(--font-mono)", fontSize: 12, color: "var(--text-primary)" }}>
                    {run.run_id.slice(0, 16)}
                  </div>
                  <div style={{ fontSize: 11, color: "var(--text-muted)" }}>
                    {run.current_phase} · {run.agent_profile}
                  </div>
                </div>
                <button className="btn btn-secondary" style={{ fontSize: 12 }} onClick={() => void navigate(`/runs/${run.run_id}`)}>
                  View
                </button>
              </div>
            ))}
          </div>
        </section>
      )}

      <section style={{ animation: "fadeIn 200ms ease 120ms both" }}>
        <div className="section-label" style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
          <span>Quick actions</span>
        </div>
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 10 }}>
          <QuickAction icon="▶" label="New session" desc="Start an unattended run" onClick={() => void navigate("/sessions")} />
          <QuickAction icon="⎇" label="Worktrees" desc="Manage git worktrees" onClick={() => void navigate("/worktrees")} />
        </div>
      </section>
    </div>
  );
}

function StatCard({ label, value, accent = false }: { label: string; value: number; accent?: boolean }) {
  return (
    <div className="card" style={{ padding: "16px 20px" }}>
      <div style={{ fontFamily: "var(--font-display)", fontSize: 28, fontWeight: 600, color: accent ? "var(--accent)" : "var(--text-primary)", letterSpacing: "-0.02em" }}>
        {value}
      </div>
      <div style={{ fontSize: 12, color: "var(--text-muted)", marginTop: 2 }}>{label}</div>
    </div>
  );
}

function QuickAction({ icon, label, desc, onClick }: { icon: string; label: string; desc: string; onClick: () => void }) {
  return (
    <button
      onClick={onClick}
      className="card"
      style={{ display: "flex", alignItems: "flex-start", gap: 12, padding: "14px 16px", cursor: "pointer", background: "var(--bg-surface)", border: "1px solid var(--border-subtle)", textAlign: "left", width: "100%" }}
      onMouseEnter={(e) => { (e.currentTarget as HTMLButtonElement).style.borderColor = "var(--border-default)"; }}
      onMouseLeave={(e) => { (e.currentTarget as HTMLButtonElement).style.borderColor = "var(--border-subtle)"; }}
    >
      <span style={{ fontSize: 18, color: "var(--accent)", flexShrink: 0, marginTop: 1 }}>{icon}</span>
      <div>
        <div style={{ fontSize: 13, fontWeight: 500, color: "var(--text-primary)" }}>{label}</div>
        <div style={{ fontSize: 11, color: "var(--text-muted)", marginTop: 2 }}>{desc}</div>
      </div>
    </button>
  );
}
