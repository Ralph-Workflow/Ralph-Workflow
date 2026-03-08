import { Component, type ErrorInfo, type ReactNode } from "react";

interface ErrorBoundaryProps {
  children: ReactNode;
}

interface ErrorBoundaryState {
  hasError: boolean;
  error: Error | null;
  errorInfo: ErrorInfo | null;
}

/**
 * React class-based error boundary.
 *
 * Wraps page routes so a runtime exception in one page does not crash the
 * entire application. Shows a fallback UI with:
 *   - A human-readable error description
 *   - A "Reload page" button that resets the boundary state
 *   - An expandable stack trace for diagnostics
 *
 * NOTE: In Vite HMR dev mode, React will catch errors here as well. The
 * "Reload page" button resets boundary state; a full browser reload is
 * available via the browser controls. This is harmless in production builds.
 */
export class ErrorBoundary extends Component<
  ErrorBoundaryProps,
  ErrorBoundaryState
> {
  constructor(props: ErrorBoundaryProps) {
    super(props);
    this.state = { hasError: false, error: null, errorInfo: null };
  }

  static getDerivedStateFromError(error: Error): Partial<ErrorBoundaryState> {
    return { hasError: true, error };
  }

  override componentDidCatch(error: Error, errorInfo: ErrorInfo): void {
    console.error("ErrorBoundary caught an error:", error, errorInfo);
    this.setState({ errorInfo });
  }

  handleReset = (): void => {
    this.setState({ hasError: false, error: null, errorInfo: null });
  };

  override render(): ReactNode {
    if (!this.state.hasError) {
      return this.props.children;
    }

    const { error, errorInfo } = this.state;

    return (
      <div
        style={{
          display: "flex",
          flexDirection: "column",
          alignItems: "center",
          justifyContent: "center",
          minHeight: "60vh",
          padding: "var(--space-10)",
          textAlign: "center",
        }}
      >
        {/* Error icon */}
        <div
          style={{
            fontSize: 40,
            marginBottom: "var(--space-4)",
            color: "var(--status-failed)",
          }}
        >
          ⊘
        </div>

        {/* Title */}
        <h2
          style={{
            fontFamily: "var(--font-display)",
            fontSize: 22,
            fontWeight: 700,
            color: "var(--text-primary)",
            marginBottom: "var(--space-3)",
          }}
        >
          Something went wrong
        </h2>

        {/* Error message */}
        <p
          style={{
            fontSize: 13,
            color: "var(--text-secondary)",
            fontFamily: "var(--font-mono)",
            marginBottom: "var(--space-6)",
            maxWidth: 480,
          }}
        >
          {error?.message ?? "An unexpected error occurred in this page."}
        </p>

        {/* Reload button */}
        <button
          className="btn btn-primary"
          onClick={this.handleReset}
          style={{ marginBottom: "var(--space-6)" }}
        >
          Reload page
        </button>

        {/* Expandable stack trace */}
        <details
          style={{
            maxWidth: 640,
            width: "100%",
            textAlign: "left",
          }}
        >
          <summary
            style={{
              fontSize: 11,
              color: "var(--text-muted)",
              fontFamily: "var(--font-mono)",
              cursor: "pointer",
              marginBottom: "var(--space-2)",
            }}
          >
            Stack trace
          </summary>
          <pre
            style={{
              fontSize: 11,
              color: "var(--text-muted)",
              fontFamily: "var(--font-mono)",
              background: "var(--bg-elevated)",
              border: "1px solid var(--border-subtle)",
              borderRadius: "var(--radius-md)",
              padding: "var(--space-4)",
              overflow: "auto",
              whiteSpace: "pre-wrap",
              wordBreak: "break-all",
            }}
          >
            {error?.stack ?? "No stack trace available."}
            {errorInfo?.componentStack
              ? `\n\nComponent stack:${errorInfo.componentStack}`
              : ""}
          </pre>
        </details>
      </div>
    );
  }
}
