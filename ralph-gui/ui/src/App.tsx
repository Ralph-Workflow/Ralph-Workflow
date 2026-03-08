import { useEffect } from "react";
import { Provider } from "react-redux";
import { HashRouter, Routes, Route, Navigate } from "react-router-dom";
import { store } from "./store";
import { useAppDispatch, useAppSelector } from "./store";
import { initializeRepo } from "./store/slices/worktreeSlice";
import { Layout } from "./components/Layout";
import { Home } from "./pages/Home";
import { Sessions } from "./pages/Sessions";
import { Worktrees } from "./pages/Worktrees";
import { Configuration } from "./pages/Configuration";
import { RunDetail } from "./pages/RunDetail";

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

export function App() {
  return (
    <Provider store={store}>
      <HashRouter>
        <AppInitializer />
        <Layout>
          <Routes>
            <Route path="/" element={<Home />} />
            <Route path="/sessions" element={<Sessions />} />
            <Route path="/worktrees" element={<Worktrees />} />
            <Route path="/configuration" element={<Configuration />} />
            <Route path="/runs/:runId" element={<RunDetail />} />
            <Route path="*" element={<Navigate to="/" replace />} />
          </Routes>
        </Layout>
      </HashRouter>
    </Provider>
  );
}
