import { useEffect } from "react";
import { useParams, useNavigate } from "react-router-dom";
import { useAppSelector, useAppDispatch } from "../store";
import { fetchRunDetail, clearRunDetail } from "../store/slices/runSlice";
import { resumeInterruptedSession } from "../store/slices/sessionSlice";
import { RunStatusBadge } from "../components/RunStatusBadge";

interface DetailRowProps {
  label: string;
  value: string | null;
  mono?: boolean;
}

function DetailRow({ label, value, mono = false }: DetailRowProps) {
  return (
    <div
      style={{
        display: "flex",
        alignItems: "flex-start",
        gap: 16,
        padding: "10px 0",
        borderBottom: "1px solid var(--border-subtle)",
      }}
    >
      <div
        style={{
          width: 160,
          flexShrink: 0,
          fontSize: 11,
          color: "var(--text-muted)",
          fontFamily: "var(--font-mono)",
          paddingTop: 1,
        }}
      >
        {label}
      </div>
      <div
        style={{
          flex: 1,
          fontSize: mono ? 12 : 13,
          color: value ? "var(--text-primary)" : "var(--text-muted)",
          fontFamily: mono ? "var(--font-mono)" : "var(--font-ui)",
          wordBreak: "break-all",
        }}
      >
        {value ?? "—"}
      </div>
    </div>
  );
}

