# Open Design Namespaced Bundle Design

## Goal

Add a second shipped design-oriented skill bundle to Ralph Workflow using curated skills discovered through `nexu-io/open-design`, while ensuring Ralph fetches the actual upstream skill content rather than shipping Open Design pointer/stub files.

## Scope

In scope:
- a second Ralph Workflow-managed shipped skill bundle for design-oriented skills
- namespaced materialization for Open Design-derived skills so they do not collide with the existing 17 workflow skills
- manifest support for catalog sources plus true upstream sources
- sync logic that follows Open Design upstream declarations and fetches the real skill content
- compatibility for both Claude Code and OpenCode through the same flattened process-scoped runtime directory
- tests proving pointer files are not what Ralph ships

Out of scope:
- changing the existing 17 workflow skill names
- flattening Open Design skills into the existing workflow bundle
- replacing personal/project user skills
- broad UI/design product decisions about exactly which Open Design skills to include after the namespaced bundle mechanism exists

## Problem Statement

Open Design is not a single-origin skill source. It is a curated catalog of many design-oriented skills, and many entries are stubs that declare another upstream in frontmatter or body text. Shipping the Open Design stub file would be incorrect because:

1. the stub is not the actual skill content users need,
2. it loses provenance accuracy by making Open Design look like the authored source,
3. it makes Ralph ship indirection instead of the real skill.

Ralph therefore needs a two-step sourcing rule:

- use Open Design as a discovery catalog,
- but fetch the actual skill content from the declared upstream whenever Open Design says the skill comes from somewhere else.

## Product Contract

### 1. Second shipped bundle, not bundle replacement

Ralph Workflow should continue shipping the existing 17 workflow/process skills as the core default bundle.

Open Design-derived skills should ship as a **second bundled collection** focused on design-related work. This keeps the core workflow surface stable and avoids accidental collisions with process-oriented skills already used in planning and development prompts.

### 2. Ralph-managed namespace

Every Open Design-derived shipped skill must receive a Ralph-managed export name before it is materialized into the runtime filesystem. The namespace should be deterministic and cross-platform-safe, for example:

- `open-design--frontend-slides`
- `open-design--frontend-design`
- `open-design--ui-skills`

This namespace is a Ralph packaging/runtime namespace, not a claim about the upstream skill’s original name.

### 3. True-upstream fetch rule

If an Open Design skill declares an upstream source, Ralph must fetch the actual skill content from that upstream. Ralph must not ship the Open Design pointer file itself as the bundle artifact.

Only when an Open Design entry is itself the authored source and has no better upstream should Ralph fetch directly from Open Design.

## Architecture

### 1. Catalog layer vs content layer

Open Design becomes a **catalog/discovery layer**, not automatically the content source.

For each shipped design skill, the manifest must record two distinct concepts:

- **catalog source**: where Ralph discovered the skill (`nexu-io/open-design`)
- **content source**: where Ralph fetches the real skill content

### 2. Namespaced bundle layer

Ralph should maintain a second bundle definition in the shipped manifest for Open Design-derived skills. This bundle should:

- enumerate the selected design skills Ralph wants to ship,
- define the exported Ralph-facing namespaced names,
- preserve the original upstream skill name separately,
- store catalog provenance plus content-source provenance.

### 3. Runtime materialization layer

The process-scoped skill directory used by `RALPH_SKILLS_PROCESS_DIR` remains a flat directory. Because both Claude Code and OpenCode ultimately consume flat materialized skill files in Ralph’s runtime view, namespacing must happen **before files are written**.

The runtime view therefore contains:

- current 17 core workflow skills with unchanged names,
- Open Design bundle skills under namespaced filenames,
- external user/project skills merged in afterward.

### 4. Sync resolution layer

The sync script must support a two-hop resolution flow:

1. read an Open Design catalog entry,
2. inspect the entry for upstream metadata,
3. resolve the true upstream repo/path/ref,
4. fetch the actual skill content from there,
5. materialize it under the Ralph-managed namespaced export name,
6. record both catalog and content provenance in metadata.

