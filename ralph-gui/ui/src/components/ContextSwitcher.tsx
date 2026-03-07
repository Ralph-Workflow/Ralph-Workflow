import { useState, useRef, useEffect } from "react";
import { useAppDispatch, useAppSelector } from "../store";
import { switchActiveContext, setActiveWorktree } from "../store/slices/worktreeSlice";

export function ContextSwitcher() {
  const dispatch = useAppDispatch();
  const worktrees = useAppSelector((s) => s.worktrees.worktrees);
  const activePath = useAppSelector((s) => s.worktrees.activeWorktreePath);
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    function handleClick(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) {
        setOpen(false);
      }
    }
    document.addEventListener("mousedown", handleClick);
    return () => document.removeEventListener("mousedown", handleClick);
  }, []);

  const mainWorktree = worktrees.find((wt) => wt.is_main);
  const linkedWorktrees = worktrees.filter((wt) => !wt.is_main);

  const activeWorktree = activePath
    ? worktrees.find((wt) => wt.path === activePath)
    : mainWorktree;

  const contextLabel = activeWorktree
    ? activeWorktree.is_main
      ? "direct repo"
      : activeWorktree.name
    : "no context";

  const contextBranch = activeWorktree?.branch ?? "";

  function handleSelect(path: string | null) {
    const repoPath = mainWorktree?.path ?? "";
    if (!repoPath) return;
    void dispatch(switchActiveContext({ repoPath, worktreePath: path }));
    dispatch(setActiveWorktree(path));
    setOpen(false);
  }

  return (
    <div ref={ref} style={{ position: "relative" }}>
      <button
        onClick={() => setOpen((o) => !o)}
        style={{
          width: "100%",
          display: "flex",
          flexDirection: "column",
          alignItems: "flex-start",
          gap: 2,
          padding: "8px 10px",
          background: open ? "var(--bg-overlay)" : "var(--bg-elevated)",
          border: "1px solid var(--border-default)",
          borderRadius: "var(--radius-md)",
          cursor: "pointer",
          transition: "all var(--transition-fast)",
        }}
      >
        <span style={{ fontSize: 10, color: "var(--text-muted)", fontWeight: 500, textTransform: "uppercase", letterSpacing: "0.06em" }}>
          Context
        </span>
        <span style={{ fontFamily: "var(--font-mono)", fontSize: 12, color: "var(--text-primary)", fontWeight: 500 }}>
          {contextLabel}
        </span>
        {contextBranch && (
          <span style={{ fontFamily: "var(--font-mono)", fontSize: 10, color: "var(--text-secondary)" }}>
            {contextBranch}
          </span>
        )}
      </button>

      {open && (
        <div
          style={{
            position: "absolute",
            top: "calc(100% + 4px)",
            left: 0,
            right: 0,
            zIndex: 100,
            background: "var(--bg-overlay)",
            border: "1px solid var(--border-default)",
            borderRadius: "var(--radius-md)",
            boxShadow: "var(--shadow-lg)",
            overflow: "hidden",
            animation: "fadeIn 120ms ease",
          }}
        >
          {/* Direct repo option */}
          <ContextOption
            label="Direct repository"
            sublabel={mainWorktree?.branch ?? "main"}
            active={activePath === null}
            onClick={() => handleSelect(null)}
            badge="repo"
          />

          {linkedWorktrees.length > 0 && (
            <>
              <div style={{ height: 1, background: "var(--border-subtle)", margin: "2px 0" }} />
              <div style={{ padding: "4px 10px 2px", fontSize: 10, color: "var(--text-muted)", fontWeight: 600, textTransform: "uppercase", letterSpacing: "0.06em" }}>
                Worktrees
              </div>
              {linkedWorktrees.map((wt) => (
                <ContextOption
                  key={wt.path}
                  label={wt.name}
                  sublabel={wt.branch}
                  active={activePath === wt.path}
                  onClick={() => handleSelect(wt.path)}
                  badge={wt.has_active_run ? "active" : "wt"}
                />
              ))}
            </>
          )}
        </div>
      )}
    </div>
  );
}

function ContextOption({
  label,
  sublabel,
  active,
  onClick,
  badge,
}: {
  label: string;
  sublabel: string;
  active: boolean;
  onClick: () => void;
  badge: string;
}) {
  return (
    <button
      onClick={onClick}
      style={{
        width: "100%",
        display: "flex",
        alignItems: "center",
        gap: 8,
        padding: "7px 10px",
        background: active ? "var(--accent-bg)" : "transparent",
        border: "none",
        cursor: "pointer",
        textAlign: "left",
        transition: "background var(--transition-fast)",
      }}
      onMouseEnter={(e) => {
        if (!active)
          (e.currentTarget as HTMLButtonElement).style.background =
            "var(--bg-base)";
      }}
      onMouseLeave={(e) => {
        if (!active)
          (e.currentTarget as HTMLButtonElement).style.background = "transparent";
      }}
    >
      <span
        style={{
          width: 6,
          height: 6,
          borderRadius: "50%",
          background: active
            ? "var(--accent)"
            : badge === "active"
            ? "var(--status-running)"
            : "var(--border-strong)",
          flexShrink: 0,
        }}
      />
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ fontFamily: "var(--font-mono)", fontSize: 12, color: active ? "var(--accent)" : "var(--text-primary)", fontWeight: 500, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
          {label}
        </div>
        <div style={{ fontFamily: "var(--font-mono)", fontSize: 10, color: "var(--text-muted)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
          {sublabel}
        </div>
      </div>
    </button>
  );
}
