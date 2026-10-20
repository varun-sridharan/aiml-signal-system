-- The spend ledger: one row per API call, in Railway Postgres.
--
-- Applied idempotently by agents/budget.py (ensure_schema) on first connect and at app
-- startup. No migration framework yet; the storage layer will add one, and this file
-- becomes its first migration.
--
-- A row is born 'reserved' at the call's worst-case cost, inside the same transaction
-- as the ceiling check, and becomes 'settled' at the actual cost once the response is
-- back. A reservation whose process died mid-call stays at worst case and keeps
-- counting toward the month, which errs on the side of spending less.

CREATE TABLE IF NOT EXISTS spend_ledger (
    id                  uuid           PRIMARY KEY,
    ts                  timestamptz    NOT NULL,
    user_id             text           NOT NULL,
    phase               text           NOT NULL,
    model               text           NOT NULL,
    input_tokens        integer        NOT NULL DEFAULT 0,
    output_tokens       integer        NOT NULL DEFAULT 0,
    cache_read_tokens   integer        NOT NULL DEFAULT 0,
    cache_write_tokens  integer        NOT NULL DEFAULT 0,
    estimated_cost_usd  numeric(10, 6) NOT NULL CHECK (estimated_cost_usd >= 0),
    state               text           NOT NULL CHECK (state IN ('reserved', 'settled'))
);

-- Month-to-date is a range scan on ts.
CREATE INDEX IF NOT EXISTS spend_ledger_ts_idx ON spend_ledger (ts);


-- ======================================================================================
-- The excerpt store: article text, stored once at fetch time and never re-fetched.
--
-- The faithfulness gate checks every quoted span as a literal substring of the excerpt,
-- so an excerpt must stay byte-identical to what the writer saw. That rule is enforced
-- here, by the database, rather than by every caller remembering it.
-- ======================================================================================

CREATE TABLE IF NOT EXISTS excerpts (
    id              uuid         PRIMARY KEY,
    canonical_url   text         NOT NULL,
    domain          text         NOT NULL,
    fetched_at      timestamptz  NOT NULL,
    content_sha256  text         NOT NULL CHECK (content_sha256 ~ '^[0-9a-f]{64}$'),  -- of the UTF-8 plain text
    text_z          bytea        NOT NULL,                                           -- zlib(UTF-8 text)
    text_bytes      integer      NOT NULL CHECK (text_bytes > 0),
    stored_bytes    integer      NOT NULL CHECK (stored_bytes > 0),
    expires_at      timestamptz  NULL,                                               -- NULL: kept forever
    UNIQUE (canonical_url, content_sha256)
);

CREATE INDEX IF NOT EXISTS excerpts_expires_idx ON excerpts (expires_at) WHERE expires_at IS NOT NULL;

-- One excerpt, many briefs. ON DELETE RESTRICT is the backstop; the trigger below is what
-- refuses first, with a message that says why.
CREATE TABLE IF NOT EXISTS excerpt_citations (
    excerpt_id  uuid         NOT NULL REFERENCES excerpts (id) ON DELETE RESTRICT,
    brief_id    text         NOT NULL,
    cited_at    timestamptz  NOT NULL DEFAULT now(),
    PRIMARY KEY (excerpt_id, brief_id)
);

-- WRITE ONCE. The only permitted change to a stored excerpt is pinning it: expires_at
-- going from a date to NULL. Every other column is fixed at insert, and a pinned excerpt
-- cannot be unpinned.
CREATE OR REPLACE FUNCTION excerpts_write_once() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF (NEW.id, NEW.canonical_url, NEW.domain, NEW.fetched_at, NEW.content_sha256,
        NEW.text_z, NEW.text_bytes, NEW.stored_bytes)
       IS DISTINCT FROM
       (OLD.id, OLD.canonical_url, OLD.domain, OLD.fetched_at, OLD.content_sha256,
        OLD.text_z, OLD.text_bytes, OLD.stored_bytes) THEN
        RAISE EXCEPTION 'excerpt % is write-once: only expires_at may change, and only to NULL', OLD.id
            USING ERRCODE = 'restrict_violation';
    END IF;
    IF NEW.expires_at IS DISTINCT FROM OLD.expires_at
       AND NOT (OLD.expires_at IS NOT NULL AND NEW.expires_at IS NULL) THEN
        RAISE EXCEPTION 'excerpt %: expires_at may only go from a date to NULL (pinning)', OLD.id
            USING ERRCODE = 'restrict_violation';
    END IF;
    RETURN NEW;