If the Open Design entry lacks upstream metadata, Ralph may fall back to Open Design itself as the content source, but that should be an explicit manifest choice rather than implicit behavior.

## Design Details

### Manifest schema additions

Each Open Design-derived skill should carry fields like:

- `bundle`: `open-design`
- `export_name`: Ralph-managed namespaced name
- `catalog`: repo/ref/path of the Open Design entry
- `resolved_from`: `via-open-design`
- `content_source`: repo/ref/path of the actual skill content Ralph fetches
- `upstream_name`: original upstream skill name

### Metadata requirements

Bundle metadata should grow to include per-skill provenance with enough detail to audit both catalog origin and true content origin. For Open Design-derived skills, metadata must make it obvious that Ralph shipped the actual upstream skill rather than the Open Design pointer file.

### Collision policy

No Open Design-derived skill should be exported into the flat runtime view without a namespace prefix. This avoids collisions with:

- the current 17 core workflow skills,
- user/project skills in `~/.claude/skills` or `./.claude/skills`,
- future design bundle additions.

## File-Level Change Set

### Bundle manifest and sync pipeline

- `skills-package/upstream-skills.json`
  - add a second bundle section for Open Design-derived skills
  - record both catalog and resolved content source

- `skills-package/bin/sync-upstream-skills.js`
  - add resolution logic for Open Design catalog entries
  - parse declared upstream metadata from catalog skills when required
  - materialize exported namespaced filenames
  - emit metadata proving both catalog source and true content source

### Python-side shipped metadata

- `ralph/skills/content/metadata.json`
- `ralph/skills/_content.py`

These should expose the expanded bundle inventory and per-skill source records so runtime/install checks understand the second bundle too.

### Runtime composition

- `ralph/skills/_process_view.py`

This should keep the existing merge order but naturally accept the larger namespaced shipped bundle without collisions.

### Tests

- new or updated sync-script tests for Open Design two-hop fetch behavior
- metadata tests proving namespaced bundle presence and per-skill provenance
- runtime-view tests proving namespaced design skills coexist with existing workflow skills and external personal/project skills

## Testability Requirements

### Pure tests

- manifest coverage test: selected Open Design bundle skills have export names and resolved content sources
- resolver test: Open Design catalog entry with declared upstream resolves to the actual upstream fetch URL
- metadata test: generated metadata includes both catalog and content provenance for namespaced design skills

### Thin integration tests

- sync script against a fixture catalog entry that points to a different upstream fixture skill
- process-scoped runtime view containing both core and namespaced design skills without collision
- full shipped inventory tests verifying bundle counts and namespace presence

## Acceptance Criteria

- Ralph ships Open Design-derived skills as a second bundle, not as flat additions to the current 17 workflow skills.
- Open Design-derived skills are namespaced at materialization time.
- Ralph does not ship Open Design pointer files when a declared true upstream exists.
- Generated metadata clearly records both catalog source and true content source.
- Both Claude Code and OpenCode compatibility continue through the flat `RALPH_SKILLS_PROCESS_DIR` runtime directory.
- `make verify` passes.

## Risks and Mitigations

- **Risk:** Open Design upstream metadata is inconsistent.
  - **Mitigation:** treat upstream resolution as explicit manifest-driven logic with tests for fallback behavior.

- **Risk:** Namespace strategy leaks into user-visible guidance awkwardly.
  - **Mitigation:** keep Ralph-managed namespaced export names stable and document them as shipped bundle names, while preserving original upstream names in metadata only.

- **Risk:** Future design-bundle growth bloats the runtime view.
  - **Mitigation:** keep the second bundle curated and explicitly enumerated instead of mirroring the entire Open Design catalog.

## Review Notes

- The critical rule is that Open Design is often a catalog, not the actual content source.
- This design keeps the current workflow bundle stable while allowing Ralph to ship many design-focused skills safely.
- It preserves one runtime consumption shape for Claude Code and OpenCode by doing namespace resolution before files are materialized.
