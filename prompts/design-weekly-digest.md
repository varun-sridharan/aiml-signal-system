# Claude Design prompt: the Weekly Digest template

Paste everything below the line into Claude Design. When Varun signs off on what it produces, Claude
Code turns it into `templates/digest.template.html`, rendered from the database on every request,
and every future weekly run fills it.

---

Design the **Weekly Digest** page for *AI/ML Signal.*: one page per weekly edition, read by a technical
product leader who knows ML well and has about 15 minutes. This is a **template, not a one-off page**.
Every block must be a repeatable slot that a weekly pipeline fills from data, so a week with 4 stories
and a week with 11 both look right.

## Look and feel

- **Editorial, calm, print-like.** A warm off-white page on a slightly darker warm-grey surround, and
  one centred reading column of about 680 px for prose, with a narrow right rail of about 220 px on
  wide screens.
- **Type.** A literary serif for headlines and body, at a large and generous size (headline about
  44 px, body about 18 px, line height 1.6). Small-caps monospace for kickers, labels, counts and
  dates, with wide letter-spacing. A single burnt-amber accent for active tabs, rules, footnote
  markers and link underlines. No other colour except a muted green for "verified" and a muted red
  for "miss / failed".
- **Header.** Keep the site header and top navigation **exactly as they are on the other pages**: the
  "AI/ML Signal." wordmark and the links Product · Weekly Digest · System Design · Roadmap & Backlog ·
  Plan & Progress. They are a shared include; draw them only as a placeholder bar.
- **No icons-as-decoration, no gradients, no cards with heavy shadows.** Hairline rules separate
  things.
- **Accessibility.** Contrast of at least 4.5:1, every control reachable by keyboard, a visible focus
  ring, and tabs implemented as a proper ARIA tablist.
- **Mobile.** Below 760 px the rail drops under the column, the tabs scroll horizontally and the tiles
  stack.

## Page frame

1. **The edition bar** (top right, monospace): `EDITION 09 · OCT-17-2026 TO OCT-23-2026`, and on the
   left a small "Weekly Digest" wordmark.
2. **Primary tabs:** `NEWS` · `PREDICTION (live/limit)` · `HOMEWORK (n)` · `ARCHIVE`. Each count sits
   in a small outlined pill. **A tab whose section is not built yet is not rendered at all**, so no
   "coming soon" badges. Design all four, and show in one small extra frame what the bar looks like
   with only NEWS and ARCHIVE.
3. Under NEWS, **sub-tabs** with counts: `THE WEEK IN ONE THREAD` · `LEARNING FOR THE WEEK` ·
   `THIS WEEK'S STORIES · 8` · `WHAT ARE INDUSTRY LEADERS TALKING? · 5`. A sub-tab bar that sticks to
   the top while scrolling.

## NEWS · The week in one thread (opens first)

An editor's note that ties the whole week together before any single story.

- A **two-line contrast headline** in large serif, for example
  *"Capability got cheaper. / Judgment didn't."* The second line sits in the accent colour.
- **One short paragraph**, 80–120 words. Its first sentence names how the week shifted; then two or
  three stories are named as the evidence, each a link that jumps to its tile; and it ends on the
  harder question the week raises.
- Under it, a thin **"9 signals worth your attention"** line, where the number is the edition's story
  count.

## NEWS · Learning for the Week

The essay: what the stories mean together, which none of them says alone.

- A kicker, then a **headline that states a fact or a pair of facts** (not an abstraction), a short
  accent rule, and **6–12 paragraphs** of continuous prose.
- **Footnote markers** (superscript, accent colour) on every proper noun or term. Hovering or
  focusing a marker shows the definition in a **tooltip**, and the marker still links to a
  **numbered notes list** under the essay.
- **The right rail:** "WHAT IT WAS DRAWN FROM", a list of the story headlines it used, each linking
  to its tile; then the edition's numbers (considered, synthesised, minutes of source reading) in
  small monospace; then a one-paragraph note on how the edition was made.
- It closes with **"What to do differently this week"**: one practical instruction, in a quietly
  highlighted box.

## NEWS · This Week's Stories

Numbered tiles in one column, in the register of a good weekly newsletter. **Depth is hybrid: 8–10
short tiles, of which the top one or two are FEATURES.** A feature has the same header but a longer
body (300–500 words that build from the news to how it works, then the twist the headline number
hides), and it may show two named experts' views side by side. A feature is visibly larger, but it is
still one numbered tile in the same list.