END $$;

CREATE OR REPLACE TRIGGER excerpts_write_once
    BEFORE UPDATE ON excerpts
    FOR EACH ROW EXECUTE FUNCTION excerpts_write_once();

-- A cited excerpt is evidence: it is never deleted, whoever asks.
CREATE OR REPLACE FUNCTION excerpts_keep_cited() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF EXISTS (SELECT 1 FROM excerpt_citations WHERE excerpt_id = OLD.id) THEN
        RAISE EXCEPTION 'excerpt % is cited by a brief and cannot be deleted', OLD.id
            USING ERRCODE = 'restrict_violation';
    END IF;
    RETURN OLD;
END $$;

CREATE OR REPLACE TRIGGER excerpts_keep_cited
    BEFORE DELETE ON excerpts
    FOR EACH ROW EXECUTE FUNCTION excerpts_keep_cited();

-- Citations are append-only, so the protection above cannot be removed by deleting the
-- citation first.
CREATE OR REPLACE FUNCTION excerpt_citations_append_only() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'excerpt citations are append-only'
        USING ERRCODE = 'restrict_violation';
END $$;

CREATE OR REPLACE TRIGGER excerpt_citations_append_only
    BEFORE UPDATE OR DELETE ON excerpt_citations
    FOR EACH ROW EXECUTE FUNCTION excerpt_citations_append_only();


-- ======================================================================================
-- Runs: a weekly run is reader, then framer, then gates, then publish. Each attempt at a
-- stage is a row; a 'done' attempt is the checkpoint a failed run resumes from, so the
-- reader is not re-fetched and no paid stage that already finished is paid for again.
-- ======================================================================================

CREATE TABLE IF NOT EXISTS runs (
    id           text         PRIMARY KEY CHECK (id ~ '^[0-9]{4}-W[0-9]{2}(-r([2-9]|[1-9][0-9]+))?$'),  -- the week key; later attempts add -r2, -r3
    started_at   timestamptz  NOT NULL,
    finished_at  timestamptz  NULL,
    status       text         NOT NULL CHECK (status IN ('running', 'done')),
    CHECK ((status = 'done') = (finished_at IS NOT NULL))
);

-- A week can have numbered attempts: 2026-W43 is the first, 2026-W43-r2 the second. The CHECK was first
-- written for the bare week key; widen it once. Existing rows all satisfy the wider pattern.
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'runs_id_check'
                   AND conrelid = 'runs'::regclass AND pg_get_constraintdef(oid) LIKE '%[2-9]|[1-9]%') THEN
        ALTER TABLE runs DROP CONSTRAINT IF EXISTS runs_id_check;
        ALTER TABLE runs ADD CONSTRAINT runs_id_check CHECK (id ~ '^[0-9]{4}-W[0-9]{2}(-r([2-9]|[1-9][0-9]+))?$');
    END IF;
END $$;

