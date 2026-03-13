import {
  Component,
  input,
  computed,
  signal,
  ChangeDetectionStrategy,
  inject,
  OnInit,
  OnDestroy,
  viewChild,
  InjectionToken,
} from '@angular/core';
import { CommonModule } from '@angular/common';
import { DomSanitizer, SafeHtml } from '@angular/platform-browser';
import { CdkVirtualScrollViewport, ScrollingModule } from '@angular/cdk/scrolling';
import { listen as tauriListen } from '@tauri-apps/api/event';
import type { RunLogLine } from '../../types';
import { TauriService } from '../../services/tauri.service';

// Injection token for the Tauri listen function — allows mocking in tests.
export const LISTEN_TOKEN = new InjectionToken<typeof tauriListen>('LISTEN_TOKEN', {
  providedIn: 'root',
  factory: () => tauriListen,
});

// ANSI color codes 30-37 (standard) and 90-97 (bright)
const ANSI_COLORS: Record<number, string> = {
  30: '#4e4e4e', // black
  31: '#ef4444', // red
  32: '#22c55e', // green
  33: '#eab308', // yellow
  34: '#3b82f6', // blue
  35: '#a855f7', // magenta
  36: '#06b6d4', // cyan
  37: '#e2e8f0', // white
  90: '#6b7280', // bright black (gray)
  91: '#f87171', // bright red
  92: '#4ade80', // bright green
  93: '#fde047', // bright yellow
  94: '#60a5fa', // bright blue
  95: '#c084fc', // bright magenta
  96: '#22d3ee', // bright cyan
  97: '#f8fafc', // bright white
};

const MAX_LINES = 5000;

/**
 * Pure function: converts ANSI escape codes in a string to HTML spans.
 * Exported for direct unit testing.
 */
export function parseAnsiToHtml(input: string): string {
  if (!input) return input;

  // Escape HTML entities first to prevent XSS from log content
  const escaped = input
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;');

  let result = '';
  let openSpans = 0;

  // Split on ANSI escape sequences: ESC[ ... m
  // Using dynamic RegExp with String.fromCharCode to avoid embedding control chars in regex literals
  const ESC = String.fromCharCode(27);
  const ansiSequenceRe = new RegExp(`(${ESC}\\[[0-9;]*m)`, 'g');
  const ansiMatchRe = new RegExp(`^${ESC}\\[([0-9;]*)m$`);
  const parts = escaped.split(ansiSequenceRe);

  for (const part of parts) {
    if (!part) continue;

    const ansiMatch = part.match(ansiMatchRe);
    if (ansiMatch) {
      const codes = (ansiMatch[1] ?? '').split(';').map(Number);
      const firstCode = codes[0];
      const isReset = codes.length === 0 || (codes.length === 1 && (firstCode === 0 || firstCode === undefined || isNaN(firstCode)));

      if (isReset) {
        // Close all open spans
        for (let i = 0; i < openSpans; i++) {
          result += '</span>';
        }
        openSpans = 0;
      } else {
        // Process each code
        for (const code of codes) {
          if (code === 1) {
            result += '<span style="font-weight:bold">';
            openSpans++;
          } else if (ANSI_COLORS[code] !== undefined) {
            result += `<span style="color:${ANSI_COLORS[code]}">`;
            openSpans++;
          }
          // Unknown codes are silently stripped (no action)
        }
      }
    } else {
      // Regular text content
      result += part;
    }
  }

  // Close any remaining open spans
  for (let i = 0; i < openSpans; i++) {
    result += '</span>';
  }

  return result;
}

/** Fixed item height in pixels for CDK virtual scroll. */
export const VIRTUAL_SCROLL_ITEM_SIZE = 20;

@Component({
  selector: 'app-run-log',
  standalone: true,
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [CommonModule, ScrollingModule],
  templateUrl: './run-log.component.html',
  styleUrl: './run-log.component.css',
})
export class RunLogComponent implements OnInit, OnDestroy {
  private readonly sanitizer = inject(DomSanitizer);
  private readonly tauri = inject(TauriService);
  private readonly listenFn = inject(LISTEN_TOKEN);

  readonly runId = input.required<string>();
  readonly repoPath = input<string>('');
  readonly worktreePath = input<string | null>(null);

  readonly logLines = signal<string[]>([]);
  readonly autoScroll = signal(true);
  readonly searchTerm = signal('');

  /** Exposed for template use: fixed line height for virtual scroller. */
  readonly itemSize = VIRTUAL_SCROLL_ITEM_SIZE;

  /** Getters for template read access (avoids calling signals with () in templates). */
  get currentSearchTerm() { return this.searchTerm(); }
  get isAutoScrollEnabled() { return this.autoScroll(); }
  get currentLogLines() { return this.logLines(); }
  get currentFilteredLines() { return this.filteredLines(); }
  get currentParsedLines() { return this.parsedLines(); }

  private unlistenFn: (() => void) | null = null;
  private viewport = viewChild<CdkVirtualScrollViewport>('viewport');

  readonly filteredLines = computed(() => {
    const term = this.searchTerm().toLowerCase();
    const lines = this.logLines();
    if (!term) return lines;
    return lines.filter(line => line.toLowerCase().includes(term));
  });

  readonly parsedLines = computed((): SafeHtml[] => {
    return this.filteredLines().map(line =>
      this.sanitizer.bypassSecurityTrustHtml(parseAnsiToHtml(line))
    );
  });

  ngOnInit(): void {
    const runId = this.runId();
    const repoPath = this.repoPath();
    const worktreePath = this.worktreePath();

    // Subscribe to backend log streaming
    void this.tauri.subscribeRunLogs(runId, repoPath, worktreePath);

    // Listen for Tauri events for this run (async setup, store unlisten fn)
    void this.listenFn<RunLogLine>(
      `run-log-${runId}`,
      (event) => {
        this.addLogLine(event.payload.line);
      }
    ).then(unlisten => {
      this.unlistenFn = unlisten;
    });
  }

  ngOnDestroy(): void {
    const runId = this.runId();
    void this.tauri.unsubscribeRunLogs(runId);
    if (this.unlistenFn) {
      this.unlistenFn();
      this.unlistenFn = null;
    }
  }

  /** Add a single log line, enforcing the MAX_LINES limit. */
  addLogLine(line: string): void {
    this.logLines.update(lines => {
      const updated = [...lines, line];
      if (updated.length > MAX_LINES) {
        return updated.slice(updated.length - MAX_LINES);
      }
      return updated;
    });

    if (this.autoScroll()) {
      this.scrollToBottom();
    }
  }

  setAutoScroll(value: boolean): void {
    this.autoScroll.set(value);
    if (value) {
      this.scrollToBottom();
    }
  }

  downloadLogs(): void {
    const content = this.logLines().join('\n');
    const blob = new Blob([content], { type: 'text/plain' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `run-${this.runId()}-logs.txt`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
  }

  private scrollToBottom(): void {
    // Use setTimeout to let the DOM update before scrolling
    setTimeout(() => {
      const vp = this.viewport();
      if (vp) {
        vp.scrollToIndex(this.filteredLines().length - 1, 'smooth');
      }
    }, 0);
  }

  onScrolledIndexChange(index: number): void {
    const vp = this.viewport();
    if (!vp) return;
    const total = this.filteredLines().length;
    const visible = Math.floor(vp.getViewportSize() / this.itemSize);
    const isAtBottom = index + visible >= total - 2;
    if (!isAtBottom && this.autoScroll()) {
      this.autoScroll.set(false);
    }
  }
}
