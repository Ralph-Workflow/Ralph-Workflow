import { useEffect } from "react";
import { useParams, useNavigate } from "react-router-dom";
import { useAppSelector, useAppDispatch } from "../store";
import {
  fetchRunDetail,
  clearRunDetail,
  startPollingInterval,
  stopPollingInterval,
} from "../store/slices/runSlice";
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
  const pollingStatus = useAppSelector((s) => s.runs.pollingStatus);

  const detailRunId = runDetail?.run_id ?? null;
  const detailStatus = runDetail?.status ?? null;
  const detailRepoPath = runDetail?.repo_path ?? null;
  const detailWorktreePath = runDetail?.worktree_path ?? null;

  useEffect(() => {
    if (runId) {
      void dispatch(fetchRunDetail(runId));
    }
    return () => {
      dispatch(clearRunDetail());
    };
  }, [dispatch, runId]);

  useEffect(() => {
    if (!detailStatus || detailStatus !== "Running" || !detailRepoPath) return;
    stopPollingInterval();
    startPollingInterval(
      (action) => {
        void dispatch(action);
      },
      detailRepoPath,
      detailWorktreePath,
    );
    return () => {
      stopPollingInterval();
    };
  }, [dispatch, detailRunId, detailStatus, detailRepoPath, detailWorktreePath]);

  useEffect(() => {
    if (pollingStatus && runId) {
      void dispatch(fetchRunDetail(runId));
    }
  }, [dispatch, pollingStatus, runId]);

  const handleResume = async () => {
    if (!runDetail) return;
    await dispatch(
      resumeInterruptedSession({
        runId: runDetail.run_id,
        repoPath: runDetail.repo_path,
      }),
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
        <RunStatusBadge status={runDetail.status} showLabel isDegraded={runDetail.is_degraded} />
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

        {/* Degraded condition banner */}
        {runDetail.is_degraded && (
          <div
            style={{
              padding: "10px 16px",
              background: "var(--status-degraded-bg)",
              border: "1px solid var(--status-degraded-border)",
              borderRadius: "var(--radius-md)",
              color: "var(--status-degraded)",
              fontSize: 12,
              fontFamily: "var(--font-mono)",
              marginBottom: "var(--space-4)",
              display: "flex",
              alignItems: "center",
              gap: 10,
            }}
            data-testid="degraded-banner"
          >
            <span style={{ fontSize: 15, flexShrink: 0 }}>⚠</span>
            <span>
              <strong style={{ fontWeight: 600 }}>Degraded conditions</strong>
              {" "}— retries exceeded or fallback agent active. Monitor closely.
            </span>
          </div>
        )}

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
            label="iteration_count"
            value={String(runDetail.iteration_count ?? 0)}
            mono
          />
          {runDetail.last_error != null && (
            <DetailRow label="last_error" value={runDetail.last_error} mono />
          )}
          <DetailRow
            label="description"
            value={runDetail.description || null}
          />
        </div>

        {/* Phase timeline */}
        <div className="card">
          <div
            style={{
              fontFamily: "var(--font-display)",
              fontSize: 13,
              fontWeight: 600,
              color: "var(--text-secondary)",
              textTransform: "uppercase",
              letterSpacing: "0.08em",
              marginBottom: 20,
            }}
          >
            Phase
          </div>

          <div
            className="phase-timeline"
            style={{ alignItems: "flex-start" }}
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

              let dotBg = "var(--bg-elevated)";
              let dotBorder = "2px solid var(--border-default)";
              let dotColor = "var(--text-muted)";
              let labelColor = "var(--text-muted)";

              if (isCurrent) {
                dotBg = "var(--accent)";
                dotBorder = "2px solid var(--accent)";
                dotColor = "#000";
                labelColor = "var(--accent)";
              } else if (isDone) {
                dotBg = "var(--status-completed)";
                dotBorder = "2px solid var(--status-completed)";
                dotColor = "#000";
                labelColor = "var(--status-completed)";
              }

              return (
                <div
                  key={phase}
                  style={{
                    display: "flex",
                    alignItems: "flex-start",
                    flex: idx < 3 ? 1 : undefined,
                  }}
                >
                  <div className="phase-node">
                    <div
                      className="phase-node__dot"
                      style={{
                        background: dotBg,
                        border: dotBorder,
                        color: dotColor,
                        boxShadow: isCurrent ? "0 0 12px var(--accent-glow)" : "none",
                      }}
                    >
                      {isDone ? "✓" : idx + 1}
                    </div>
                    <div
                      className="phase-node__label"
                      style={{ color: labelColor }}
                    >
                      {phase}
                    </div>
                  </div>
                  {idx < 3 && (
                    <div
                      className="phase-connector"
                      style={{
                        background: isDone
                          ? "var(--status-completed)"
                          : isCurrent
                          ? "linear-gradient(90deg, var(--accent) 0%, var(--border-subtle) 100%)"
                          : "var(--border-subtle)",
                        opacity: isDone ? 1 : 0.5,
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