CREATE TABLE IF NOT EXISTS run_stages (
    run_id       text         NOT NULL REFERENCES runs (id) ON DELETE RESTRICT,
    stage        text         NOT NULL CHECK (stage IN ('reader', 'framer', 'gates', 'publish')),
    attempt      integer      NOT NULL CHECK (attempt >= 1),
    status       text         NOT NULL CHECK (status IN ('running', 'done', 'failed')),
    started_at   timestamptz  NOT NULL,
    finished_at  timestamptz  NULL,
    -- Pointers only (ids of items, excerpts, the brief), never article text: that lives
    -- in excerpts. The cap is on the JSON as text, so compression cannot hide a page.
    output       jsonb        NULL CHECK (output IS NULL OR octet_length(output::text) <= 65536),
    error        text         NULL CHECK (error IS NULL OR length(error) <= 1000),
    PRIMARY KEY (run_id, stage, attempt),
    CHECK ((status = 'running') = (finished_at IS NULL))
);

-- One owner: at most one worker is running a given stage of a given run.
CREATE UNIQUE INDEX IF NOT EXISTS run_stages_one_running
    ON run_stages (run_id, stage) WHERE status = 'running';
-- One checkpoint: a stage is done at most once, so a resume never has to choose.
CREATE UNIQUE INDEX IF NOT EXISTS run_stages_one_done
    ON run_stages (run_id, stage) WHERE status = 'done';

-- A DONE STAGE IS THE CHECKPOINT. A resume trusts its output without re-running it, so
-- once written it is never changed or removed.
CREATE OR REPLACE FUNCTION run_stages_done_write_once() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF OLD.status = 'done' THEN
        RAISE EXCEPTION 'run % stage % attempt % is done: a checkpoint is never changed or deleted',
            OLD.run_id, OLD.stage, OLD.attempt
            USING ERRCODE = 'restrict_violation';
    END IF;
    RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
END $$;

CREATE OR REPLACE TRIGGER run_stages_done_write_once
    BEFORE UPDATE OR DELETE ON run_stages
    FOR EACH ROW EXECUTE FUNCTION run_stages_done_write_once();

-- An attempt is born running and ends once: running -> done or running -> failed. Its
-- identity (run, stage, attempt, start) never changes.
CREATE OR REPLACE FUNCTION run_stages_transitions() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'INSERT' THEN
        IF NEW.status <> 'running' THEN
            RAISE EXCEPTION 'run % stage %: an attempt starts as running, not %', NEW.run_id, NEW.stage, NEW.status
                USING ERRCODE = 'restrict_violation';
        END IF;
        RETURN NEW;
    END IF;
    IF (NEW.run_id, NEW.stage, NEW.attempt, NEW.started_at)
       IS DISTINCT FROM (OLD.run_id, OLD.stage, OLD.attempt, OLD.started_at) THEN
        RAISE EXCEPTION 'run % stage % attempt %: identity columns never change', OLD.run_id, OLD.stage, OLD.attempt
            USING ERRCODE = 'restrict_violation';
    END IF;
    IF NOT (OLD.status = 'running' AND NEW.status IN ('done', 'failed')) THEN
        RAISE EXCEPTION 'run % stage % attempt %: % -> % is not allowed (only running -> done or failed)',
            OLD.run_id, OLD.stage, OLD.attempt, OLD.status, NEW.status
            USING ERRCODE = 'restrict_violation';
    END IF;
    RETURN NEW;
END $$;

CREATE OR REPLACE TRIGGER run_stages_transitions
    BEFORE INSERT OR UPDATE ON run_stages
    FOR EACH ROW EXECUTE FUNCTION run_stages_transitions();

-- Order: a stage starts only after the stage before it is done for this run.
CREATE OR REPLACE FUNCTION run_stages_order() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    stages text[] := ARRAY['reader', 'framer', 'gates', 'publish'];
    pos integer := array_position(stages, NEW.stage);
BEGIN
    IF pos > 1 AND NOT EXISTS (
        SELECT 1 FROM run_stages
        WHERE run_id = NEW.run_id AND stage = stages[pos - 1] AND status = 'done'
    ) THEN
        RAISE EXCEPTION 'run %: % cannot start before % is done', NEW.run_id, NEW.stage, stages[pos - 1]
            USING ERRCODE = 'restrict_violation';
    END IF;
    RETURN NEW;