- **Each tile:**
  - a large monospace number (`01/`);
  - a **two-level category tag** (`MODELS / COST`, `DEVELOPERS / EVALUATION`);
  - a tag pill (`DO THIS`, `KNOW` or `RADAR`);
  - a **headline that states the consequence**;
  - 3–5 sentences of what happened, with specifics;
  - a **"WHY IT MATTERS /"** line naming the trade-off;
  - optionally, **"Try it"**, a worked example of 2–4 sentences in a slightly inset block;
  - optionally, **"In 90 seconds"**, a three-row Method / Result / Caveat table for papers;
  - optionally, **"↔ Ties to"**, one line connecting the tile to another story, linking to it;
  - the **source line** (`Primary: github.blog · Hugging Face Papers`), with a small green
    **"checked against source"** mark;
  - **feedback**: `▲ Useful` / `▼ Not useful`, and a one-line text box that appears only after
    `▼`.
- **Theme dividers** between groups of tiles: a full-width hairline with a centred monospace label,
  for example `THE SHIFT / AGENTS NEED CONTROL PLANES`. The dividers come from data; a week may have
  none.
- A tile can be marked **"from your backlog"** with a small accent flag, because the reader likes to
  see his own submissions land.
- The section ends with **"THE THREAD THROUGH IT ALL"**: a two-line contrast (it mirrors the opening
  thread and answers its question), two sentences of summary, and **one concrete thing to try this
  week**.

## NEWS · What are industry leaders talking?

The reactions of named people, laid out as **testimonial tiles**.

- **A two-column grid of tiles.** Each tile has:
  - a large opening quotation mark in the accent colour;
  - **the quote, verbatim**, in serif italic, with any truncation shown visibly (`…`) and a
    "read the full post ↗" link;
  - under it, a **monogram circle** with the person's initials (no photos), then **name**,
    role · organisation, and the date;
  - a small type badge, `POST` (short, the pulse) or `EXCERPT` (longer, the substance);
  - a **"WHY IT MATTERS /"** line.
- **"Where they disagree"**: when two people contradict each other on the same event, show their
  tiles **side by side under one shared heading**, with a thin vertical "vs" rule between them and a
  one-line note on what exactly they disagree about.
- It closes with **"The common thread"**: one short paragraph on what the voices add up to, or why
  they split.
- **The empty state** says it plainly: "Nothing quotable surfaced this week." It is never padded.

## PREDICTION

A ranked board of falsifiable claims with a public track record.

- **The record strip** at the top: `HIT 2 · MISS 1 · OPEN 3`, with a one-line note on the limit
  (`Board 3/3: a new prediction must displace one`).
- **Each prediction card:**
  - a rank number;
  - **the claim**, as one bold falsifiable sentence;
  - a **"Resolves by" date** chip and the **hit criteria** (exactly what must be true by then);
  - "Why" with cited sources;
  - "What a smart operator would do", naming anyone already doing it;
  - "Who is reacting badly, and the flaw in their logic";
  - a status pill: `OPEN` (amber), `HIT` (green) or `MISS` (red).
- **Scored predictions** (hit or miss) collapse into a "Scored" list underneath. A miss shows its
  **post-mortem**, what the reasoning got wrong, and is never hidden.
- The empty state: "No new prediction this week. Nothing cleared the bar."

## HOMEWORK

One exercise of an hour or less, grounded in the reader's own code.

- **The exercise card:**
  - a title;
  - a **time box chip** (`≤ 60 min`);
  - "Why this week": a link to the story it comes from;
  - **"Where in your code"**: a list of `repo / path / function` references, each with a green
    **"verified to exist"** tick or a red "not found" cross;
  - numbered **steps**;
  - **"Done when"**: checkable acceptance criteria;
  - a **"How long did it really take?"** input (minutes) with a Submit button.
- **Previous homework** sits in a compact list with status (done / skipped) and the recorded time.
- The empty state: "No homework this week."

## ARCHIVE

Past editions, newest first: edition number, date range, the opening thread's two-line headline,
story count, and a link. **Include a row for the "Sample edition · week ending 2026-08-04".** Retired
predictions also appear here, each with its reason.

## Page states (design each one)

- **Generating:** "Generating this week's edition…", and last week's edition stays readable underneath.
- **Stale:** "This week's run hasn't finished. You are reading last week's edition."
- **Quiet week:** "Quiet week. Nothing cleared the bar. Go build."
- **Failed:** "No edition this week: the run stopped before writing anything", with the cause in
  plain words.
- **A section with nothing this week** shows its one honest empty line, never a placeholder badge.

## What to deliver

1. The page, filled with **realistic sample content** for every slot (an obvious "SAMPLE" chip at the
   top). Use 9 stories (the first one a feature), 2 theme dividers, 5 leader tiles including one disagreement pair, 3
   predictions with 1 scored miss, and 1 homework exercise.
2. **A slot list:** every repeatable block and field, with its name, type, whether it is optional, and
   its empty state, so the pipeline can be built to fill exactly these slots.

---

## Revision 1 · 2026-10-20 · Varun's feedback on the first design (six changes)

Paste everything below into the same Claude Design project, as a follow-up.

