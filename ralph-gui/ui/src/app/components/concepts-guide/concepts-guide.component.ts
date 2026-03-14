import { ChangeDetectionStrategy, Component, signal } from '@angular/core';
import { RouterModule } from '@angular/router';

interface DrainInfo {
  name: string;
  description: string;
}

interface PhaseInfo {
  name: string;
  color: string;
  description: string;
}

const PIPELINE_PHASES: PhaseInfo[] = [
  { name: 'Plan', color: 'phase-plan', description: 'Refines and structures your task prompt.' },
  { name: 'Develop', color: 'phase-develop', description: 'Writes code to fulfill the task.' },
  { name: 'Review', color: 'phase-review', description: 'Reviews the developer output.' },
  { name: 'Commit', color: 'phase-commit', description: 'Creates a git commit with the changes.' },
];

const DRAIN_INFO: DrainInfo[] = [
  { name: 'Planning', description: 'Refines and structures your task prompt before development.' },
  { name: 'Development', description: 'Writes code to fulfill the task requirements.' },
  { name: 'Analysis', description: 'Checks code against the plan after each dev iteration. GPT models recommended.' },
  { name: 'Review', description: 'Reviews the developer\'s output for quality and correctness.' },
  { name: 'Fix', description: 'Addresses issues found during review.' },
  { name: 'Commit', description: 'Creates a git commit with the changes.' },
];

@Component({
  selector: 'app-concepts-guide',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [RouterModule],
  templateUrl: './concepts-guide.component.html',
  styleUrl: './concepts-guide.component.css',
})
export class ConceptsGuideComponent {
  readonly isOpen = signal<boolean>(false);
  readonly pipelinePhases = PIPELINE_PHASES;
  readonly drainInfo = DRAIN_INFO;

  get isOpen_() { return this.isOpen(); }

  toggle(): void {
    this.isOpen.update(v => !v);
  }
}