END $$;

CREATE OR REPLACE TRIGGER run_stages_order
    BEFORE INSERT ON run_stages
    FOR EACH ROW EXECUTE FUNCTION run_stages_order();

-- A FINISHED RUN IS WRITE-ONCE. The only change a run ever takes is running -> done,
-- made when publish finishes; after that it is never changed or deleted. A real finished
-- run also has done stages, so ON DELETE RESTRICT refuses its deletion too; this trigger
-- refuses first, and also covers the update.
CREATE OR REPLACE FUNCTION runs_finished_write_once() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF OLD.status = 'done' THEN
        RAISE EXCEPTION 'run % is done: a finished run is never changed or deleted', OLD.id
            USING ERRCODE = 'restrict_violation';
    END IF;
    IF TG_OP = 'DELETE' THEN
        RETURN OLD;
    END IF;
    IF NOT (OLD.status = 'running' AND NEW.status = 'done')
       OR (NEW.id, NEW.started_at) IS DISTINCT FROM (OLD.id, OLD.started_at) THEN
        RAISE EXCEPTION 'run %: the only change allowed is running -> done', OLD.id
            USING ERRCODE = 'restrict_violation';
    END IF;
    RETURN NEW;
END $$;

CREATE OR REPLACE TRIGGER runs_finished_write_once
    BEFORE UPDATE OR DELETE ON runs
    FOR EACH ROW EXECUTE FUNCTION runs_finished_write_once();


-- ======================================================================================
-- Sources, items, the interest graph, briefs and feedback: the rest of the storage layer.
--
-- Shaped by what Reader writes and what the Weekly Digest surface reads. An item is one
-- canonical URL; it carries the provenance of every place it was seen (item_sources) and
-- the excerpt that holds its text. Rules a later caller could forget are enforced here.
-- ======================================================================================

-- A source is a publisher Reader knows the reputation of. tier and reputation_prior come
-- from config/scout-sources.md; a domain Reader meets that the list does not name is
-- recorded as 'unrated' rather than guessed at. domain is NULL where the list names a
-- source without giving its address.
CREATE TABLE IF NOT EXISTS sources (
    id                text         PRIMARY KEY CHECK (id ~ '^[a-z0-9][a-z0-9._-]*$'),
    name              text         NOT NULL,
    domain            text         NULL UNIQUE,
    tier              text         NOT NULL CHECK (tier IN ('A', 'B', 'C', 'D', 'unrated')),
    reputation_prior  text         NOT NULL CHECK (reputation_prior IN ('high', 'medium', 'low', 'unrated')),
    feed_url          text         NULL,
    notes             text         NULL,
    CHECK ((tier IN ('A', 'B')) = (reputation_prior = 'high')),
    CHECK ((tier = 'C') = (reputation_prior = 'medium')),
    CHECK ((tier = 'D') = (reputation_prior = 'low'))
);

-- One row per canonical URL (agents/excerpts.canonicalise). Its text lives in excerpts.
CREATE TABLE IF NOT EXISTS items (
    id               uuid         PRIMARY KEY,
    canonical_url    text         NOT NULL UNIQUE,
    domain           text         NOT NULL,
    title            text         NOT NULL,
    source_id        text         NOT NULL REFERENCES sources (id) ON DELETE RESTRICT,  -- who surfaced it first
    discovered_at    timestamptz  NOT NULL,
    status           text         NOT NULL CHECK (status IN ('discovered', 'skipped', 'fetched', 'failed', 'expired')),
    relevance_score  integer      NOT NULL DEFAULT 0 CHECK (relevance_score >= 0),
    relevance_terms  text[]       NOT NULL DEFAULT '{}',
    category         text         NULL,
    excerpt_id       uuid         NULL REFERENCES excerpts (id) ON DELETE RESTRICT,
    fetch_error      text         NULL CHECK (fetch_error IS NULL OR length(fetch_error) <= 500),
    CHECK ((status = 'fetched') = (excerpt_id IS NOT NULL))
);
CREATE INDEX IF NOT EXISTS items_status_idx ON items (status, discovered_at);

