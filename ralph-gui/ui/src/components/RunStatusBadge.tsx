import type { RunStatus } from "../types";

interface RunStatusBadgeProps {
  status: RunStatus;
  showLabel?: boolean;
  size?: "sm" | "md";
}

const STATUS_CONFIG: Record<
  RunStatus,
  { label: string; color: string; bg: string; pulse: boolean; description: string }
> = {
  Running: {
    label: "Running",
    color: "var(--status-running)",
    bg: "var(--status-running-bg)",
    pulse: true,
    description: "Pipeline is actively executing",
  },
  Paused: {
    label: "Paused",
    color: "var(--status-paused)",
    bg: "var(--status-paused-bg)",
    pulse: false,
    description: "Pipeline is interrupted and can be resumed",
  },
  Completed: {
    label: "Completed",
    color: "var(--status-completed)",
    bg: "var(--status-completed-bg)",
    pulse: false,
    description: "Pipeline completed successfully",
  },
  Failed: {
    label: "Failed",
    color: "var(--status-failed)",
    bg: "var(--status-failed-bg)",
    pulse: false,
    description: "Pipeline encountered an unrecoverable error",
  },
  NotStarted: {
    label: "Not Started",
    color: "var(--status-idle)",
    bg: "transparent",
    pulse: false,
    description: "No active pipeline in this repository",
  },
};

export function RunStatusBadge({
  status,
  showLabel = true,
  size = "md",
}: RunStatusBadgeProps) {
  const cfg = STATUS_CONFIG[status];
  const dotSize = size === "sm" ? 6 : 8;

  return (
    <span
      title={cfg.description}
      style={{
        display: "inline-flex",
        alignItems: "center",
        gap: 6,
        padding:
          showLabel
            ? size === "sm"
              ? "2px 8px"
              : "3px 10px"
            : "3px",
        borderRadius: 100,
        background: cfg.bg,
        border: `1px solid ${cfg.color}30`,
        fontSize: size === "sm" ? 11 : 12,
        fontWeight: 500,
        color: cfg.color,
        whiteSpace: "nowrap",
        userSelect: "none",
      }}
    >
      <span
        style={{
          width: dotSize,
          height: dotSize,
          borderRadius: "50%",
          background: cfg.color,
          flexShrink: 0,
          animation: cfg.pulse
            ? "pulse-dot 1.4s ease-in-out infinite"
            : undefined,
        }}
      />
      {showLabel && <span>{cfg.label}</span>}
    </span>
  );
}
