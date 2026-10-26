# Go live: the Friday job in Railway

What you do, in order. Nothing here has been done for you: the repository only holds the entrypoint and these steps.

## What the job is

`python -m agents.weekly --scheduled` runs the whole weekly edition (Reader, Framer, gates, publish) for the current ISO week. It is built to be started by a cron that fires twice and to do the right thing both times:

- It runs **only** when the Pacific clock says Friday, 19:00 to 19:59. Railway cron is in UTC, so you give it two entries, Saturday 02:00 and Saturday 03:00 UTC. Whichever side of daylight saving the year is on, exactly one of them is Friday 19:00 Pacific; the other one prints `outside Friday 19:00 to 19:59 Pacific; nothing done` and exits 0. (Summer: 02:00 UTC. Winter: 03:00 UTC.)
- It is **idempotent**. If the week already has a published edition it prints `already published` and exits 0 without a database write, a model call or a fetch.
- It **resumes**. Every stage is checkpointed; if a run died after the Reader, the next run in the window starts at the Framer and does not pay for the Reader again. A week whose gates failed is a failed stage, and the next run retries it.
- It writes **one status line** to the log:
  - `scheduled: 2026-W44 published (2026-W44, from reader) at 2026-10-31 02:41 UTC; stages reader, framer, gates, publish`
  - `scheduled: 2026-W44 is already published (2026-10-31 02:41 UTC); nothing done`
  - `scheduled: 2026-W44 stopped: <reason>` (exit code 1)
- It takes no other option; it works out the week itself.

`/health` shows the result from outside: `"last_edition": {"week": "2026-W44", "published_at": "2026-10-31T02:41:07Z"}`.

## Steps in Railway

1. **Create the service.** In the project that holds the web service and Postgres: New, then Service from the same GitHub repository, branch `main`. Name it `weekly-edition`.
2. **Make it a Cron service.** Service Settings, Deploy: set **Custom Start Command** to
   ```
   python -m agents.weekly --scheduled
   ```
   (The repository's `Procfile` starts the web server; the start command here replaces it for this service only. The same build applies.)
3. **Set the schedule.** Service Settings, Deploy, **Cron Schedule**:
   ```
   0 2,3 * * 6
   ```
   That is 02:00 and 03:00 UTC on Saturdays (cron day 6).
4. **Set the variables, by reference where Railway offers it.** Only these two are needed:
   - `DATABASE_URL`: the reference to the Postgres service's private URL (`${{Postgres.DATABASE_URL}}`), the same value the web service uses.
   - `ANTHROPIC_API_KEY`: the same key as the web service (reference it from the web service, `${{<web service>.ANTHROPIC_API_KEY}}`, or paste it once).

   Do **not** set `ADD_NEWS_TOKEN` or `VOTE_SALT` here; they belong to the web service. `GATE` is optional and defaults to `claims`, the gate this system ships with.
5. **Deploy it.** The service builds and then waits for its schedule. It does not run on deploy.

## Trigger one run by hand

The scheduled entrypoint refuses to run outside Friday 19:00 Pacific, on purpose, so a hand-started run uses the plain command, which does the same work for the current week and resumes the same checkpoints:

- From your laptop, with the tunnel open (`railway connect Postgres --tunnel-only --port 15432` in another terminal and the API key in `.env`):
  ```
  .venv/bin/python -m agents.weekly
  ```
  Add `--status` to see every attempt at the week without any model call.
- Or, to exercise the Railway service itself, redeploy or run it from the dashboard during the Friday hour (what Railway offers for a cron service is its own; outside the hour the job does nothing by design).

A run that stopped (`scheduled: ... stopped: ...`) is not retried until next Friday. Read the reason, fix it if it needs fixing, and run the plain command by hand; it resumes where the stopped run left off.

## What success looks like

Within about 15 minutes of Friday 19:00 Pacific:

1. The service log has the line `scheduled: 2026-Wnn published (...)` and the deployment shows as completed (exit 0).
2. `https://aiml-signal-system-production.up.railway.app/health` shows `last_edition.week` as the current ISO week, and `published_at` a few minutes after 02:00 (summer) or 03:00 (winter) UTC.
3. `/weekly-digest` shows the new edition at the top of the archive.
4. For the first four editions, `/weekly-digest/review` lists what the claim gate held, removed or rewrote. Each run costs about $1.30 to $1.80; the month's ceiling is $40 and the stop line is $35.

The second cron entry of the same night (or any rerun) logs `outside ... nothing done` or `already published` and changes nothing.

## What stays on until you decide

The old scheduled Weekly Digest keeps running in parallel. Switching it off, the story video and the citation gate are separate decisions.

The Friday capture import (reading `news-backlog.md` and posting it to `/add-news/batch` at 18:30 Pacific, before this job starts) is set up in [capture-import.md](capture-import.md).