-- An item whose text has aged out of the excerpt store is 'expired': it keeps its identity and
-- provenance, and its excerpt pointer is cleared. The CHECK above was first written without
-- 'expired', and CREATE TABLE IF NOT EXISTS will not change a table that exists, so widen it once.
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'items_status_check'
                   AND conrelid = 'items'::regclass AND pg_get_constraintdef(oid) LIKE '%expired%') THEN
        ALTER TABLE items DROP CONSTRAINT IF EXISTS items_status_check;
        ALTER TABLE items ADD CONSTRAINT items_status_check
            CHECK (status IN ('discovered', 'skipped', 'fetched', 'failed', 'expired'));
    END IF;
END $$;

-- Provenance and corroboration: every place an item was seen, appended, never edited.
-- 'primary' is the publisher's own page; 'corroboration' is another outlet reporting it.
CREATE TABLE IF NOT EXISTS item_sources (
    item_id    uuid         NOT NULL REFERENCES items (id) ON DELETE RESTRICT,
    url        text         NOT NULL,
    source_id  text         NOT NULL REFERENCES sources (id) ON DELETE RESTRICT,
    role       text         NOT NULL CHECK (role IN ('primary', 'corroboration')),
    seen_at    timestamptz  NOT NULL,
    PRIMARY KEY (item_id, url)
);

-- The interest graph, from config/profile.json: topics in rings, and the terms that
-- recognise each one. Reloaded from the profile, so it carries no history of its own.
CREATE TABLE IF NOT EXISTS interest_nodes (
    user_id  text  NOT NULL,
    topic    text  NOT NULL,
    ring     text  NOT NULL CHECK (ring IN ('core', 'adjacent', 'explore')),
    PRIMARY KEY (user_id, topic)
);
CREATE TABLE IF NOT EXISTS interest_terms (
    user_id  text  NOT NULL,
    topic    text  NOT NULL,
    term     text  NOT NULL CHECK (term = lower(term) AND length(term) >= 2),
    PRIMARY KEY (user_id, topic, term),
    FOREIGN KEY (user_id, topic) REFERENCES interest_nodes (user_id, topic) ON DELETE CASCADE
);

-- A published weekly brief: the Framer's output, as written. One per WEEK (the id is the bare week
-- key, so a second attempt at a published week cannot publish), made by one of that week's attempts
-- (2026-W43 or 2026-W43-r2), never edited.
CREATE TABLE IF NOT EXISTS briefs (
    id            text         PRIMARY KEY CHECK (id ~ '^[0-9]{4}-W[0-9]{2}$'),
    run_id        text         NOT NULL UNIQUE REFERENCES runs (id) ON DELETE RESTRICT,
    body          jsonb        NOT NULL CHECK (octet_length(body::text) <= 1048576),
    published_at  timestamptz  NOT NULL
);
-- The run must be an attempt at this brief's own week. (Added separately: the table may predate it.)
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'briefs_run_is_this_week'
                   AND conrelid = 'briefs'::regclass) THEN
        ALTER TABLE briefs ADD CONSTRAINT briefs_run_is_this_week
            CHECK (run_id = id OR left(run_id, length(id) + 2) = id || '-r');
    END IF;
END $$;

