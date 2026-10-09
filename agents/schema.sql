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
