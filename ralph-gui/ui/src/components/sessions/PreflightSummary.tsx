interface PreflightSummaryProps {
  repoPath: string;
  worktreePath: string | null;
  promptPath: string;
  developerIterations: number;
  reviewerPasses: number;
  onConfirm: () => void;
  onBack: () => void;
  isLaunching: boolean;
}

export function PreflightSummary({
  repoPath,
  worktreePath,
  promptPath,
  developerIterations,
  reviewerPasses,
  onConfirm,
  onBack,
  isLaunching,
}: PreflightSummaryProps) {
  const contextRows: Array<{ label: string; value: string; important?: boolean }> = [
    { label: "Repository", value: repoPath, important: true },
    { label: "Context", value: worktreePath ?? "Direct repository", important: true },
    { label: "Prompt", value: promptPath },
  ];

  const configRows: Array<{ label: string; value: string }> = [
    { label: "Dev iterations", value: `${developerIterations}` },
    { label: "Review passes", value: `${reviewerPasses}` },
  ];

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 20 }}>
      <div>
        <div className="section-label">Pre-flight summary</div>
        <div
          style={{
            background: "var(--bg-elevated)",
            border: "1px solid var(--border-default)",
            borderRadius: "var(--radius-md)",
            overflow: "hidden",
          }}
        >
          {/* Context rows — given visual prominence */}
          {contextRows.map((row) => (
            <div
              key={row.label}
              style={{
                display: "flex",
                gap: 16,
                padding: row.important ? "10px 14px" : "8px 14px",
                borderBottom: "1px solid var(--border-subtle)",
                background: row.important ? "var(--bg-surface)" : undefined,
              }}
            >
              <span
                style={{
                  fontSize: 12,
                  color: row.important ? "var(--text-secondary)" : "var(--text-muted)",
                  width: 110,
                  flexShrink: 0,
                  fontWeight: row.important ? 500 : 400,
                }}
              >
                {row.label}
              </span>
              <span
                className="chip-mono"
                style={{
                  maxWidth: "100%",
                  overflow: "hidden",
                  textOverflow: "ellipsis",
                  color: row.important ? "var(--text-primary)" : undefined,
                }}
              >
                {row.value}
              </span>
            </div>
          ))}
          {/* Config rows — two-column grid */}
          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr" }}>
            {configRows.map((row, i) => (
              <div
                key={row.label}
                style={{
                  display: "flex",
                  flexDirection: "column",
                  gap: 4,
                  padding: "10px 14px",
                  borderRight: i === 0 ? "1px solid var(--border-subtle)" : "none",
                }}
              >
                <span style={{ fontSize: 11, color: "var(--text-muted)" }}>
                  {row.label}
                </span>
                <span
                  className="chip-mono"
                  style={{ fontSize: 14, fontWeight: 600, color: "var(--accent)" }}
                >
                  {row.value}
                </span>
              </div>
            ))}
          </div>
        </div>
      </div>

      <div
        style={{
          padding: 12,
          background: "var(--accent-bg)",
          border: "1px solid var(--accent-dim)40",
          borderRadius: "var(--radius-md)",
          fontSize: 12,
          color: "var(--text-secondary)",
          lineHeight: 1.6,
        }}
      >
        This will launch an unattended Ralph session. The pipeline will run autonomously. You can monitor progress from the Run Dashboard.
      </div>

      <div style={{ display: "flex", gap: 8, justifyContent: "flex-end" }}>
        <button className="btn btn-secondary" onClick={onBack} disabled={isLaunching}>
          Back
        </button>
        <button
          className="btn btn-primary"
          onClick={onConfirm}
          disabled={isLaunching}
        >
          {isLaunching ? "Launching…" : "Launch session"}
        </button>
      </div>
    </div>
  );
}
