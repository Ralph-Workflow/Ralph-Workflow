import { useEffect } from "react";

interface KeyboardShortcutsHelpProps {
  onClose: () => void;
}

interface ShortcutEntry {
  keys: string;
  description: string;
}

interface ShortcutGroup {
  category: string;
  shortcuts: ShortcutEntry[];
}

const SHORTCUT_GROUPS: ShortcutGroup[] = [
  {
    category: "Navigation",
    shortcuts: [
      { keys: "g + h", description: "Go to Home" },
      { keys: "g + s", description: "Go to Sessions" },
      { keys: "g + w", description: "Go to Worktrees" },
      { keys: "g + c", description: "Go to Configuration" },
    ],
  },
  {
    category: "General",
    shortcuts: [
      { keys: "?", description: "Open keyboard shortcuts help" },
      { keys: "⌘N / Ctrl+N", description: "Open new session wizard" },
      { keys: "Escape", description: "Dismiss overlay or modal" },
    ],
  },
];

function Kbd({ children }: { children: string }) {
  return (
    <kbd
      style={{
        display: "inline-flex",
        alignItems: "center",
        justifyContent: "center",
        padding: "2px 6px",
        background: "var(--bg-elevated)",
        border: "1px solid var(--border-default)",
        borderRadius: "var(--radius-sm)",
        fontFamily: "var(--font-mono)",
        fontSize: 11,
        color: "var(--text-secondary)",
        whiteSpace: "nowrap",
      }}
    >
      {children}
    </kbd>
  );
}

/**
 * Keyboard shortcuts help overlay.
 *
 * Displayed when the user presses "?" and dismissed with Escape or the close button.
 * Lists all global shortcuts grouped by category.
 */
export function KeyboardShortcutsHelp({ onClose }: KeyboardShortcutsHelpProps) {
  // Dismiss on Escape key
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        onClose();
      }
    };
    document.addEventListener("keydown", handleKeyDown);
    return () => {
      document.removeEventListener("keydown", handleKeyDown);
    };
  }, [onClose]);

  return (
    // Backdrop
    <div
      role="dialog"
      aria-modal="true"
      aria-label="Keyboard shortcuts"
      onClick={onClose}
      style={{
        position: "fixed",
        inset: 0,
        background: "rgba(8,10,12,0.7)",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        zIndex: 9000,
        animation: "fadeIn 150ms ease",
      }}
    >
      {/* Panel */}
      <div
        onClick={(e) => {
          e.stopPropagation();
        }}
        style={{
          background: "var(--bg-surface)",
          border: "1px solid var(--border-default)",
          borderRadius: "var(--radius-lg)",
          boxShadow: "var(--shadow-lg)",
          width: 480,
          maxWidth: "90vw",
          maxHeight: "80vh",
          overflow: "auto",
          padding: "var(--space-6)",
          animation: "fadeIn 150ms ease",
        }}
      >
        {/* Header */}
        <div
          style={{
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
            marginBottom: "var(--space-5)",
          }}
        >
          <h2
            style={{
              fontFamily: "var(--font-display)",
              fontSize: 16,
              fontWeight: 700,
              color: "var(--text-primary)",
              margin: 0,
            }}
          >
            Keyboard Shortcuts
          </h2>
          <button
            className="btn btn-ghost"
            onClick={onClose}
            aria-label="Close"
            style={{ padding: "4px 10px", fontSize: 12 }}
          >
            Close
          </button>
        </div>

        {/* Shortcut groups */}
        {SHORTCUT_GROUPS.map((group) => (
          <div key={group.category} style={{ marginBottom: "var(--space-5)" }}>
            <div
              style={{
                fontFamily: "var(--font-display)",
                fontSize: 11,
                fontWeight: 600,
                color: "var(--text-muted)",
                textTransform: "uppercase",
                letterSpacing: "0.08em",
                marginBottom: "var(--space-3)",
              }}
            >
              {group.category}
            </div>
            <div
              style={{
                display: "flex",
                flexDirection: "column",
                gap: "var(--space-2)",
              }}
            >
              {group.shortcuts.map((s) => (
                <div
                  key={s.keys}
                  style={{
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "space-between",
                    padding: "6px 0",
                    borderBottom: "1px solid var(--border-subtle)",
                  }}
                >
                  <span
                    style={{
                      fontSize: 12,
                      color: "var(--text-secondary)",
                    }}
                  >
                    {s.description}
                  </span>
                  <Kbd>{s.keys}</Kbd>
                </div>
              ))}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
