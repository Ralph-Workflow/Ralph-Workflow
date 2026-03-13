import { type Routes } from '@angular/router';

export const routes: Routes = [
  {
    path: '',
    loadComponent: () =>
      import('./pages/home/home.component').then((m) => m.HomeComponent),
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
