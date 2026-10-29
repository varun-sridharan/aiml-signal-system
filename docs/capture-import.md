# The weekly capture import

Links reach the app **when you save them**. `/AGI-add-news` writes the link to `news-backlog.md` in the "AI/ML Latest News" project (the old digest still reads and empties that file in its own Friday run) and, in the same step, posts it to the app's `/add-news/batch` as a list of one. By Friday the week's links are already candidates, so nothing has to read the file before the old digest empties it, and **the 18:30 Friday scheduled task that this page used to describe is not used**. `ADD_NEWS_TOKEN` is set on the web service (Varun, 2026-10-26).

## What the app does with a batch

`POST https://aiml-signal-system-production.up.railway.app/add-news/batch`, with the header `X-Add-News-Token: <ADD_NEWS_TOKEN>`.

- **Off until `ADD_NEWS_TOKEN` is set** on the web service in Railway (a long random string you choose). Unset, the route answers 503. A missing or wrong token answers 403. The same token works for the single-link `/add-news`.
- **Free.** It fetches nothing and calls no model. The links join the week's candidates; the Friday run fetches them. A link you saved is fetched first, ranks with the best of the week, may surface on its own whatever site it is, is read even if its page is dated before the window, and is flagged **FROM YOUR BACKLOG** on its story if it makes the edition.
- **Idempotent.** Posting the same batch twice changes nothing: the second answer reports every link as `already-known`.
- Each link goes through the dedup layers that need no page: the address after safe clean-up (tracking parameters, `www.`, fragments, trailing slash, arXiv `abs`/`pdf` forms), the identity of a paper (the same arXiv or DOI id at another address is another sighting of the story already held), and the sites that refuse fetchers. The layers that compare text run in the Friday run over what was fetched.
- At most 100 links and 64 KB per batch; the task below sends 40 at a time.
- Only public web addresses are accepted: no user name or password, no ports other than 80 and 443, no IP numbers, no local names. A link a feed already found this week is flagged from your backlog and moved up; one that is older than a week or was skipped is reported `already-known` and not read again.

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

## How a link is sent, at capture time

`/AGI-add-news` is the only sender. Each time you use it, it posts `[{"url": "<the link>", "addedAt": "<when>", "note": "<your note>"}]` to the endpoint above with the `X-Add-News-Token` header, and tells you the app's answer for that link: `added`, `merged`, `already-known` or `rejected` with why. The token lives in the skill's own setup, never in the repository. Saving the same link twice is harmless (the second answer is `already-known`).

A batch of many links still works (at most 100 links and 64 KB, 40 at a time is comfortable), for example to send a whole backlog once by hand; the rules above are the same for one link or many.

### What success looks like

- Saving a link shows `received: 1` and `added`, `merged` or `already-known` (or `rejected` with the reason) in the same reply.
- Saving it again at once reports `already-known`.
- After a Friday run, the stories that came from your saved links show **FROM YOUR BACKLOG**.

If the app answers 503 (token not set) or 403 (token wrong), the link was not sent; it is still in `news-backlog.md`, and you can send it again. The Friday job is in [go-live.md](go-live.md).