-- Thumbs and notes on a brief's items, from anyone (no login). client_hash is a salted
-- hash of the voter's address, never the address. A changed mind is a new row; the
-- latest per (brief, item, client) counts.
CREATE TABLE IF NOT EXISTS feedback (
    id           uuid         PRIMARY KEY,
    brief_id     text         NOT NULL REFERENCES briefs (id) ON DELETE RESTRICT,
    item_ref     text         NOT NULL CHECK (length(item_ref) BETWEEN 1 AND 200),
    vote         text         NOT NULL CHECK (vote IN ('up', 'down')),
    comment      text         NULL CHECK (comment IS NULL OR length(comment) BETWEEN 1 AND 2000),
    client_hash  text         NOT NULL CHECK (client_hash ~ '^[0-9a-f]{64}$'),
    created_at   timestamptz  NOT NULL,
    CHECK (comment IS NULL OR vote = 'down')
);
CREATE INDEX IF NOT EXISTS feedback_brief_idx ON feedback (brief_id, item_ref, created_at);
CREATE INDEX IF NOT EXISTS feedback_client_idx ON feedback (client_hash, created_at);
CREATE INDEX IF NOT EXISTS feedback_time_idx ON feedback (created_at);

-- ITEMS. Identity is fixed; the text is attached once; status only moves forward;
-- an item is never deleted, because a brief may have been written from it.
CREATE OR REPLACE FUNCTION items_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'item % is never deleted', OLD.id USING ERRCODE = 'restrict_violation';
    END IF;
    IF (NEW.id, NEW.canonical_url, NEW.domain, NEW.source_id, NEW.discovered_at)
       IS DISTINCT FROM (OLD.id, OLD.canonical_url, OLD.domain, OLD.source_id, OLD.discovered_at) THEN
        RAISE EXCEPTION 'item %: identity columns never change', OLD.id USING ERRCODE = 'restrict_violation';
    END IF;
    IF OLD.excerpt_id IS NOT NULL AND NEW.excerpt_id IS DISTINCT FROM OLD.excerpt_id
       AND NOT (OLD.status = 'fetched' AND NEW.status = 'expired' AND NEW.excerpt_id IS NULL) THEN
        RAISE EXCEPTION 'item %: its excerpt is attached once and never swapped (only expiry clears it)', OLD.id
            USING ERRCODE = 'restrict_violation';
    END IF;
    IF NEW.status IS DISTINCT FROM OLD.status AND NOT (
           (OLD.status = 'discovered' AND NEW.status IN ('skipped', 'fetched', 'failed'))
        OR (OLD.status = 'skipped'    AND NEW.status IN ('discovered', 'fetched', 'failed'))
        OR (OLD.status = 'failed'     AND NEW.status IN ('discovered', 'fetched'))
        OR (OLD.status = 'fetched'    AND NEW.status = 'expired')) THEN
        RAISE EXCEPTION 'item %: % -> % is not allowed (fetched only ever expires)', OLD.id, OLD.status, NEW.status
            USING ERRCODE = 'restrict_violation';
    END IF;
    RETURN NEW;
END $$;

CREATE OR REPLACE TRIGGER items_guard
    BEFORE UPDATE OR DELETE ON items
    FOR EACH ROW EXECUTE FUNCTION items_guard();

-- Provenance, briefs and feedback are append-only: a record of what happened.
CREATE OR REPLACE FUNCTION append_only() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION '% is append-only: rows are never updated or deleted', TG_TABLE_NAME
        USING ERRCODE = 'restrict_violation';
END $$;

CREATE OR REPLACE TRIGGER item_sources_append_only
    BEFORE UPDATE OR DELETE ON item_sources
    FOR EACH ROW EXECUTE FUNCTION append_only();
CREATE OR REPLACE TRIGGER briefs_append_only
    BEFORE UPDATE OR DELETE ON briefs
    FOR EACH ROW EXECUTE FUNCTION append_only();
CREATE OR REPLACE TRIGGER feedback_append_only
    BEFORE UPDATE OR DELETE ON feedback
    FOR EACH ROW EXECUTE FUNCTION append_only();

