# Story — the script behind the video on the Product page

This file is the source of `application/media/story.mp4`. The video is the rendered output;
this is what gets reviewed, diffed and approved. Change the story here first, then re-export
the video from it.

**When it changes:** at the end of every milestone, and only then. The closing step of each
milestone's plan adds that milestone's chapter below, drafted from `plan/plan.md` and its
decision log, so the chapter tells what actually happened rather than what was planned.

**The approval gate:** Varun approves the script, then the video. Only an approved video is
committed, as `application/media/story.mp4`, and tagged `story-m<n>`.

**Rules for writing it**

- Every claim must be true of the system as it stands at the version's tag. Something
  designed but not built is called designed.
- Parts 1 and 2 are evergreen. They change only if the vision or the milestones change.
- Narration is spoken, so short sentences and no jargon a product leader would stumble on.
- Target 2–3 minutes for parts 1 and 2. Each finished milestone adds about a minute.

| Version | Tag | Covers | Status |
|---|---|---|---|
| v0 | — | Parts 1 and 2 | Draft, not yet approved |

---

## Part 1 · The business story

### Scene 1.1 · The gap

**Narration.** Every week, the technology your business runs on gets better. New models,
cheaper ways to run them, techniques that would make your product faster or smarter. Almost
none of it reaches your product. Not because nobody cares, but because finding the one change
that matters, and then actually building it, takes time no team has.

**On screen.** A stream of new releases and papers flowing past a product that stays still.

### Scene 1.2 · The idea

**Narration.** So imagine a system that does this continuously. It watches what is new. It
understands your business: your goals, and where your product stands today. It works out which
of those changes actually matters to you. It reads your code to find exactly where the change
would go. It recommends it. And then it builds it, tests it, and merges it into your product.

**On screen.** One loop, left to right: *Watch → Understand → Decide → Locate → Recommend →
Build → Test → Merge*, returning to *Watch*.

### Scene 1.3 · People at the gate

**Narration.** Not on autopilot. People stay in control at the moments that matter. You
approve a recommendation before anything gets built. You review the scaffolding before it is
merged. The system does the searching, the reading and the drafting; people make the calls.

**On screen.** The same loop, with two gates lit: *Approve the recommendation* before Build,
*Review before merge* before Merge.

### Scene 1.4 · Why it matters

**Narration.** The payoff is speed from idea to value. A capability that would have taken a
quarter to notice, justify and build arrives in weeks, and every step of the way someone with
judgement said yes.

**On screen.** *A business that keeps improving itself, with people at the gate.*

---

## Part 2 · The first application: AI/ML Signal

### Scene 2.1 · Why start here

**Narration.** The first place to prove this pattern is the first step of the loop: knowing
what is worth knowing. AI/ML Signal is being built to read the week's AI news, decide what
matters to one reader, explain why, and check its own facts before anyone sees them. It comes
in three milestones, and each one has an end state you can check.

**On screen.** The loop from Part 1, with *Watch*, *Understand* and *Decide* highlighted as
the part this system takes on first.

### Scene 2.2 · Milestone 1 · Reading and writing, hosted

**Narration.** Milestone one builds a system that reads and writes on its own, in the cloud.
One agent will gather the week's sources, remove duplicates and keep the evidence. Another
writes the weekly brief. Both are checked by code before anything is published: every fact
has to trace back to the stored source.

**End state, on screen.** *The Reader and the writer run in the cloud, both gated, producing
the weekly brief at a public address.*

### Scene 2.3 · Milestone 2 · Two voices, one ledger

**Narration.** Milestone two holds opinions to account. A new agent will report what named
experts actually said, quoted word for word and checked against the source, and make
predictions that can be proven wrong, each with a date when it will be judged.

**End state, on screen.** *Every quote verified against its source. Every prediction scored
Hit, Miss or Expired unresolved.*

### Scene 2.4 · Milestone 3 · Practice

**Narration.** Milestone three moves from reading to doing. A coach will read your own code,
find a real place where this week's idea would fit, and write an exercise of an hour or less
for exactly that spot. Every file and function it names is checked to exist before the
exercise is shown.

**End state, on screen.** *Homework grounded in your own repository, every reference
verified, and the time it really took recorded.*

### Scene 2.5 · Where this leads

**Narration.** Milestone three is the first time the system touches your code, as a
suggestion you act on yourself. Building, testing and merging the change automatically is the
destination from Part 1, not a claim about today. Each milestone earns the trust the next one
needs.

**On screen.** The full loop again: the first stages lit, the rest outlined.

---

## Part 3 · Milestone chapters

Each chapter is added when its milestone completes, from that milestone's plan and decision
log. It explains, at a high level, what was built, what was learned, and what changed from
the plan.

### Chapter M1 · Reading and writing, hosted

*Written when M1 completes.*

### Chapter M2 · Two voices, one ledger

*Written when M2 completes.*

### Chapter M3 · Practice

*Written when M3 completes.*
