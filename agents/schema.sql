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
