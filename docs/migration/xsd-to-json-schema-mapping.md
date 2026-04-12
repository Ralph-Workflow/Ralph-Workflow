# XSD to JSON Schema Conversion Mapping

> Survey of all 6 XSD schemas with element-by-element JSON Schema equivalents.
> This document is the source of truth for schema conversion decisions.

---

## Common Type Mappings

These XSD patterns appear across multiple schemas:

| XSD Pattern | JSON Schema Equivalent | Notes |
|---|---|---|
| `xs:string` | `{"type": "string"}` | Direct mapping |
| `xs:positiveInteger` | `{"type": "integer", "minimum": 1}` | |
| `xs:enumeration` values | `{"type": "string", "enum": [...]}` | List all values |
| `mixed="true"` (TextWithCodeType) | `{"type": "string"}` | Flatten to plain string; code escaping not needed in JSON |
| `mixed="true"` (InlineTextType) | `{"type": "string"}` | Flatten; emphasis/code/link become markdown in string |
| `xs:attribute` | Inline property on parent object | Attributes become regular properties |
| `xs:sequence` of elements | `{"type": "array", "items": {...}}` | When repeating; otherwise object properties |
| `xs:choice` | `anyOf` with discriminated objects | Per conventions: flat, const discriminator |
| `minOccurs="0"` | Omit from `required` array | Property is optional |
| `minOccurs="1"` / default | Include in `required` array | Property is mandatory |
| `maxOccurs="unbounded"` | `{"type": "array"}` with no `maxItems` | |
| `SkillsMcpType` | `{"skills": [...], "mcps": [...]}` | Flatten mixed content to two string arrays |

---

## 1. Plan (`plan.xsd`)

**Source**: `ralph-workflow/src/prompts/xsd/plan.xsd`
**Root element**: `<ralph-plan>`
**Rust type**: `PlanElements` in `xsd_validation_plan/schema.rs`

### Structure Overview

Root contains a fixed sequence: summary, skills-mcp (opt), parallel-plan (opt), implementation-steps, critical-files, risks-mitigations, verification-strategy.

### Element Mapping

| XSD Element/Type | XSD Kind | JSON Schema Equivalent | Notes |
|---|---|---|---|
| `ralph-plan` | Root element | Root object | Remove `ralph-` prefix |
| `ralph-summary` → `SummaryType` | Complex: sequence(context, scope-items) | `{"summary": {"context": str, "scope_items": [...]}}` | Flatten |
| `scope-items` → `ScopeItemsType` | Sequence of scope-item (min 3) | `{"scope_items": {"type": "array", "minItems": 3, "items": {...}}}` | |
| `scope-item` → `ScopeItemType` | simpleContent + attrs (count, category) | `{"text": str, "count": str?, "category": str?}` | Flatten simpleContent + attrs to object |
| `skills-mcp` → `SkillsMcpType` | Mixed content with skill/mcp children | `{"skills": [str], "mcps": [str]}` | Flatten to two arrays |
| `ralph-parallel-plan` → `ParallelPlanType` | Sequence of work-unit | `{"parallel_plan": [{"id": str, "description": str, ...}]}` | Flatten |
| `work-unit` → `WorkUnitType` | Complex: description, edit-area, dependencies + id attr | `{"id": str, "description": str, "edit_area": {...}, "depends_on": [str]}` | |
| `edit-area` → `EditAreaType` | Sequence of paths/directories | `{"paths": [str], "directories": [str]}` | Flatten nested containers |
| `dependencies` → `DependenciesType` | Sequence of depends-on with unit-id attr | `{"depends_on": [str]}` | Flatten to string array of IDs |
| `ralph-implementation-steps` | Sequence of step | `{"steps": [{...}]}` | Remove ralph- prefix |
| `step` → `StepType` | Complex: title, target-files, location, rationale, content, depends-on + number/type/priority attrs | `{"number": int, "type": str, "priority": str?, "title": str, "targets": [...], "location": str?, "rationale": str?, "content": str, "depends_on": [int]}` | Flatten attrs to properties; content becomes string |
| `target-files` → `TargetFilesType` | Sequence of file with path/action attrs | `{"targets": [{"path": str, "action": str}]}` | |
| `FileActionType` | Enum: create, modify, delete | `{"type": "string", "enum": ["create", "modify", "delete"]}` | 3 values - OK |
| `StepTypeEnum` | Enum: file-change, action, research | `{"type": "string", "enum": ["file_change", "action", "research"]}` | 3 values - OK; normalize hyphens to underscores |
| `PriorityType` | Enum: critical, high, medium, low | `{"type": "string", "enum": ["critical", "high", "medium", "low"]}` | 4 values - OK |
| `content` → `RichContentType` | Choice of paragraph/code-block/table/list/heading | `{"type": "string"}` | **FLATTEN**: all rich content becomes a single markdown string |
| `ralph-critical-files` → `CriticalFilesType` | Sequence: primary-files, reference-files(opt) | `{"critical_files": {"primary_files": [...], "reference_files": [...]}}` | |
| `PrimaryFileType` | Attrs: path, action, estimated-changes | `{"path": str, "action": str, "estimated_changes": str?}` | |
| `ReferenceFileType` | Attrs: path, purpose | `{"path": str, "purpose": str}` | |
| `ralph-risks-mitigations` | Sequence of risk-pair | `{"risks_mitigations": [{"severity": str?, "risk": str, "mitigation": str}]}` | |
| `SeverityType` | Enum: low, medium, high, critical | Same as PriorityType | 4 values - OK |
| `ralph-verification-strategy` | Sequence of verification | `{"verification_strategy": [{"method": str, "expected_outcome": str}]}` | |

