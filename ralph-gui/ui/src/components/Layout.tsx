import type { ReactNode } from "react";
import { Navigation } from "./Navigation";
import { ContextSwitcher } from "./ContextSwitcher";

interface LayoutProps {
  children: ReactNode;
}

export function Layout({ children }: LayoutProps) {
  return (
    <div
      style={{
        display: "flex",
        height: "100vh",
        width: "100vw",
        overflow: "hidden",
        background: "var(--bg-base)",
      }}
    >
      {/* Sidebar */}
      <aside
        style={{
          width: "var(--sidebar-width)",
          minWidth: "var(--sidebar-width)",
          height: "100%",
          background: "var(--sidebar-bg)",
          borderRight: "1px solid var(--border-subtle)",
          display: "flex",
          flexDirection: "column",
          overflow: "hidden",
          position: "relative",
        }}
      >
        {/* Amber top accent stripe */}
        <div
          style={{
            position: "absolute",
            top: 0,
            left: 0,
            right: 0,
            height: 2,
            background: "linear-gradient(90deg, var(--accent) 0%, transparent 100%)",
            opacity: 0.7,
            pointerEvents: "none",
          }}
        />

        {/* Logo / Brand */}
        <div
          style={{
            padding: "22px 16px 16px",
            borderBottom: "1px solid var(--border-subtle)",
          }}
        >
          <div
            style={{
              display: "flex",
              alignItems: "center",
              gap: 8,
              marginBottom: 2,
            }}
          >
            <div
              style={{
                width: 26,
                height: 26,
                background: "var(--accent)",
                borderRadius: "var(--radius-sm)",
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
                fontSize: 13,
                fontWeight: 700,
                color: "#000",
                flexShrink: 0,
                fontFamily: "var(--font-mono)",
                boxShadow: "0 0 10px var(--accent-glow)",
              }}
            >
              R
            </div>
            <span
              style={{
                fontFamily: "var(--font-display)",
                fontSize: 15,
                fontWeight: 600,
                color: "var(--text-primary)",
                letterSpacing: "-0.02em",
              }}
            >
              Ralph
            </span>
          </div>
          <div
            style={{
              fontFamily: "var(--font-mono)",
              fontSize: 10,
              color: "var(--text-muted)",
              paddingLeft: 34,
              letterSpacing: "0.08em",
              textTransform: "uppercase",
            }}
          >
            workflow
          </div>
        </div>

        {/* Context Switcher */}
        <div style={{ padding: "12px 8px 8px" }}>
          <ContextSwitcher />
        </div>

        <div style={{ height: 1, background: "var(--border-subtle)", margin: "0 8px" }} />

        {/* Navigation */}
        <div style={{ flex: 1, padding: "8px 0", overflow: "auto" }}>
          <Navigation />
        </div>

        {/* Footer */}
        <div
          style={{
            padding: "12px 16px",
            borderTop: "1px solid var(--border-subtle)",
            fontSize: 10,
            color: "var(--text-muted)",
            fontFamily: "var(--font-mono)",
            letterSpacing: "0.04em",
          }}
        >
          v0.1.0
        </div>
      </aside>

      {/* Main content */}
      <main
        style={{
          flex: 1,
          height: "100%",
          overflow: "hidden",
          display: "flex",
          flexDirection: "column",
        }}
      >
        {children}
      </main>
    </div>
  );
}
