import { type Routes } from '@angular/router';
import { configurationCanDeactivateGuard } from './pages/configuration/configuration.guard';

export const routes: Routes = [
  {
    path: '',
    loadComponent: () =>
      import('./pages/home/home.component').then((m) => m.HomeComponent),
  },
  {
    path: 'welcome',
    loadComponent: () =>
      import('./pages/welcome/welcome.component').then((m) => m.WelcomeComponent),
  },
  {
    path: 'sessions',
    loadComponent: () =>
      import('./pages/sessions/sessions.component').then((m) => m.SessionsComponent),
  },
  {
    path: 'worktrees',
    loadComponent: () =>
      import('./pages/worktrees/worktrees.component').then((m) => m.WorktreesComponent),
  },
  {
    path: 'configuration',
    loadComponent: () =>
      import('./pages/configuration/configuration.component').then((m) => m.ConfigurationComponent),
    canDeactivate: [configurationCanDeactivateGuard],
  },
  {
    path: 'preferences',
    loadComponent: () =>
      import('./pages/preferences/preferences.component').then((m) => m.PreferencesComponent),
  },
  {
    path: 'onboarding',
    loadComponent: () =>
      import('./pages/onboarding/onboarding.component').then((m) => m.OnboardingComponent),
  },
  {
    path: 'templates',
    loadComponent: () =>
      import('./pages/templates/templates.component').then((m) => m.TemplatesComponent),
  },
  {
    path: 'agent-tools',
    loadComponent: () =>
      import('./pages/agent-tools/agent-tools.component').then((m) => m.AgentToolsComponent),
  },
  {
    path: 'runs/:runId',
    loadComponent: () =>
      import('./pages/run-detail/run-detail.component').then((m) => m.RunDetailComponent),
  },
  {
    path: '**',
    redirectTo: '',
  },
];