-- Work products a stage hands to the next one. run_stages.output holds POINTERS only, so
-- the Framer's writing (text, not an id) lives here, and the stage's output names it.
-- Append-only: an attempt's product is a record of what that attempt made.
CREATE TABLE IF NOT EXISTS run_artifacts (
    run_id      text         NOT NULL REFERENCES runs (id) ON DELETE RESTRICT,
    stage       text         NOT NULL CHECK (stage IN ('reader', 'framer', 'gates', 'publish')),
    attempt     integer      NOT NULL CHECK (attempt >= 1),
    body        jsonb        NOT NULL CHECK (octet_length(body::text) <= 1048576),
    created_at  timestamptz  NOT NULL,
    PRIMARY KEY (run_id, stage, attempt),
    FOREIGN KEY (run_id, stage, attempt) REFERENCES run_stages (run_id, stage, attempt) ON DELETE RESTRICT
);

CREATE OR REPLACE TRIGGER run_artifacts_append_only
    BEFORE UPDATE OR DELETE ON run_artifacts
    FOR EACH ROW EXECUTE FUNCTION append_only();

-- DEDUPLICATION DECISIONS (agents/dedupe.py). One row per pair of fetched items the gate decided
-- on, by which layer and why. A 'same' row is a merge: the other item is folded into the kept one
-- (its link is also recorded as a sighting in item_sources) and can be undone, once, with a reason.
-- Nothing else about a decision ever changes and no row is deleted, so the record of why a story
-- was merged away outlives the merge. 'different' rows exist only for model judgements, so a pair
-- the model has seen is not paid for twice.
CREATE TABLE IF NOT EXISTS dedup_decisions (
    id             uuid         PRIMARY KEY,
    kept_id        uuid         NOT NULL REFERENCES items (id) ON DELETE RESTRICT,
    other_id       uuid         NOT NULL REFERENCES items (id) ON DELETE RESTRICT,
    verdict        text         NOT NULL CHECK (verdict IN ('same', 'different')),
    layer          text         NOT NULL CHECK (layer IN ('url', 'identity', 'fingerprint', 'model', 'default')),
    reason         text         NOT NULL CHECK (length(reason) BETWEEN 1 AND 1000),
    decided_at     timestamptz  NOT NULL,
    undone_at      timestamptz  NULL,
    undone_reason  text         NULL CHECK (undone_reason IS NULL OR length(undone_reason) BETWEEN 1 AND 1000),
    CHECK (kept_id <> other_id),
    CHECK ((undone_at IS NULL) = (undone_reason IS NULL)),
    CHECK (verdict = 'same' OR undone_at IS NULL)
);
CREATE UNIQUE INDEX IF NOT EXISTS dedup_decisions_pair_idx
    ON dedup_decisions (LEAST(kept_id::text, other_id::text), GREATEST(kept_id::text, other_id::text));
CREATE INDEX IF NOT EXISTS dedup_decisions_other_idx ON dedup_decisions (other_id) WHERE verdict = 'same' AND undone_at IS NULL;

CREATE OR REPLACE FUNCTION dedup_decisions_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'dedup decision % is never deleted', OLD.id USING ERRCODE = 'restrict_violation';
    END IF;
    IF (NEW.id, NEW.kept_id, NEW.other_id, NEW.verdict, NEW.layer, NEW.reason, NEW.decided_at)
       IS DISTINCT FROM (OLD.id, OLD.kept_id, OLD.other_id, OLD.verdict, OLD.layer, OLD.reason, OLD.decided_at) THEN
        RAISE EXCEPTION 'dedup decision %: only an undo may change it', OLD.id USING ERRCODE = 'restrict_violation';
    END IF;
    IF OLD.undone_at IS NOT NULL THEN
        RAISE EXCEPTION 'dedup decision %: an undo is final', OLD.id USING ERRCODE = 'restrict_violation';
    END IF;
    RETURN NEW;
END $$;

CREATE OR REPLACE TRIGGER dedup_decisions_guard
    BEFORE UPDATE OR DELETE ON dedup_decisions
    FOR EACH ROW EXECUTE FUNCTION dedup_decisions_guard();