---

Good first version; keep everything not mentioned here exactly as it is. Six changes, then re-export
the page and the slot list.

**1. The four NEWS sections collapse.** "The week in one thread", "Learning for the week", "This
week's stories" and "What are industry leaders talking?" each get a disclosure arrow before the
heading (▸ collapsed, ▾ open), and **all four start collapsed**, showing only the heading, its count and
a one-line preview. A section opens when the reader clicks the arrow or the heading, or picks it from
the sticky sub-tab bar (which also scrolls to it). Add a small "Expand all / Collapse all" control at
the right of the sub-tab bar. Each heading is a real button with `aria-expanded`. Opening and closing
animate quickly (about 150 ms) and never shift the page above the section.

**2. Each NEWS section has its own colour.** Give the four sections four distinct, muted hues that
sit well with the warm off-white page, used for the section's arrow, kicker, heading underline, a
3 px left rule down the open section, and its sub-tab marker. Keep burnt amber (#B5502A) for the first
section, since it is the brand accent. Do not use green or red for any section: those mean "verified"
and "miss / failed". All four must stay distinguishable for colour-blind readers and keep text
contrast at 4.5:1 or better, and the colour is never the only cue: the label always shows.

**3. Thumbs replace the text buttons.** Swap "▲ Useful / ▼ Not useful" for two icon buttons, thumbs
up and thumbs down, outline by default and filled in the section colour when chosen, with
`aria-label`s "Useful" and "Not useful" and a visible focus ring. The one-line reason box still
appears only after thumbs down.

**4. One pill, then hashtags.** Each story keeps exactly one pill beside its number: `DO THIS`, `KNOW`
or `RADAR`. Replace the two-level category tag (`MODELS / COST`) with **hashtags** for the topics,
for example `#Agents` `#Permissions` `#ModelContextProtocol` `#OpenWeights`: one to four per story, in
small muted monospace, on one line under the headline. They are labels for skimming, not links.

**5. Sources are real links.** The source line lists every source as a link to the exact article,
labelled by publication: `Primary: GitHub Blog ↗ · Also: Hugging Face Papers ↗, The Decoder ↗`. The
primary source comes first and is the only one under "Primary"; links open in a new tab. Keep the
green "checked against source" mark after them.

**6. Homework time is filled in from Claude Code.** The "How long did it really take?" box arrives
**pre-filled with the time measured in Claude Code** for that exercise (for example `47`), with a
small note under it: "Measured from your Claude Code sessions on this repo · 2 sessions, Oct 21–22.
Edit if it's off." The reader can change the number; once changed, the note reads "Edited by you
(measured: 47 min)" so both numbers stay visible, and Submit saves the edited one. If nothing was
measured, the box is empty with the note "Not measured: no Claude Code session found for this
exercise." Show all three states (measured, edited, not measured). In "Previous homework", each row
shows the saved minutes with a small `measured` or `edited` marker.

**Update the slot list to match:** `cat` becomes `hashtags[]` (1–4 strings); `sources` becomes
`sources[]` of `{label, href, primary}` with exactly one `primary: true`; add `section.color` for each
of the four NEWS sections; add `homework.measured` as `{minutes, sessions, from, to}` (optional; absent
means not measured) and a `minutesSource` of `measured` or `edited` on each `previous[]` row; feedback
and the reader's edit stay UI state, not slots.

---

## Revision 2 · 2026-10-20 · headline highlights in each section's colour

Paste everything below into the same Claude Design project, as a follow-up.

---

One change; keep everything else exactly as it is.

**Each NEWS section's headline highlights its key phrase in that section's colour**, the way "The week
in one thread" already does with *Judgment didn't.* in burnt amber. Apply the same treatment to the
other three:

- **Learning for the week** (blue): highlight the turn of the headline, for example
  "Inference fell 80% in a quarter. The cost of checking an answer **did not move.**"
- **This week's stories** (purple): for example "What happened, **in order of consequence**".
- **What are industry leaders talking?** (olive): for example "Five voices on **the same week**".

Rules for the highlight:

- **One phrase per headline**, at most about five words, and the rest of the headline stays in the
  body ink colour. It marks the turn or the consequence, never a whole sentence.
- The highlight is **the same hue as its section's kicker, arrow and rule**, so a section reads as one
  colour family. Check every highlight colour at 4.5:1 or better against the page at headline size;
  darken a hue slightly for headline text if needed, keeping the family recognisable.
- It is visible when the section is collapsed (in the collapsed header) and when it is open.
- A headline may have no highlight; it then renders entirely in ink, never with a forced highlight.

**Slot list:** a headline carries its highlight as an inline token, `[[hl|phrase]]`, for
`thread.lines`, `learning.headline`, `stories.title` and `leaders.title`. At most one token per headline.
Re-export the page and the slot list.
