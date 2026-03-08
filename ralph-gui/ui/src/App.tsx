import { useEffect } from "react";
import { Provider } from "react-redux";
import { HashRouter, Routes, Route, Navigate } from "react-router-dom";
import { store } from "./store";
import { useAppDispatch, useAppSelector } from "./store";
import { initializeRepo } from "./store/slices/worktreeSlice";
import { Layout } from "./components/Layout";
import { ErrorBoundary } from "./components/ErrorBoundary";
import { Home } from "./pages/Home";
import { Sessions } from "./pages/Sessions";
import { Worktrees } from "./pages/Worktrees";
import { Configuration } from "./pages/Configuration";
import { RunDetail } from "./pages/RunDetail";
import { useKeyboardShortcuts } from "./hooks/useKeyboardShortcuts";
import { KeyboardShortcutsHelp } from "./components/KeyboardShortcutsHelp";
import { useState } from "react";
import { useNavigate } from "react-router-dom";

/// Initializes the repo context from the persisted last-used path on startup.
/// Only dispatches if worktrees haven't been loaded yet and a path is known.
export function AppInitializer() {
  const dispatch = useAppDispatch();
  const worktrees = useAppSelector((s) => s.worktrees.worktrees);
  const status = useAppSelector((s) => s.worktrees.status);
  const lastRepoPath = useAppSelector((s) => s.worktrees.lastRepoPath);

  useEffect(() => {
    if (lastRepoPath && worktrees.length === 0 && status === "idle") {
      void dispatch(initializeRepo(lastRepoPath));
    }
  }, [dispatch, lastRepoPath, worktrees.length, status]);

  return null;
}

/// Inner app with routing context, keyboard shortcuts, and error boundaries per page.
function AppRoutes() {
  const navigate = useNavigate();
  const [helpOpen, setHelpOpen] = useState(false);

  useKeyboardShortcuts({
    onHelpOpen: () => {
      setHelpOpen(true);
    },
    onNavigate: navigate,
  });

  return (
    <>
      <AppInitializer />
      <Layout>
        <Routes>
          <Route
            path="/"
            element={
              <ErrorBoundary>
                <Home />
              </ErrorBoundary>
            }
          />
          <Route
            path="/sessions"
            element={
              <ErrorBoundary>
                <Sessions />
              </ErrorBoundary>
            }
          />
          <Route
            path="/worktrees"
            element={
              <ErrorBoundary>
                <Worktrees />
              </ErrorBoundary>
            }
          />
          <Route
            path="/configuration"
            element={
              <ErrorBoundary>
                <Configuration />
              </ErrorBoundary>
            }
          />
          <Route
            path="/runs/:runId"
            element={
              <ErrorBoundary>
                <RunDetail />
              </ErrorBoundary>
            }
          />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </Layout>
      {helpOpen && (
        <KeyboardShortcutsHelp
          onClose={() => {
            setHelpOpen(false);
          }}
        />
      )}
    </>
  );
}

export function App() {
  return (
    <Provider store={store}>
      <HashRouter>
        <AppRoutes />
      </HashRouter>
    </Provider>
  );
}
