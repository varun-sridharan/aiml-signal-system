# Style guide: how the Weekly Digest reads

A **copy**, taken 2026-10-20, of the style guide kept in project memory (`claude/style-guide-backlog.md`),
for the Framer to read from the repo. It holds how to write, never what to say: nothing here is a fact,
and nothing here may appear in a brief as a claim. The project-memory file stays the inbox the style
skills write to until go-live; refresh this copy then.

Two changes from the inbox version, both Varun's decisions of 2026-10-19 and 2026-10-20: **topic
labels are hashtags**, not a two-level category tag, and **depth is hybrid** (eight to ten short items,
the top one or two written as features). Examples are paraphrased here, because the repository is
public and the newsletters are someone else's writing. Sources: The Gen Academy's AI Weekly
(GenAcademy), Chamath Palihapitiya's weekly digest (Chamath), and Varun.

## Hard rules (checked by code; a failure blocks publishing)

1. **Every essay paragraph opens on a dated, attributed fact carrying a number**: who did what, when,
   for how much. An opening that announces something will be told ("Capability went ambient") fails.
   [Chamath]
2. **A named company or person is the subject of the sentence, never an abstract noun.** Write
   "Anthropic scoped its models' reasoning", not "Everything that grants permission did". Banned
   subjects: capability, permission, oversight, verification, the mechanism, the shape, the axis, the
   thing, the scarce good, and their relatives. [Chamath]
3. **No sentence restates its subject as its predicate.** Varun's example of what to block: *"Permission
   costs what it costs because verification costs what it costs."* Block `X costs what it costs`,
   `X is what it is` and their relatives. [Varun]
4. **A comparison is computed and both endpoints are shown.** "Grew about 25x, from $96 billion to $2.4
   trillion", never a bare ratio. [Chamath]
5. **No stage directions.** No sentence tells the reader how to read the next one: "Read that twice",
   "So ask what", "Here is the shape", "Notice that", "The point is". [Varun]

A paragraph of argument with no digit in it is almost always assertion; the checker flags it.

## Voice

- Write as someone who read the week and is telling a friend what mattered, not as a wire service. A
  light first-person frame is fine in section headings, never inside an item's facts. [Chamath]
- Calm and measured. No hype words; the specifics carry the weight. [GenAcademy, Chamath]
- **Keep what was announced apart from what is available or verified.** A preview is not a release, a
  promised download is not a download, and a company's own benchmark is its estimate. [GenAcademy]
- When the evidence cannot choose between readings, state them side by side without hedging, for
  example lenders under-pricing a risk, investors over-pricing a profit, or both. [Chamath]

## The edition, top to bottom

- **The week in one thread opens the edition**: one sentence naming how the week shifted, two or three
  stories as evidence, then the harder question the week raises. Its headline is a two-line contrast,
  and its key phrase is highlighted. [GenAcademy, Varun]
- **Edition and essay headlines are a fact or a pair of facts, never an abstraction.** "Four releases
  cut the cost of running a model. Three decisions restricted who may inspect one." [Varun]
- Number every story. [GenAcademy]
- **One tag pill per story (DO THIS, KNOW or RADAR), then one to four hashtags** for its topics.
  [Varun]
- When several stories share a theme, name it once with a divider. [GenAcademy]
- **Depth is hybrid:** eight to ten short stories, the top one or two written as features that build
  from the news to how it works, then the twist the headline number hides. [Varun, Chamath]
- **Close with "the thread through it all"**, which mirrors the opening: two contrasting lines, two
  sentences of summary, one concrete thing to try. The opening asks the question; the close answers
  it. [GenAcademy]
- Keep optional further reading out of the stories, in a short list after them. [Chamath]

## A story

- The headline states the consequence, what changed for the reader, not the topic's name.
  [GenAcademy]
- Four or five sentences: when, who and what was done, the specifics that matter, then the caveat.
  [GenAcademy, Chamath]
- **"Why it matters" names the trade-off rather than asserting importance**, for example that cheaper
  tokens make routing more useful while whole-task cost still matters more. [GenAcademy]
- When a story rests on a study or a benchmark, say what it did not cover: its sample, its window, what
  it left unmeasured. [GenAcademy, Chamath]
- Anchor a twist with one concrete comparison from the source, never an adjective. [Chamath]
- Every story links to its primary source, and every source is a real link. [GenAcademy, Varun]

## Sentences and words

- Define a technical term at first use, in a short plain clause right after it. [Chamath]
- Short declarative sentences, one idea each; prefer the plain verb ("lowered the cost", not "drove
  cost optimisation"). [GenAcademy, Chamath]
- Transitions name the next subject; they carry content, not rhetoric. [Chamath]
- State limitations as facts, in the flow, without throat-clearing. [Chamath]
- When experts disagree, put both views side by side, attributed, and do not pick a winner the
  evidence does not support. [Chamath]

## Leave out

- Adjectives doing the work of evidence: "massive", "groundbreaking", "game-changing".
- Unexplained jargon, and acronyms without their first expansion.
- Any sentence that only restates the headline. If it adds no specific, cut it.
