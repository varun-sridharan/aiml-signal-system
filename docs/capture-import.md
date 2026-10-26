# The weekly capture import

You keep saving links with `/AGI-add-news`, which writes them to `news-backlog.md` in the "AI/ML Latest News" project. The old digest empties that file in its own Friday run, so the import has to read it **before 19:00 Pacific on Friday**. A scheduled task in the Claude app does that at 18:30 and posts the week's links to the app. The app side is built; the task is yours to create (below). Nothing has been created for you.

## What the app does with a batch

`POST https://aiml-signal-system-production.up.railway.app/add-news/batch`, with the header `X-Add-News-Token: <ADD_NEWS_TOKEN>`.

- **Off until `ADD_NEWS_TOKEN` is set** on the web service in Railway (a long random string you choose). Unset, the route answers 503. A missing or wrong token answers 403. The same token works for the single-link `/add-news`.
- **Free.** It fetches nothing and calls no model. The links join the week's candidates; the Friday run fetches them. A link you saved is fetched first, ranks with the best of the week, may surface on its own whatever site it is, is read even if its page is dated before the window, and is flagged **FROM YOUR BACKLOG** on its story if it makes the edition.
- **Idempotent.** Posting the same batch twice changes nothing: the second answer reports every link as `already-known`.
- Each link goes through the dedup layers that need no page: the address after safe clean-up (tracking parameters, `www.`, fragments, trailing slash, arXiv `abs`/`pdf` forms), the identity of a paper (the same arXiv or DOI id at another address is another sighting of the story already held), and the sites that refuse fetchers. The layers that compare text run in the Friday run over what was fetched.
- At most 100 links and 64 KB per batch.

### The payload

A JSON list, one object per link, exactly as the backlog stores them:

```json
[
  {"url": "https://example.com/post", "addedAt": "2026-10-23T09:12:00Z", "note": "why I saved it"},
  {"url": "https://arxiv.org/abs/2610.01234", "addedAt": "2026-10-24T18:40:00Z", "note": ""}
]
```

- `url`: required, an absolute http(s) address of at most 250 characters.
- `addedAt`: when you saved it (any text; it is accepted and not used yet).
- `note`: your own words (accepted and not used yet; it is never copied into an edition).

An object `{"links": [ ... ]}` is accepted as well.

### The answer

```json
{"received": 3, "added": 1, "merged": 1, "already_known": 0, "rejected": 1,
 "links": [
   {"url": "https://example.com/post", "canonical": "https://example.com/post", "outcome": "added", "detail": "...", "item": "<id>"},
   {"url": "https://huggingface.co/papers/2610.01234", "outcome": "merged", "into": "<item id>", "story": {"week": "2026-W43", "story": "s03"}, "detail": "the same arxiv id as an item already held; kept as another sighting of it"},
   {"url": "ftp://x", "outcome": "rejected", "detail": "not an absolute http(s) address of at most 250 characters"}
 ]}
```

Outcomes: `added`, `merged` (the same paper as an item already held; `story` appears when that item is already in a published edition), `already-known`, `rejected` (with why).

## The scheduled task to create in the Claude app

Create a scheduled task in the Claude app, **every Friday at 18:30 Pacific**, in the "AI/ML Latest News" project (so it can read `news-backlog.md`), with this prompt. Put the real token in the prompt where it says `<ADD_NEWS_TOKEN>` inside the Claude app only; never in this repository.

```
Read the file news-backlog.md in this project. Do not change it, move anything out of it or empty it: the old digest does that in its own Friday run.

Every entry in it is a link I saved with /AGI-add-news. Make a JSON list with one object per entry, in file order:
  {"url": "<the link, exactly as saved>", "addedAt": "<the date and time it was saved, ISO 8601, or an empty string if the entry has none>", "note": "<my note on it, or an empty string>"}
Include every entry that has a link. Do not include the same link twice. Do not summarise, fetch or open any link.

If the list is empty, stop and say "nothing to import this week". Otherwise POST the list as the JSON body to
  https://aiml-signal-system-production.up.railway.app/add-news/batch
with the header  X-Add-News-Token: <ADD_NEWS_TOKEN>  and Content-Type: application/json (for example with curl --data @file). Send at most 100 links per request; if there are more, send them in several requests in order.

Then report, for each request, the answer's counts (received, added, merged, already_known, rejected) and list every rejected link with its detail. If the answer is not HTTP 200, say so and show the status and the detail; do not retry more than once. Do not post anything anywhere else.
```

### What success looks like

- The task's last message shows `received` equal to the number of entries in the file, with each link `added`, `merged` or `already-known`, and any `rejected` explained.
- Running it again at once reports `added: 0` and every link `already-known`.
- After the Friday run, the stories that came from the file show **FROM YOUR BACKLOG**.

If the task cannot read the file, or the app answers 503 (token not set) or 403 (token wrong), nothing was imported; fix that and run the task by hand before 19:00. The Friday job is in [go-live.md](go-live.md).