export function RunDetail() {
  const { runId } = useParams<{ runId: string }>();
  const navigate = useNavigate();
  const dispatch = useAppDispatch();

  const runDetail = useAppSelector((s) => s.runs.runDetail);
  const status = useAppSelector((s) => s.runs.status);
  const error = useAppSelector((s) => s.runs.error);

  useEffect(() => {
    if (runId) {
      void dispatch(fetchRunDetail(runId));
    }
    return () => {
      dispatch(clearRunDetail());
    };
  }, [dispatch, runId]);

  const handleResume = async () => {
    if (!runDetail) return;
    await dispatch(
      resumeInterruptedSession(runDetail.run_id),
    ).unwrap();
    navigate("/sessions");
  };

  const canResume =
    runDetail?.status === "Paused" || runDetail?.status === "Failed";

  if (status === "loading") {
    return (
      <div className="page-content">
        <div
          style={{
            display: "flex",
            alignItems: "center",
            gap: 12,
            marginBottom: "var(--space-6)",
            animation: "fadeIn 200ms ease",
          }}
        >
          <button className="btn btn-ghost" onClick={() => navigate(-1)}>
            ← Back
          </button>
          <h1 className="page-title" style={{ marginBottom: 0 }}>
            Run Detail
          </h1>
        </div>
        <div
          style={{
            padding: "var(--space-10)",
            textAlign: "center",
            color: "var(--text-muted)",
            fontSize: 13,
            fontFamily: "var(--font-mono)",
          }}
        >
          Loading run...
        </div>
      </div>
    );
  }

  if (error || !runDetail) {
    return (
      <div className="page-content">
        <div
          style={{
            display: "flex",
            alignItems: "center",
            gap: 12,
            marginBottom: "var(--space-6)",
            animation: "fadeIn 200ms ease",
          }}
        >
          <button className="btn btn-ghost" onClick={() => navigate(-1)}>
            ← Back
          </button>
          <h1 className="page-title" style={{ marginBottom: 0 }}>
            Run Detail
          </h1>
        </div>
        <div className="empty-state">
          <span className="empty-state-icon">⊘</span>
          <div className="empty-state-title">Run not found</div>
          <div className="empty-state-desc">
            {error ?? "This run could not be loaded."}
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
          gap: 12,
          marginBottom: "var(--space-6)",
          animation: "fadeIn 200ms ease",
        }}
      >
        <button className="btn btn-ghost" onClick={() => navigate(-1)}>
          ← Back
        </button>
        <h1 className="page-title" style={{ marginBottom: 0, flex: 1 }}>
          Run Detail
        </h1>
        <RunStatusBadge status={runDetail.status} showLabel />
        {canResume && (
          <button
            className="btn btn-primary"
            onClick={() => void handleResume()}
          >
            Resume
          </button>
        )}
      </div>

      <div style={{ animation: "fadeIn 200ms ease 40ms both" }}>
        {/* Run ID banner */}
        <div
          style={{
            padding: "10px 16px",
            background: "var(--accent-bg)",
            border: "1px solid rgba(232,168,56,0.15)",
            borderRadius: "var(--radius-md)",
            fontFamily: "var(--font-mono)",
            fontSize: 12,
            color: "var(--accent)",
            marginBottom: "var(--space-5)",
            letterSpacing: "0.02em",
          }}
        >
          {runDetail.run_id}
        </div>

        {/* Details card */}
        <div className="card" style={{ marginBottom: "var(--space-5)" }}>
          <div
            style={{
              fontFamily: "var(--font-display)",
              fontSize: 13,
              fontWeight: 600,
              color: "var(--text-secondary)",
              textTransform: "uppercase",
              letterSpacing: "0.08em",
              marginBottom: 6,
            }}
          >
            Run information
          </div>

          <DetailRow label="status" value={runDetail.status} />
          <DetailRow label="phase" value={runDetail.current_phase} mono />
          <DetailRow label="agent_profile" value={runDetail.agent_profile} mono />
          <DetailRow label="repo_path" value={runDetail.repo_path} mono />
          <DetailRow
            label="worktree_path"
            value={runDetail.worktree_path}
            mono
          />
          <DetailRow label="created_at" value={runDetail.created_at} mono />
          <DetailRow
            label="last_checkpoint"
            value={runDetail.last_checkpoint}
            mono
          />
          <DetailRow
            label="description"
            value={runDetail.description || null}
          />
        </div>

        {/* Phase timeline stub */}
        <div className="card">
          <div
            style={{
              fontFamily: "var(--font-display)",
              fontSize: 13,
              fontWeight: 600,
              color: "var(--text-secondary)",
              textTransform: "uppercase",
              letterSpacing: "0.08em",
              marginBottom: 14,
            }}
          >
            Phase
          </div>

          <div
            style={{
              display: "flex",
              alignItems: "center",
              gap: 8,
            }}
          >
            {[
              "plan",
              "develop",
              "review",
              "commit",
            ].map((phase, idx) => {
              const currentPhase = runDetail.current_phase.toLowerCase();
              const isDone = ["commit", "done", "completed"].some((p) =>
                currentPhase.includes(p),
              )
                ? true
                : currentPhase.includes(phase)
                ? false
                : idx < ["plan", "develop", "review", "commit"].indexOf(currentPhase.split("_")[0] ?? "");
              const isCurrent = currentPhase.includes(phase);
              return (
                <div
                  key={phase}
                  style={{
                    display: "flex",
                    alignItems: "center",
                    gap: 8,
                    flex: idx < 3 ? 1 : undefined,
                  }}
                >
                  <div
                    style={{
                      display: "flex",
                      flexDirection: "column",
                      alignItems: "center",
                      gap: 4,
                    }}
                  >
                    <div
                      style={{
                        width: 28,
                        height: 28,
                        borderRadius: "50%",
                        background: isCurrent
                          ? "var(--accent)"
                          : isDone
                          ? "var(--status-completed)"
                          : "var(--bg-elevated)",
                        border: isCurrent
                          ? "2px solid var(--accent)"
                          : isDone
                          ? "2px solid var(--status-completed)"
                          : "2px solid var(--border-default)",
                        display: "flex",
                        alignItems: "center",
                        justifyContent: "center",
                        fontSize: 11,
                        color: isCurrent
                          ? "#000"
                          : isDone
                          ? "#000"
                          : "var(--text-muted)",
                        fontWeight: 600,
                        transition: "all var(--transition-base)",
                      }}
                    >
                      {isDone ? "✓" : idx + 1}
                    </div>
                    <div
                      style={{
                        fontSize: 10,
                        fontFamily: "var(--font-mono)",
                        color: isCurrent
                          ? "var(--accent)"
                          : isDone
                          ? "var(--status-completed)"
                          : "var(--text-muted)",
                        textTransform: "uppercase",
                        letterSpacing: "0.05em",
                      }}
                    >
                      {phase}
                    </div>
                  </div>
                  {idx < 3 && (
                    <div
                      style={{
                        flex: 1,
                        height: 1,
                        background: isDone
                          ? "var(--status-completed)"
                          : "var(--border-subtle)",
                        marginBottom: 20,
                      }}
                    />
                  )}
                </div>
              );
            })}
          </div>
        </div>
      </div>
    </div>
  );
}
