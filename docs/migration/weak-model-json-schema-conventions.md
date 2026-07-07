# Weak-Model-Compatible JSON Schema Conventions

> Design guide for JSON Schemas that weak executor models can reliably follow.
> Applies to all artifact schemas used in the MCP artifact submission flow.

---

## Rules

### 1. Maximum Nesting Depth: 3 Levels

Schemas must not exceed 3 levels of nesting:

```
Level 0: root object
Level 1: direct properties (including arrays)
Level 2: items within arrays (objects)
Level 3: leaf properties within those objects
```

No deeper. If you need more depth, flatten the structure.

### 2. No `$ref`

All types must be inlined. Never use JSON Schema `$ref` references.

Weak models lose context when navigating reference chains. They cannot reliably
resolve `$ref` to understand the expected shape at a given location.

### 3. No `oneOf`

Never use `oneOf` discriminators. Weak models cannot reliably pick the correct
variant from a `oneOf` set.

Use `anyOf` with flat discriminated objects instead (see Rule 6).

### 4. Enum Policy: Maximum 7 Values

Every `enum` must have at most 7 values. Each enum value must also be listed in
the property's `description` field.

When more than 7 values are needed, split into multiple properties or use a
category + subcategory pattern.

### 5. Explicit `required` Arrays

Every object must have an explicit `required` array listing ALL mandatory fields.
Never rely on defaults or implicit optionality.

If a field is optional, document it as such in the `description`.

### 6. `anyOf` Pattern for Discriminated Unions

When a property can be one of several shapes, use `anyOf` with flat discriminated
objects. Each variant must:

- Be a complete, self-contained object (no shared base)
- Have a `type` property with a `const` value as the discriminator
- Have its own `required` array

```json
{
  "anyOf": [
    {
      "type": "object",
      "properties": {
        "type": { "const": "commit" },
        "subject": { "type": "string" }
      },
      "required": ["type", "subject"],
      "additionalProperties": false
    },
    {
      "type": "object",
      "properties": {
        "type": { "const": "skip" },
        "reason": { "type": "string" }
      },
      "required": ["type", "reason"],
      "additionalProperties": false
    }
  ]
}
```

### 7. No `additionalProperties: true`

Always set `additionalProperties: false` on objects. This prevents models from
inventing fields and makes validation errors specific.

### 8. String Content for Rich Text

Use `"type": "string"` for any content that was previously mixed XML content.
Do not attempt to represent inline markup, paragraphs, or rich formatting in the
JSON Schema. The string can contain markdown if needed.

### 9. Array Items Must Be Specified

Every array must have an explicit `items` schema. Use `minItems` and `maxItems`
where there are known bounds.

### 10. Description on Every Property

Every property MUST have a `description` field that explains:
- What the field represents
- What values are valid (especially for enums)
- Any constraints (length, format, etc.)

---

## Anti-Patterns

| Pattern | Why Weak Models Fail |
|---|---|
| `$ref` chains | Models lose context navigating reference indirection. They produce shapes that match the referencing site but not the referenced definition. |
| `oneOf` discriminators | Models cannot reliably select the correct variant. They often merge fields from multiple variants or pick arbitrarily. |
| Nesting > 3 levels | Models lose track of which level they are at. Deeper objects get truncated or malformed. |
| Enums > 7 values | Models hallucinate invalid enum values or confuse similar-sounding options. |
| Implicit required | If fields are not listed in `required`, models treat them as optional and omit them even when semantically mandatory. |
| Mixed content (XML-style) | Models cannot generate interleaved text + structured elements. Use plain strings instead. |
| `additionalProperties: true` | Models invent plausible-sounding but invalid fields, making debugging harder. |

---

## Example: Good vs Bad

### Bad (deeply nested, uses $ref and oneOf)

```json
{
  "definitions": {
    "InlineElement": {
      "oneOf": [
        { "type": "string" },
        { "$ref": "#/definitions/Emphasis" },
        { "$ref": "#/definitions/CodeSpan" }
      ]
    },
    "Emphasis": {
      "type": "object",
      "properties": {
        "emphasis": { "type": "string" }
      }
    },
    "CodeSpan": {
      "type": "object",
      "properties": {
        "code": { "type": "string" }
      }
    },
    "Paragraph": {
      "type": "object",
      "properties": {
        "content": {
          "type": "array",
          "items": { "$ref": "#/definitions/InlineElement" }
        }
      }
    }
  },
  "type": "object",
  "properties": {
    "steps": {
      "type": "array",
      "items": {
        "type": "object",
        "properties": {
          "content": { "$ref": "#/definitions/Paragraph" }
        }
      }
    }
  }
}
```

Problems: `$ref` chains, `oneOf`, 4+ levels of nesting.

### Good (flat, inlined, no $ref)

```json
{
  "type": "object",
  "properties": {
    "steps": {
      "type": "array",
      "description": "Implementation steps in execution order.",
      "minItems": 1,
      "items": {
        "type": "object",
        "properties": {
          "number": {
            "type": "integer",
            "description": "Step number, starting from 1.",
            "minimum": 1
          },
          "title": {
            "type": "string",
            "description": "Short imperative title for this step."
          },
          "content": {
            "type": "string",
            "description": "Step description. May contain markdown."
          }
        },
        "required": ["number", "title", "content"],
        "additionalProperties": false
      }
    }
  },
  "required": ["steps"],
  "additionalProperties": false
}
```

All types inlined, max 3 levels, every field described, explicit `required`.
