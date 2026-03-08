interface RunLogProps {
  lines: string[];
  isLoading?: boolean;
  "aria-label"?: string;
}

export function RunLog({
  lines,
  isLoading = false,
  "aria-label": ariaLabel,
}: RunLogProps) {
  if (isLoading) {
    return (
      <div
        data-testid="run-log-loading"
        aria-busy="true"
        style={{
          padding: "8px 0",
          fontSize: 11,
          color: "var(--text-muted)",
          fontFamily: "var(--font-mono)",
        }}
      >
        Loading logs…
      </div>
    );
  }

  if (lines.length === 0) {
    return (
      <div
        data-testid="run-log-empty"
        role="status"
        style={{
          padding: "8px 0",
          fontSize: 11,
          color: "var(--text-muted)",
          fontFamily: "var(--font-mono)",
        }}
      >
        No log output yet.
      </div>
    );
  }

  return (
    <pre
      data-testid="run-log-content"
      role="log"
      aria-label={ariaLabel ?? "Run log output"}
      aria-live="polite"
      style={{
        overflowY: "auto",
        maxHeight: 320,
        fontFamily: "var(--font-mono)",
        fontSize: 11,
        lineHeight: 1.5,
        color: "var(--text-secondary)",
        background: "var(--bg-base)",
        border: "1px solid var(--border-subtle)",
        borderRadius: "var(--radius-md)",
        padding: "10px 12px",
        margin: 0,
        whiteSpace: "pre-wrap",
        wordBreak: "break-all",
      }}
    >
      {lines.join("\n")}
    </pre>
  );
}