### Problem Areas

- **Rich content types** (InlineTextType, ParagraphType, CodeBlockType, TableType, ListType, HeadingType, RichContentType): These form a deeply nested tree (5+ levels). **Decision**: flatten entirely to `"type": "string"` with markdown content. This is the single biggest simplification.
- **Mixed content** on InlineTextType, ParagraphType, CellType, ListItemType: Mixed content has no JSON equivalent. **Decision**: flatten to string.
- **`ralph-` prefixes** on all elements: Remove in JSON Schema. Use clean names (e.g., `summary` not `ralph-summary`).
- **Attributes vs elements**: XSD uses attributes for metadata (number, type, path, action). JSON Schema treats these as regular properties on the same object.

### Flattening Candidates

- `RichContentType` hierarchy (Paragraph, CodeBlock, Table, List, Heading) -> single string
- `InlineTextType` / `TextWithCodeType` -> string
- `ScopeItemType` simpleContent -> object with text/count/category
- `EditAreaType` nested paths/directories containers -> flat arrays
- `DependenciesType` -> flat array of strings

---

## 2. Development Result (`development_result.xsd`)

**Source**: `ralph-workflow/src/prompts/xsd/development_result.xsd`
**Root element**: `<ralph-development-result>`
**Rust type**: `DevResultElements` in `xsd_validation_development_result/types.rs`

### Element Mapping

| XSD Element/Type | XSD Kind | JSON Schema Equivalent | Notes |
|---|---|---|---|
| `ralph-development-result` | Root element | Root object | Remove prefix |
| `ralph-status` | Enum: completed, partial, failed | `{"status": {"type": "string", "enum": ["completed", "partial", "failed"]}}` | 3 values - OK |
| `ralph-summary` → `TextWithCodeType` | Mixed text + code | `{"summary": {"type": "string"}}` | Flatten mixed content |
| `skills-mcp` → `SkillsMcpType` | Mixed content | `{"skills": [str], "mcps": [str]}` | Two flat arrays |
| `ralph-files-changed` → `TextWithCodeType` | Mixed, optional | `{"files_changed": {"type": "string"}}` | Optional |
| `ralph-next-steps` → `TextWithCodeType` | Mixed, optional | `{"next_steps": {"type": "string"}}` | Optional |

