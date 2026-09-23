# Issue tracker

This project uses local markdown files for issue tracking.

## Structure

Issues live under `.scratch/<feature>/` where each feature has its own directory containing:
- `issue.md` — the issue description
- `notes.md` — working notes
- Other supporting files

## Creating issues

Create a new directory under `.scratch/` with a descriptive name, then add `issue.md` with:

```markdown
# Issue title

## Description
What needs to be done.

## Acceptance criteria
- [ ] Criterion 1
- [ ] Criterion 2
```

## Reading issues

Skills should look for `.scratch/*/issue.md` to find all issues.
