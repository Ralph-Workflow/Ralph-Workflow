# Plan Schema: XSD Equivalence Notes

> Field-by-field mapping from `ralph-workflow/schemas/plan.schema.json` to `ralph-workflow/src/prompts/xsd/plan.xsd`.

---

## Field-by-Field Mapping

| JSON Schema Property | XSD Element/Type | Conversion Notes |
|---|---|---|
| (root object) | `ralph-plan` | Removed `ralph-` prefix |
| `summary` | `ralph-summary` / `SummaryType` | Direct mapping, prefix removed |
| `summary.context` | `context` (child of SummaryType) | Unchanged |
| `summary.scope_items[]` | `scope-item` / `ScopeItemType` | `simpleContent` + attrs flattened to object with `text`, `count`, `category` |
| `summary.scope_items[].text` | `ScopeItemType` base string (simpleContent) | XSD simpleContent value becomes explicit `text` property |
| `summary.scope_items[].count` | `@count` attribute on scope-item | XSD attribute becomes optional JSON property |
| `summary.scope_items[].category` | `@category` attribute on scope-item | XSD attribute becomes optional JSON property |
| `skills_mcp` | `skills-mcp` / `SkillsMcpType` | Mixed content flattened to two string arrays; `reason` attribute dropped (not used downstream) |
| `skills_mcp.skills[]` | `<skill>` children | Mixed content + reason attr simplified to plain string name |
| `skills_mcp.mcps[]` | `<mcp>` children | Mixed content + reason attr simplified to plain string name |
| `parallel_plan[]` | `ralph-parallel-plan` / `ParallelPlanType` / `work-unit` | Prefix removed; work-unit objects inlined |
| `parallel_plan[].id` | `@id` attribute on work-unit | Attribute becomes property |
| `parallel_plan[].description` | `description` child of WorkUnitType | Unchanged |
| `parallel_plan[].edit_area` | `edit-area` / `EditAreaType` | Nested `<paths><path>` and `<directories><directory>` flattened to flat arrays |
| `parallel_plan[].depends_on[]` | `dependencies` / `DependenciesType` / `depends-on/@unit-id` | Flattened from nested elements with attributes to plain string array |
| `steps[]` | `ralph-implementation-steps` / `step` / `StepType` | Container removed; prefix removed; step items directly in array |
| `steps[].number` | `@number` attribute (xs:positiveInteger) | Attribute becomes property; `integer` with `minimum: 1` |
| `steps[].title` | `title` child element | Unchanged |
| `steps[].step_type` | `@type` attribute / `StepTypeEnum` | Attribute becomes property; renamed to `step_type` to avoid JSON keyword collision; hyphens normalized to underscores (`file-change` becomes `file_change`) |
| `steps[].priority` | `@priority` attribute / `PriorityType` | Attribute becomes optional property |
| `steps[].targets[]` | `target-files` / `TargetFilesType` / `file` | Container removed; `file` elements become `targets` array items |
| `steps[].targets[].path` | `@path` attribute on TargetFileType | Attribute becomes property |
| `steps[].targets[].action` | `@action` attribute / FileActionType | Attribute becomes property |
| `steps[].location` | `location` child element | Unchanged |
| `steps[].rationale` | `rationale` child element | Unchanged |
| `steps[].content` | `content` / `RichContentType` | Entire rich content hierarchy (Paragraph, CodeBlock, Table, List, Heading) flattened to single markdown string |
| `steps[].depends_on[]` | `depends-on` elements / `DependsOnType` / `@step` | Flattened from elements with attribute to integer array |
| `critical_files` | `ralph-critical-files` / `CriticalFilesType` | Prefix removed |
| `critical_files.primary_files[]` | `primary-files` / `PrimaryFilesType` / `file` / `PrimaryFileType` | Container removed; attributes become properties |
| `critical_files.primary_files[].path` | `@path` attribute | Attribute becomes property |
| `critical_files.primary_files[].action` | `@action` attribute / FileActionType | Attribute becomes property |
| `critical_files.primary_files[].estimated_changes` | `@estimated-changes` attribute | Attribute becomes optional property; hyphen to underscore |
| `critical_files.reference_files[]` | `reference-files` / `ReferenceFilesType` / `file` / `ReferenceFileType` | Container removed; attributes become properties |
| `critical_files.reference_files[].path` | `@path` attribute | Attribute becomes property |
| `critical_files.reference_files[].purpose` | `@purpose` attribute | Attribute becomes property |
| `risks_mitigations[]` | `ralph-risks-mitigations` / `RisksMitigationsType` / `risk-pair` | Container and prefix removed; `risk-pair` becomes array items |
| `risks_mitigations[].severity` | `@severity` attribute / SeverityType | Attribute becomes optional property |
| `risks_mitigations[].risk` | `risk` child element | Unchanged |
| `risks_mitigations[].mitigation` | `mitigation` child element | Unchanged |
| `verification_strategy[]` | `ralph-verification-strategy` / `VerificationStrategyType` / `verification` | Container and prefix removed |
| `verification_strategy[].method` | `method` child element | Unchanged |
| `verification_strategy[].expected_outcome` | `expected-outcome` child element | Hyphen to underscore |