### Problem Areas

- **TextWithCodeType mixed content**: Flatten to string. The `<code>` elements were only for XML escaping of `<`, `>`, `&` which is not needed in JSON.
- Simple schema overall - straightforward conversion.

---

## 3. Development Continuation Result (`development_continuation_result.xsd`)

**Source**: `ralph-workflow/src/prompts/xsd/development_continuation_result.xsd`
**Root element**: `<ralph-development-result>` (same root name as dev result)

### Element Mapping

| XSD Element/Type | XSD Kind | JSON Schema Equivalent | Notes |
|---|---|---|---|
| `ralph-development-result` | Root element | Same as development_result | **MERGE**: continuation is a variant of development_result, not separate |
| `ralph-status` | Enum: partial, failed (NO completed) | Use same enum but continuation restricts to `["partial", "failed"]` | Or: add `continuation` object to signal continuation mode |
| `ralph-summary` | Mixed text + code | `{"summary": str}` | Same as dev result |
| `skills-mcp` | Mixed content, optional | Same as dev result | |
| `ralph-next-steps` | Mixed text + code, REQUIRED | `{"next_steps": str}` | **Required** in continuation (optional in normal) |

### Problem Areas

- **Separate XSD for same concept**: The continuation result is nearly identical to development_result. **Decision per plan**: merge into single `development_result` schema with optional `continuation` object. No separate schema.
- `ralph-files-changed` is absent in continuation (present in normal dev result).

---

## 4. Issues (`issues.xsd`)

**Source**: `ralph-workflow/src/prompts/xsd/issues.xsd`
**Root element**: `<ralph-issues>`
**Rust type**: `IssuesElements` in `xsd_validation_issues/types.rs`

### Element Mapping

| XSD Element/Type | XSD Kind | JSON Schema Equivalent | Notes |
|---|---|---|---|
| `ralph-issues` | Root element | Root object | Remove prefix |
| `ralph-issue` → `IssueWithSkillsType` | Mixed text + code + skills-mcp children, unbounded | `{"issues": [{"text": str, "skills": [str]?, "mcps": [str]?}]}` | Flatten mixed content; extract skills-mcp |
| `ralph-no-issues-found` → `TextWithCodeType` | Mixed text + code | Use anyOf: either issues array OR no_issues_found variant | **anyOf candidate** |
| `SkillsMcpType` within issue | Per-issue skill/mcp recommendations | Inline skills/mcps arrays on each issue object | |

### Problem Areas

- **xs:choice at root level**: Either `ralph-issue` list OR `ralph-no-issues-found`. This maps to an `anyOf` discriminated union.
- **Per-issue skills-mcp**: Mixed content with nested skills. Flatten to arrays on each issue object.
- **Canonical name**: `issues` not `review_issues`.

### anyOf Candidate

```json
{
  "anyOf": [
    { "type": "object", "properties": { "type": {"const": "issues_found"}, "issues": [...] } },
    { "type": "object", "properties": { "type": {"const": "no_issues_found"}, "explanation": str } }
  ]
}
```

---

## 5. Fix Result (`fix_result.xsd`)

**Source**: `ralph-workflow/src/prompts/xsd/fix_result.xsd`
**Root element**: `<ralph-fix-result>`

### Element Mapping

| XSD Element/Type | XSD Kind | JSON Schema Equivalent | Notes |
|---|---|---|---|
| `ralph-fix-result` | Root element | Root object | Remove prefix |
| `ralph-status` | Enum: all_issues_addressed, issues_remain, no_issues_found | `{"status": {"type": "string", "enum": ["all_issues_addressed", "issues_remain", "no_issues_found"]}}` | 3 values - OK |
| `ralph-summary` → `TextWithCodeType` | Mixed, optional | `{"summary": {"type": "string"}}` | Optional |

### Problem Areas

