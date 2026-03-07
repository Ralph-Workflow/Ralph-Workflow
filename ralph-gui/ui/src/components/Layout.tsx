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
        }}
      >
        {/* Logo / Brand */}
        <div
          style={{
            padding: "20px 16px 16px",
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
                width: 24,
                height: 24,
                background: "var(--accent)",
                borderRadius: "var(--radius-sm)",
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
                fontSize: 12,
                fontWeight: 700,
                color: "#000",
                flexShrink: 0,
                fontFamily: "var(--font-mono)",
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
              paddingLeft: 32,
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
