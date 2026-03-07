import { configureStore } from "@reduxjs/toolkit";
import { useDispatch, useSelector } from "react-redux";
import sessionReducer from "./slices/sessionSlice";
import worktreeReducer from "./slices/worktreeSlice";
import configReducer from "./slices/configSlice";
import runReducer from "./slices/runSlice";

export const store = configureStore({
  reducer: {
    sessions: sessionReducer,
    worktrees: worktreeReducer,
    config: configReducer,
    runs: runReducer,
  },
});

export type RootState = ReturnType<typeof store.getState>;
export type AppDispatch = typeof store.dispatch;

// Typed hooks for use throughout the app
export const useAppDispatch = () => useDispatch<AppDispatch>();
export const useAppSelector = <TSelected>(
  selector: (state: RootState) => TSelected,
) => useSelector(selector);
