# Domain docs

This project uses a single-context layout.

## Structure

- `CONTEXT.md` — project domain language, key concepts, business rules
- `docs/adr/` — architectural decision records

## Reading context

Skills should read `CONTEXT.md` first to understand the domain, then consult `docs/adr/` for specific architectural decisions.

## Creating ADRs

When making significant architectural decisions, create an ADR in `docs/adr/` using this format:

```markdown
# ADR-NNN: Title

## Status
Accepted | Deprecated | Superseded by ADR-NNN

## Context
What is the issue that we're seeing that is motivating this decision?

## Decision
What is the change that we're proposing and/or doing?

## Consequences
What becomes easier or more difficult to do because of this change?
```