- Very simple schema. No significant conversion issues.
- Only concern: `TextWithCodeType` mixed content -> string (same as everywhere).

---

## 6. Commit Message (`commit_message.xsd`)

**Source**: `ralph-workflow/src/prompts/xsd/commit_message.xsd`
**Root element**: `<ralph-commit>`

### Element Mapping

| XSD Element/Type | XSD Kind | JSON Schema Equivalent | Notes |
|---|---|---|---|
| `ralph-commit` | Root element with xs:choice | Root object with anyOf (commit vs skip) | **anyOf candidate** |
| `ralph-subject` → `TextWithCodeType` | Mixed text | `{"subject": str}` | Required for commit variant |
| `ralph-body` → `TextWithCodeType` | Mixed, optional | `{"body": str}` | Simple body variant |
| `ralph-body-summary` | Mixed, optional | `{"body_summary": str}` | Detailed body variant |
| `ralph-body-details` | Mixed, optional | `{"body_details": str}` | Detailed body variant |
| `ralph-body-footer` | Mixed, optional | `{"body_footer": str}` | Detailed body variant |
| `ralph-files` → `FileListType` | Sequence of ralph-file | `{"files": [str]}` | Flatten to string array |
| `ralph-excluded-files` → `ExcludedFileListType` | Sequence of excluded-file entries | `{"excluded_files": [{"path": str, "reason": str}]}` | |
| `ExcludedFileReasonType` | Enum: internal-ignore, not-task-related, sensitive, deferred | `{"type": "string", "enum": ["internal_ignore", "not_task_related", "sensitive", "deferred"]}` | 4 values - OK; normalize hyphens |
| `ralph-skip` → `TextWithCodeType` | Mixed text | Skip variant: `{"type": "skip", "reason": str}` | |

### Problem Areas

- **xs:choice at root**: Either normal commit sequence OR skip. Maps to anyOf discriminated union.
- **Order-insensitive body/files**: XSD has complex choice groups allowing body before or after files. JSON doesn't have ordering, so this simplifies naturally.
- **Mutually exclusive body formats**: Either simple `ralph-body` OR detailed `ralph-body-summary`/`ralph-body-details`/`ralph-body-footer`. In JSON: use `body` for simple, and `body_summary`/`body_details`/`body_footer` for detailed. Validation: if `body` is present, detailed fields must be absent and vice versa.

### anyOf Candidate

```json
{
  "anyOf": [
    {
      "type": "object",
      "properties": {
        "type": {"const": "commit"},
        "subject": {"type": "string"},
        "body": {"type": "string"},
        "body_summary": {"type": "string"},
        "body_details": {"type": "string"},
        "body_footer": {"type": "string"},
        "files": {"type": "array", "items": {"type": "string"}},
        "excluded_files": {"type": "array", "items": {...}}
      },
      "required": ["type", "subject"]
    },
    {
      "type": "object",
      "properties": {
        "type": {"const": "skip"},
        "reason": {"type": "string"}
      },
      "required": ["type", "reason"]
    }
  ]
}
```

---

## Summary of Key Decisions

1. **All mixed content -> string**: InlineTextType, ParagraphType, TextWithCodeType, CellType, ListItemType all become plain strings.
2. **RichContentType -> string**: The entire rich content hierarchy (paragraphs, code blocks, tables, lists, headings) collapses to a markdown string.
3. **Remove `ralph-` prefixes**: All JSON property names use clean names.
4. **Attributes -> properties**: XSD attributes become regular JSON properties on the same object.
5. **SkillsMcpType -> two arrays**: `{"skills": [string], "mcps": [string]}` everywhere.
6. **Continuation merged**: No separate continuation schema; use `development_result` with optional `continuation` object.
7. **xs:choice -> anyOf**: Issues (found/not-found) and commit (commit/skip) use discriminated anyOf.
8. **Hyphen to underscore**: Property names normalize `kebab-case` to `snake_case`.
