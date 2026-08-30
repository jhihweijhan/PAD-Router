# Domain Docs

How the engineering skills should consume this repo's domain documentation when exploring the codebase.

## Before exploring, read these

- `CONTEXT.md` at the repo root, or
- `CONTEXT-MAP.md` at the repo root if it exists: it points at one `CONTEXT.md` per context.
- `docs/adr/`: read ADRs that touch the area being changed.

If any of these files do not exist, proceed silently. The `/domain-modeling` skill creates them lazily when terms or decisions need recording.

## File structure

This is a single-context repo. Domain documentation, when needed, lives at the repo root:

```
/
├── CONTEXT.md
├── docs/adr/
└── src/
```

## Use the glossary's vocabulary

When output names a domain concept, use the term defined in `CONTEXT.md`. If the needed concept is not defined, reconsider the terminology or note the gap for `/domain-modeling`.

## Flag ADR conflicts

Surface any contradiction with an existing ADR explicitly rather than silently overriding it.
