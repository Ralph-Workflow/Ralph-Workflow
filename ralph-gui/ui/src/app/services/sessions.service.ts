import { Injectable, inject, signal, computed } from '@angular/core';
import { TauriService } from './tauri.service';
import type { CreateSessionRequest, SessionSummary } from '../types';

export type LoadingStatus = 'idle' | 'loading' | 'succeeded' | 'failed';

@Injectable({ providedIn: 'root' })
export class SessionsService {
  private readonly tauri = inject(TauriService);

  // State signals
  readonly sessions = signal<SessionSummary[]>([]);
  readonly status = signal<LoadingStatus>('idle');
  readonly error = signal<string | null>(null);
  readonly selectedRunId = signal<string | null>(null);

  // Computed signals
  readonly isLoading = computed(() => this.status() === 'loading');

  readonly activeRuns = computed(() => {
    const all = this.sessions();
    return all
      .filter(s => s.status === 'running')
      .sort((a, b) => b.created_at.localeCompare(a.created_at));
  });

  readonly completedToday = computed(() => {
    const all = this.sessions();
    const today = new Date();
    today.setHours(0, 0, 0, 0);
    const todayMs = today.getTime();
    return all.filter(s => {
      if (s.status !== 'completed') return false;
      const created = new Date(s.created_at);
      created.setHours(0, 0, 0, 0);
      return created.getTime() === todayMs;
    }).length;
  });

  readonly recentCompletions = computed(() => {
    const all = this.sessions();
    return all
      .filter(s => s.status === 'completed')
      .sort((a, b) => b.created_at.localeCompare(a.created_at))
      .slice(0, 10);
  });

  readonly needsAttentionRuns = computed(() => {
    const all = this.sessions();
    return all
      .filter(s => s.status === 'failed' || s.status === 'paused' || s.status === 'interrupted')
      .sort((a, b) => b.created_at.localeCompare(a.created_at));
  });

  async fetchSessions(repoPath: string): Promise<void> {
    this.status.set('loading');
    this.error.set(null);

    try {
      const sessions = await this.tauri.getSessions(repoPath);
      this.sessions.set(sessions);
      this.status.set('succeeded');
    } catch (e) {
      this.status.set('failed');
      this.error.set(e instanceof Error ? e.message : 'Unknown error');
    }
  }

  async createSession(request: CreateSessionRequest): Promise<SessionSummary> {
    try {
      const session = await this.tauri.createSession(request);
      this.sessions.update((s) => [...s, session]);
      return session;
    } catch (e) {
      const errorMsg = e instanceof Error ? e.message : 'Unknown error';
      this.error.set(errorMsg);
      // Fire notification for failed session launch
      void this.tauri.notifyRunStatusChange('Failed', 'launch', errorMsg);
      throw e;
    }
  }

  async resumeSession(runId: string, repoPath: string): Promise<void> {
    try {
      await this.tauri.resumeRalphSession(runId, repoPath);
      const detail = await this.tauri.getSessionDetail(runId);
      this.sessions.update((sessions) => {
        const index = sessions.findIndex((s) => s.run_id === detail.run_id);
        if (index !== -1) {
          sessions[index] = detail;
        }
        return [...sessions];
      });
    } catch (e) {
      const errorMsg = e instanceof Error ? e.message : 'Unknown error';
      this.error.set(errorMsg);
      void this.tauri.notifyRunStatusChange('Failed', runId, errorMsg);
      throw e;
    }
  }

  setActiveSession(runId: string | null): void {
    this.selectedRunId.set(runId);
  }

  clearError(): void {
    this.error.set(null);
  }
}
