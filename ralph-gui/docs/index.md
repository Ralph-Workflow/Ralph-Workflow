# Ralph GUI Documentation Index

This directory is the entry point for Ralph GUI product, UX, and backend
documentation.

Use `ralph-gui/docs/glossary.md` first when terminology matters. All GUI docs
should use the same vocabulary.

## Reading Order

1. `ralph-gui/docs/glossary.md`
   - Canonical terms for workspace, worktree, session, run, checkpoint, and
     backend ownership.
2. `ralph-gui/docs/designs/gui-design.md`
   - Product structure, screens, page behavior, and interaction model.
3. `ralph-gui/docs/designs/design-criteria.md`
   - Binding visual and interaction design standards.
4. `ralph-gui/docs/designs/ux-acceptance-criteria.md`
   - Ongoing UX review principles and quality standards.
5. `ralph-gui/docs/designs/acceptance-criteria.md`
   - Implementation acceptance contract for the GUI.
6. `ralph-gui/docs/designs/tauri-cli-backend-architecture.md`
   - Binding architecture contract for Angular, Tauri, and CLI integration.
7. `ralph-gui/docs/designs/tauri-cli-protocol.md`
   - Detailed communication, typing, and compatibility contract between Tauri
     and the Ralph CLI.

## Document Roles

- `glossary.md`
  - Shared language. If docs disagree on a term, update the glossary first.
- `designs/gui-design.md`
  - What the GUI is and how the product should behave.
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
