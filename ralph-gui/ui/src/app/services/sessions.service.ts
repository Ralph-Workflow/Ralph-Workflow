import { Injectable, inject, signal, computed } from '@angular/core';
import { TauriService } from './tauri.service';
import type { CreateSessionRequest, SessionSummary } from '../types';

export type LoadingStatus = 'idle' | 'loading' | 'succeeded' | 'failed';

export interface CompletedTodayStats {
  count: number;
  successRate: string;
}

export interface DashboardTrends {
  activeWorktrees: 'up' | 'down' | 'flat';
  resumableRuns: 'up' | 'down' | 'flat';
  completedToday: 'up' | 'down' | 'flat';
  successRate: 'up' | 'down' | 'flat';
}

@Injectable({ providedIn: 'root' })
export class SessionsService {
  private readonly tauri = inject(TauriService);

  readonly sessions = signal<SessionSummary[]>([]);
  readonly status = signal<LoadingStatus>('idle');
  readonly error = signal<string | null>(null);
  readonly selectedRunId = signal<string | null>(null);

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

  readonly completedTodayStats = computed<CompletedTodayStats>(() => {
    const all = this.sessions();
    const today = new Date();
    today.setHours(0, 0, 0, 0);
    const todayMs = today.getTime();
    
    const todaySessions = all.filter(s => {
      const created = new Date(s.created_at);
      created.setHours(0, 0, 0, 0);
      return created.getTime() === todayMs;
    });
    
    const completed = todaySessions.filter(s => s.status === 'completed').length;
    const failed = todaySessions.filter(s => s.status === 'failed').length;
    const totalFinished = completed + failed;
    
    const successRate = totalFinished > 0 
      ? Math.round((completed / totalFinished) * 100) 
      : 100;
    
    return {
      count: completed,
      successRate: `${successRate}%`
    };
  });

  readonly dashboardTrends = computed<DashboardTrends>(() => {
    const all = this.sessions();
    const now = new Date();
    const todayStart = new Date(now);
    todayStart.setHours(0, 0, 0, 0);
    const yesterdayStart = new Date(todayStart);
    yesterdayStart.setDate(yesterdayStart.getDate() - 1);
    
    const todaySessions = all.filter(s => {
      const created = new Date(s.created_at);
      return created >= todayStart;
    });
    
    const yesterdaySessions = all.filter(s => {
      const created = new Date(s.created_at);
      return created >= yesterdayStart && created < todayStart;
    });
    
    const todayCompleted = todaySessions.filter(s => s.status === 'completed').length;
    const yesterdayCompleted = yesterdaySessions.filter(s => s.status === 'completed').length;
    const todayFailed = todaySessions.filter(s => s.status === 'failed').length;
    const yesterdayFailed = yesterdaySessions.filter(s => s.status === 'failed').length;
    
    const computeTrend = (today: number, yesterday: number): 'up' | 'down' | 'flat' => {
      if (today > yesterday) return 'up';
      if (today < yesterday) return 'down';
      return 'flat';
    };
    
    const todaySuccessRate = (todayCompleted + todayFailed) > 0 
      ? todayCompleted / (todayCompleted + todayFailed) 
      : 1;
    const yesterdaySuccessRate = (yesterdayCompleted + yesterdayFailed) > 0 
      ? yesterdayCompleted / (yesterdayCompleted + yesterdayFailed) 
      : 1;
    
    return {
      activeWorktrees: 'flat',
      resumableRuns: computeTrend(
        todaySessions.filter(s => s.status === 'paused' || s.status === 'interrupted').length,
        yesterdaySessions.filter(s => s.status === 'paused' || s.status === 'interrupted').length
      ),
      completedToday: computeTrend(todayCompleted, yesterdayCompleted),
      successRate: computeTrend(todaySuccessRate, yesterdaySuccessRate)
    };
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
