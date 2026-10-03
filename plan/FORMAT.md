# plan.md and roadmap.md — the format

These two files are the source of the Plan and Roadmap pages. The server renders them on
every request through `plan/plan.template.html` and `plan/roadmap.template.html`. There is
no JSON and no build step.

The rules below are what the renderer parses and what `scripts/check_plan_format.py`
enforces. **A file that breaks a rule fails the check, and the deploy stops.** That is on
purpose: a malformed file must never become a page that quietly shows the wrong thing.

Both files may use ordinary inline markdown (`**bold**`, `*em*`, `` `code` ``, links).
Inline HTML is allowed only where markdown cannot express it. HTML comments
(`<!-- ... -->`) are authoring notes: they are never rendered.

---

## Shared: front matter

Every file starts with YAML front matter. All five keys are required.

```yaml
---
title: Plan & Progress
kicker: AI/ML Signal · build plan
lede: One sentence under the title.
footer: Footer text.
updated: 2026-10-02
---
```

`updated` is `YYYY-MM-DD`. The site header and the navigation are NOT in these files.
They are identical on every page, so they live in the templates.

---

## plan.md

Top-level (`#`) headings, in this order:

1. **One or more milestones**, each `# M<n> · <title>`, for example
   `# M1 · Reading, Writing & Hosting`. The first one is the live milestone.
2. **Exactly one** `# Decision log`, last.

### Inside a milestone

```markdown
# M1 · Reading, Writing & Hosting

**End state:** One paragraph. What must be true for this milestone to be done.

## Plan

| Activity | Outcome | Why it matters | Status |
|---|---|---|---|
| The Framer agent | Writes the brief from verified items | ... | DONE |
| Storage layer, with the excerpt rule | ... | ... | TO DO · blocks paid runs |
| Agent hooks (bk-agent-hooks) | ... | ... | TO DO |

## Status

Prose, one or more paragraphs.
```

Rules:

- `**End state:**` paragraph is required, directly under the milestone heading.
- `## Plan` holds exactly one table with exactly these four columns, in this order. An
  optional intro paragraph may sit between the heading and the table.
- **Status** cell is exactly one of: `DONE`, `IN PROGRESS`, `TO DO`, or
  `TO DO · <blocker>` (a TO DO that blocks something, rendered with a warning pill).
  Uppercase, nothing else. Progress (`55% · 6 of 11`) is computed from these cells and is
  never written in the file.
- An activity adopted from the AI concepts backlog ends with its backlog id in
  parentheses, for example `(bk-agent-hooks)`, so the link back survives.
- `## Status` is required. Prose only.
- The `|` character inside a cell must be escaped as `\|`.

### Decision log

```markdown
# Decision log

### 2026-09-20 · The spend breaker now refuses the call that would cross the ceiling

Body, one or more paragraphs. At most five sentences.
```

- Each entry is `### YYYY-MM-DD · <title>`, newest first.
- Dates must be in descending order.

---

## roadmap.md

Top-level (`#`) headings are recognised by their text:

| Heading | Meaning |
|---|---|
| `# M<n> · <theme>` | a milestone |
| `# Backlog` | the backlog. Exactly one, last |
| any other `#` heading | a prose section, rendered in place (for example `# Where this is going`, `# After M3`) |

### A milestone

```markdown
# M2 · Two voices, one ledger

- **Status:** NEXT
- **One line:** Thought Leadership agent: predictions, and what named people said.
- **Why:** One or two sentences.
- **Depends on:** M1, which needs Reader's verified pool.
- **Also includes:** Optional.
- **Plan:** [Detailed plan](/plan)
- **Live plan:** Optional. Where the detailed plan stands, for example "arrives when M1 completes", and why.

### Scope

- **Concept triage, first.** ...
- ...

### Exit

- Binary, checkable criteria.

### Not in

- Deliberately excluded, with the reason.
```

Rules:

- The field list comes first. `Status` (`NOW`, `NEXT` or `LATER`), `One line` and `Why` are
  required. `Depends on`, `Also includes`, `Plan` and `Live plan` are optional. No other
  labels.
- Exactly one milestone has `Status: NOW`.
- `### Scope`, `### Exit` and `### Not in` are optional, each a bullet list, in that order.

### Backlog

```markdown
# Backlog

Optional intro paragraph.

## Eval & correctness

### bk-config-contract · Assert the config/code contract in the structural gate

- **Size:** S
- **Origin:** Sep-17-2026, found by a pre-commit check

Body, one or more paragraphs.
```

- Groups are `##` headings. Items are `### <id> · <title>`, ids start with `bk-` and are
  unique across the file.
- `Size` (`S`, `M`, `L`, or `—` for not yet sized) and `Origin` are required on every item.
- Counts shown on the page (items per group, total) are computed, never written.
