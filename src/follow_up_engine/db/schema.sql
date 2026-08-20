CREATE TABLE IF NOT EXISTS quotes (
    id               TEXT PRIMARY KEY,
    customer_name    TEXT NOT NULL,
    customer_phone   TEXT,
    tech_name        TEXT,
    amount           NUMERIC(12, 2),
    status           TEXT,
    created_at       TIMESTAMPTZ,
    last_contact_at  TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS events (
    event_id    UUID PRIMARY KEY,
    type        TEXT NOT NULL,
    quote_id    TEXT NOT NULL REFERENCES quotes(id),
    "timestamp" TIMESTAMPTZ NOT NULL,
    channel     TEXT CHECK (channel IN ('email', 'sms')),
    direction   TEXT CHECK (direction IN ('inbound', 'outbound'))
);

CREATE INDEX IF NOT EXISTS idx_events_quote_id ON events(quote_id);
CREATE INDEX IF NOT EXISTS idx_events_timestamp ON events("timestamp");
CREATE INDEX IF NOT EXISTS idx_quotes_status ON quotes(status);