---

## Constraints Parity Checklist

When T9 implements the Rust deserializer for this JSON Schema, verify:

- [ ] `summary.scope_items` enforces `minItems: 3`
- [ ] `steps` enforces `minItems: 1`
- [ ] `steps[].number` enforces `minimum: 1` (positive integer)
- [ ] `steps[].step_type` defaults to `"file_change"` when absent
- [ ] `steps[].targets[].action` only accepts `create`, `modify`, `delete`
- [ ] `steps[].step_type` only accepts `file_change`, `action`, `research`
- [ ] `steps[].priority` only accepts `critical`, `high`, `medium`, `low`
- [ ] `risks_mitigations[].severity` only accepts `low`, `medium`, `high`, `critical`
- [ ] `critical_files.primary_files` enforces `minItems: 1`
- [ ] `risks_mitigations` enforces `minItems: 1`
- [ ] `verification_strategy` enforces `minItems: 1`
- [ ] All objects reject unknown properties (`additionalProperties: false`)
- [ ] Optional sections (`skills_mcp`, `parallel_plan`, `reference_files`) can be absent
- [ ] `parallel_plan[].depends_on` can be absent or empty array
- [ ] `steps[].depends_on` items are positive integers (`minimum: 1`)

---

## Key Differences from Old plan.xsd

1. **Rich content hierarchy eliminated**: `RichContentType`, `ParagraphType`, `CodeBlockType`, `TableType`, `ListType`, `HeadingType`, `InlineTextType`, `CellType`, `ListItemType` (9 complex types, 5+ nesting levels) all replaced by a single `"type": "string"` with markdown content.

2. **Mixed content eliminated**: `InlineTextType`, `ParagraphType`, `CellType`, `ListItemType`, `SkillsMcpType` all had `mixed="true"`. JSON has no mixed content; all flattened to strings.

3. **`ralph-` prefixes removed**: All 7 prefixed element names (`ralph-plan`, `ralph-summary`, `ralph-implementation-steps`, `ralph-critical-files`, `ralph-risks-mitigations`, `ralph-verification-strategy`, `ralph-parallel-plan`) use clean names.

4. **XML attributes inlined as properties**: 12 XSD attributes (`@number`, `@type`, `@priority`, `@path`, `@action`, `@estimated-changes`, `@severity`, `@id`, `@unit-id`, `@count`, `@category`, `@step`) become regular JSON properties.

5. **Container elements removed**: `ScopeItemsType`, `TargetFilesType`, `PrimaryFilesType`, `ReferenceFilesType`, `ImplementationStepsType`, `DependenciesType` wrapper elements eliminated; their children appear directly as array items.

6. **Kebab-case normalized to snake_case**: `file-change` -> `file_change`, `estimated-changes` -> `estimated_changes`, `expected-outcome` -> `expected_outcome`, etc.

7. **`skills_mcp` simplified**: `SkillsMcpType` with mixed content, `<skill>` and `<mcp>` children each having `@reason` attributes, simplified to two flat string arrays. The `reason` attribute is dropped (not consumed by downstream executor logic).

8. **`step.type` renamed to `step_type`**: Avoids collision with the JSON Schema keyword `type`.

9. **`ScopeItemType` simpleContent to object**: XSD `simpleContent` extension (string value + attributes) becomes an explicit object with `text`, `count`, `category` properties.
