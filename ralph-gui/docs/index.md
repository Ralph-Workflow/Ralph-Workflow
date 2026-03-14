# Ralph GUI Documentation Index

This directory is the entry point for Ralph GUI product, UX, and backend
documentation.

Use `ralph-gui/docs/glossary.md` first when terminology matters. All GUI docs
should use the same vocabulary.

## Reading Order

1. `ralph-gui/docs/glossary.md`
   - Canonical terms for workspace, worktree, session, run, checkpoint, and
     backend ownership.
2. `ralph-gui/docs/wireframes/README.md`
   - Canonical screen-by-screen wireframes, UX coverage notes, state layouts, and living updates as UX findings grow.
3. `ralph-gui/docs/designs/gui-design.md`
   - Lightweight pointer document for the wireframe system.
4. `ralph-gui/docs/designs/design-criteria.md`
   - Binding visual and interaction design standards.
5. `ralph-gui/docs/designs/ux-acceptance-criteria.md`
   - Ongoing UX review principles and quality standards.
6. `ralph-gui/docs/designs/acceptance-criteria.md`
   - Implementation acceptance contract for the GUI.
7. `ralph-gui/docs/designs/tauri-cli-backend-architecture.md`
   - Binding architecture contract for Angular, Tauri, and CLI integration.
8. `ralph-gui/docs/designs/tauri-cli-protocol.md`
   - Detailed communication, typing, and compatibility contract between Tauri
     and the Ralph CLI.

## Document Roles

- `glossary.md`
  - Shared language. If docs disagree on a term, update the glossary first.
- `wireframes/README.md`
  - Canonical wireframe entry point with organized screen documents that should be updated as UX findings evolve.
- `designs/gui-design.md`
  - Redirect/reference doc pointing readers to the wireframe system.
- `designs/design-criteria.md`
  - How the GUI should look and feel when implemented.
- `designs/ux-acceptance-criteria.md`
  - How to review UX quality continuously, not just once.
- `designs/acceptance-criteria.md`
  - What must be true for the GUI to be considered complete.
- `designs/tauri-cli-backend-architecture.md`
  - How the GUI integrates with Tauri and the Ralph CLI.
- `designs/tauri-cli-protocol.md`
  - Message-level transport, type-layer, and compatibility details for the
    Tauri/CLI boundary.

## Documentation Rules

- Use `Session` for the user-facing launched unit in the GUI.
- Use `Run` for the underlying Ralph CLI execution and execution-state details.
- Assume one session maps to one run unless a later architecture document
  explicitly changes that rule.
- Keep acceptance, UX, design, and architecture docs aligned when behavior or
  terminology changes.
- When adding a new GUI doc, link it here and make it reference the glossary.
- Keep detailed ASCII wireframes in `ralph-gui/docs/wireframes/`.
- Treat wireframes as living documents; update them when new UX findings refine the right flow, state, or explanation.
- Do not reintroduce large inline wireframe specs into `designs/gui-design.md`.
