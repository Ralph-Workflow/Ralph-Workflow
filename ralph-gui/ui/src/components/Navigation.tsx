import { NavLink } from "react-router-dom";

interface NavItem {
  path: string;
  label: string;
  icon: string;
}

const NAV_ITEMS: NavItem[] = [
  { path: "/",            label: "Home",          icon: "⌂" },
  { path: "/sessions",    label: "Sessions",      icon: "▶" },
  { path: "/worktrees",   label: "Worktrees",     icon: "⎇" },
  { path: "/configuration", label: "Configuration", icon: "⚙" },
];

export function Navigation() {
  return (
    <nav
      aria-label="Main navigation"
      style={{
        display: "flex",
        flexDirection: "column",
        gap: 2,
        padding: "0 8px",
      }}
    >
      {NAV_ITEMS.map((item) => (
        <NavLink
          key={item.path}
          to={item.path}
          end={item.path === "/"}
          style={({ isActive }) => ({
            display: "flex",
            alignItems: "center",
            gap: 10,
            padding: "7px 10px",
            borderRadius: "var(--radius-md)",
            fontFamily: "var(--font-ui)",
            fontSize: 13,
            fontWeight: isActive ? 500 : 400,
            color: isActive ? "var(--text-primary)" : "var(--text-secondary)",
            background: isActive ? "var(--bg-elevated)" : "transparent",
            border: isActive
              ? "1px solid var(--border-default)"
              : "1px solid transparent",
            textDecoration: "none",
            transition: "all var(--transition-fast)",
            cursor: "pointer",
          })}
          onMouseEnter={(e) => {
            const el = e.currentTarget;
            if (!el.getAttribute("aria-current")) {
              el.style.color = "var(--text-primary)";
            }
          }}
          onMouseLeave={(e) => {
            const el = e.currentTarget;
            if (!el.getAttribute("aria-current")) {
              el.style.color = "var(--text-secondary)";
            }
          }}
        >
          {({ isActive }) => (
            <>
              <span
                style={{
                  fontSize: 14,
                  width: 18,
                  textAlign: "center",
                  color: isActive ? "var(--accent)" : "var(--text-muted)",
                  flexShrink: 0,
                }}
              >
                {item.icon}
              </span>
              <span>{item.label}</span>
            </>
          )}
        </NavLink>
      ))}
    </nav>
  );
}
