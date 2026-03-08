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
          <div style={{
            fontSize: 40,
            marginBottom: 20,
            opacity: 0.15,
            fontFamily: "var(--font-mono)",
            color: "var(--accent)",
            letterSpacing: "-0.05em",
          }}>◈</div>
          <h1 style={{ fontFamily: "var(--font-display)", fontSize: 24, fontWeight: 600, color: "var(--text-primary)", letterSpacing: "-0.02em", marginBottom: 10 }}>
            Welcome to Ralph Workflow
          </h1>
          <p style={{ fontSize: 13, color: "var(--text-secondary)", lineHeight: 1.7, marginBottom: 28 }}>
            An unattended AI orchestration platform for long-running development and review cycles.
          </p>
          <div style={{ textAlign: "left", marginBottom: 16 }}>
            <div style={{ fontSize: 11, color: "var(--text-muted)", fontFamily: "var(--font-mono)", marginBottom: 8, textTransform: "uppercase", letterSpacing: "0.06em" }}>
              Enter your repository path to get started
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
    <div
      className="card"
      style={{
        padding: "18px 20px",
        borderBottom: accent && value > 0 ? "2px solid var(--accent)" : "2px solid transparent",
      }}
    >
      <div style={{
        fontFamily: "var(--font-display)",
        fontSize: 32,
        fontWeight: 700,
        color: accent && value > 0 ? "var(--accent)" : "var(--text-primary)",
        letterSpacing: "-0.03em",
        lineHeight: 1,
        marginBottom: 6,
        textShadow: accent && value > 0 ? "0 0 20px var(--accent-glow)" : "none",
      }}>
        {value}
      </div>
      <div style={{ fontSize: 11, color: "var(--text-muted)", fontFamily: "var(--font-ui)", textTransform: "uppercase", letterSpacing: "0.06em" }}>{label}</div>
    </div>
  );
}

function QuickAction({ icon, label, desc, onClick }: { icon: string; label: string; desc: string; onClick: () => void }) {
  return (
    <button
      onClick={onClick}
      className="card"
      style={{
        display: "flex",
        alignItems: "flex-start",
        gap: 12,
        padding: "14px 16px",
        cursor: "pointer",
        background: "var(--bg-surface)",
        border: "1px solid var(--border-subtle)",
        textAlign: "left",
        width: "100%",
        transition: "border-color var(--transition-fast), background var(--transition-fast)",
      }}
      onMouseEnter={(e) => {
        const btn = e.currentTarget as HTMLButtonElement;
        btn.style.borderColor = "var(--border-default)";
        btn.style.background = "var(--bg-elevated)";
      }}
      onMouseLeave={(e) => {
        const btn = e.currentTarget as HTMLButtonElement;
        btn.style.borderColor = "var(--border-subtle)";
        btn.style.background = "var(--bg-surface)";
      }}
    >
      <span style={{ fontSize: 16, color: "var(--accent)", flexShrink: 0, marginTop: 2, opacity: 0.9 }}>{icon}</span>
      <div>
        <div style={{ fontSize: 13, fontWeight: 500, color: "var(--text-primary)", marginBottom: 2 }}>{label}</div>
        <div style={{ fontSize: 11, color: "var(--text-muted)", lineHeight: 1.5 }}>{desc}</div>
      </div>
    </button>
  );
}
