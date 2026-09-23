# Triage labels

Issues move through these states via labels:

| Label | Meaning |
|-------|---------|
| `needs-triage` | Maintainer needs to evaluate |
| `needs-info` | Waiting on reporter for more information |
| `ready-for-agent` | Fully specified, an agent can pick it up |
| `ready-for-human` | Needs human implementation |
| `wontfix` | Will not be actioned |

## Applying labels

Add labels as frontmatter in `issue.md`:

```yaml
---
labels: [needs-triage]
---
```

Or as a list in the issue body:

```markdown
**Labels**: needs-triage
```
