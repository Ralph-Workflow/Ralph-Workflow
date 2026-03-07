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
  const rows: Array<{ label: string; value: string }> = [
    { label: "Repository", value: repoPath },
    { label: "Context", value: worktreePath ?? "Direct repository" },
    { label: "Prompt", value: promptPath },
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
          {rows.map((row, i) => (
            <div
              key={row.label}
              style={{
                display: "flex",
                gap: 16,
                padding: "8px 14px",
                borderBottom:
                  i < rows.length - 1
                    ? "1px solid var(--border-subtle)"
                    : "none",
              }}
            >
              <span style={{ fontSize: 12, color: "var(--text-muted)", width: 110, flexShrink: 0 }}>
                {row.label}
              </span>
              <span className="chip-mono" style={{ maxWidth: "100%", overflow: "hidden", textOverflow: "ellipsis" }}>
                {row.value}
              </span>
            </div>
          ))}
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
