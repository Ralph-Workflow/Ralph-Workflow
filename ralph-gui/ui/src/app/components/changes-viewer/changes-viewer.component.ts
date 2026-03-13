import {
  Component,
  input,
  signal,
  computed,
  ChangeDetectionStrategy,
  inject,
  OnInit,
  effect,
} from '@angular/core';
import { CommonModule } from '@angular/common';
import type { FileDiff, RunChanges } from '../../types';
import { TauriService } from '../../services/tauri.service';

/** A file diff grouped under its parent directory. */
export interface FileGroup {
  directory: string;
  files: FileDiff[];
  expanded: boolean;
}

@Component({
  selector: 'app-changes-viewer',
  standalone: true,
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [CommonModule],
  templateUrl: './changes-viewer.component.html',
  styleUrl: './changes-viewer.component.css',
})
export class ChangesViewerComponent implements OnInit {
  private readonly tauri = inject(TauriService);

  readonly runId = input<string>('');
  readonly repoPath = input<string>('');
  readonly worktreePath = input<string | null>(null);
  /** Pre-select a specific iteration filter (from iteration history click). */
  readonly filterIteration = input<number | null>(null);

  readonly selectedIteration = signal<number | null>(null);
  readonly selectedFile = signal<FileDiff | null>(null);
  readonly runChanges = signal<RunChanges | null>(null);
  readonly isLoading = signal(false);
  readonly copySuccess = signal(false);
  /** Whether side-by-side view is active (default: unified). */
  readonly sideBySide = signal(false);

  readonly files = computed(() => this.runChanges()?.files ?? []);

  /** Files grouped by parent directory for the file tree layout. */
  readonly fileGroups = computed<FileGroup[]>(() => {
    const files = this.files();
    const groupMap = new Map<string, FileDiff[]>();

    for (const file of files) {
      const parts = file.path.split('/');
      const directory = parts.length > 1 ? parts.slice(0, -1).join('/') : '.';
      const group = groupMap.get(directory) ?? [];
      group.push(file);
      groupMap.set(directory, group);
    }

    return Array.from(groupMap.entries()).map(([directory, groupFiles]) => ({
      directory,
      files: groupFiles,
      expanded: true,
    }));
  });

  readonly iterationOptions = computed((): Array<{ label: string; value: number | null }> => {
    const changes = this.runChanges();
    const options: Array<{ label: string; value: number | null }> = [
      { label: 'All Iterations', value: null },
    ];
    if (changes?.iteration != null && changes.iteration > 0) {
      for (let i = 1; i <= changes.iteration; i++) {
        options.push({ label: `Iteration ${i}`, value: i });
      }
    }
    return options;
  });

  readonly diffLines = computed((): Array<{ text: string; cssClass: string }> => {
    const file = this.selectedFile();
    if (!file?.diff_text) return [];
    return file.diff_text.split('\n').map(line => ({
      text: line,
      cssClass: this.getLineClass(line),
    }));
  });

  /** Split view: added lines (right column). */
  readonly addedLines = computed((): string[] => {
    const file = this.selectedFile();
    if (!file?.diff_text) return [];
    return file.diff_text.split('\n').filter(
      line => line.startsWith('+') && !line.startsWith('+++')
    );
  });

  /** Split view: removed lines (left column). */
  readonly removedLines = computed((): string[] => {
    const file = this.selectedFile();
    if (!file?.diff_text) return [];
    return file.diff_text.split('\n').filter(
      line => line.startsWith('-') && !line.startsWith('---')
    );
  });

  get repoPath_() { return this.repoPath(); }
  get isLoading_() { return this.isLoading(); }
  get files_() { return this.files(); }
  get fileGroups_() { return this.fileGroups(); }
  get runChanges_() { return this.runChanges(); }
  get selectedIteration_() { return this.selectedIteration(); }
  get copySuccess_() { return this.copySuccess(); }
  get selectedFile_() { return this.selectedFile(); }
  get diffLines_() { return this.diffLines(); }
  get iterationOptions_() { return this.iterationOptions(); }
  get sideBySide_() { return this.sideBySide(); }
  get addedLines_() { return this.addedLines(); }
  get removedLines_() { return this.removedLines(); }

  constructor() {
    // Apply filterIteration input to the selectedIteration signal on first run
    effect(() => {
      const filter = this.filterIteration();
      if (filter != null) {
        this.selectedIteration.set(filter);
      }
    });

    // React to iteration changes after the initial load
    effect(() => {
      const iteration = this.selectedIteration();
      const repoPath = this.repoPath();
      // Only auto-refetch on iteration change when we already have data
      if (repoPath && this.runChanges() !== null) {
        void this.loadChanges(repoPath, iteration);
      }
    });
  }

  ngOnInit(): void {
    const repoPath = this.repoPath();
    if (!repoPath) return;
    // If a filterIteration was provided, use it for initial load
    const initialIteration = this.filterIteration() ?? this.selectedIteration();
    void this.loadChanges(repoPath, initialIteration);
  }

  private async loadChanges(repoPath: string, iteration: number | null): Promise<void> {
    this.isLoading.set(true);
    try {
      const changes = await this.tauri.getRunChanges(
        repoPath,
        this.worktreePath(),
        iteration ?? undefined,
      );
      this.runChanges.set(changes);
      // Auto-select first file
      if (changes?.files?.length > 0) {
        this.selectedFile.set(changes.files[0] ?? null);
      } else {
        this.selectedFile.set(null);
      }
    } catch (err) {
      console.error('Failed to load run changes:', err);
    } finally {
      this.isLoading.set(false);
    }
  }

  selectFile(file: FileDiff): void {
    this.selectedFile.set(file);
  }

  setIteration(iteration: number | null): void {
    this.selectedIteration.set(iteration);
  }

  toggleViewMode(): void {
    this.sideBySide.update(v => !v);
  }

  /**
   * Returns the CSS class for a diff line.
   * Addition: starts with + but not ++
   * Removal: starts with - but not --
   */
  getLineClass(line: string): string {
    if (line.startsWith('+') && !line.startsWith('++')) {
      return 'diff-line--added';
    }
    if (line.startsWith('-') && !line.startsWith('--')) {
      return 'diff-line--removed';
    }
    return '';
  }

  async copyAsPatch(): Promise<void> {
    const files = this.files();
    const patchText = files.map(f => f.diff_text).join('\n');
    await navigator.clipboard.writeText(patchText);
    this.copySuccess.set(true);
    setTimeout(() => this.copySuccess.set(false), 2000);
  }
}
