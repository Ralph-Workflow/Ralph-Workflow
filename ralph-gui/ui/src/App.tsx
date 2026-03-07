import { Provider } from "react-redux";
import { HashRouter, Routes, Route, Navigate } from "react-router-dom";
import { store } from "./store";
import { Layout } from "./components/Layout";
import { Home } from "./pages/Home";
import { Sessions } from "./pages/Sessions";
import { Worktrees } from "./pages/Worktrees";
import { Configuration } from "./pages/Configuration";
import { RunDetail } from "./pages/RunDetail";

export function App() {
  return (
    <Provider store={store}>
      <HashRouter>
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
