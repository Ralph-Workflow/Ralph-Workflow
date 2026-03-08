import { useEffect, useRef } from "react";

/// Tags where chord sequences should NOT fire (user is typing).
const INPUT_TAGS = new Set(["INPUT", "TEXTAREA", "SELECT"]);

function isTypingTarget(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false;
  if (INPUT_TAGS.has(target.tagName)) return true;
  if (target.isContentEditable) return true;
  return false;
}

interface UseKeyboardShortcutsOptions {
  onHelpOpen: () => void;
  onNavigate: (path: string) => void;
}

/// Keyboard shortcut map for chord sequences (first key = "g").
const CHORD_MAP: Record<string, string> = {
  h: "/",
  s: "/sessions",
  w: "/worktrees",
  c: "/configuration",
};

/// Timeout (ms) within which the second key of a chord must be pressed.
const CHORD_TIMEOUT_MS = 800;

/**
 * Global keyboard shortcut hook.
 *
 * Shortcuts:
 *   ?          — open help overlay (fires from anywhere except inputs/textareas)
 *   g + h      — navigate to Home
 *   g + s      — navigate to Sessions
 *   g + w      — navigate to Worktrees
 *   g + c      — navigate to Configuration
 *   Escape     — dismisses modals/overlays (handled at component level)
 *   Cmd/Ctrl+N — opens new session wizard (handled at component level via onNavigate('/sessions'))
 *
 * Chord sequences are not active when focus is inside INPUT, TEXTAREA, SELECT,
 * or any contenteditable element to avoid accidental navigation while typing.
 */
export function useKeyboardShortcuts({
  onHelpOpen,
  onNavigate,
}: UseKeyboardShortcutsOptions): void {
  const pendingChordRef = useRef<string | null>(null);
  const chordTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    const clearChord = () => {
      if (chordTimerRef.current !== null) {
        clearTimeout(chordTimerRef.current);
        chordTimerRef.current = null;
      }
      pendingChordRef.current = null;
    };

    const handleKeyDown = (event: KeyboardEvent): void => {
      const target = event.target;

      // --- ? shortcut: open help overlay (not in inputs) ---
      if (event.key === "?" && !isTypingTarget(target)) {
        clearChord();
        onHelpOpen();
        return;
      }

      // --- Cmd/Ctrl+N: navigate to sessions for new session wizard ---
      if (event.key === "n" && (event.metaKey || event.ctrlKey)) {
        clearChord();
        onNavigate("/sessions");
        return;
      }

      // --- Chord sequences: only outside text inputs ---
      if (isTypingTarget(target)) {
        // Let natural typing happen; clear any pending chord state
        clearChord();
        return;
      }

      if (pendingChordRef.current === "g") {
        // Second key of g+X chord
        const destination = CHORD_MAP[event.key];
        clearChord();
        if (destination !== undefined) {
          onNavigate(destination);
        }
        return;
      }

      if (event.key === "g") {
        // Start chord timer
        pendingChordRef.current = "g";
        chordTimerRef.current = setTimeout(() => {
          pendingChordRef.current = null;
          chordTimerRef.current = null;
        }, CHORD_TIMEOUT_MS);
      }
    };

    document.addEventListener("keydown", handleKeyDown);
    return () => {
      document.removeEventListener("keydown", handleKeyDown);
      clearChord();
    };
  }, [onHelpOpen, onNavigate]);
}
