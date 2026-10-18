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
    id           text         PRIMARY KEY CHECK (id ~ '^[0-9]{4}-W[0-9]{2}$'),  -- the week key; a rerun reuses it
    started_at   timestamptz  NOT NULL,
    finished_at  timestamptz  NULL,
    status       text         NOT NULL CHECK (status IN ('running', 'done')),
    CHECK ((status = 'done') = (finished_at IS NOT NULL))
);

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
