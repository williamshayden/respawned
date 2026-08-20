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

CREATE OR REPLACE VIEW quote_states AS
WITH deduplicated_events AS (
    SELECT DISTINCT ON (event_id)
        event_id,
        type,
        quote_id,
        "timestamp",
        channel,
        direction
    FROM events
    ORDER BY
        event_id,
        "timestamp" ASC,
        quote_id ASC,
        type ASC,
        channel ASC NULLS FIRST,
        direction ASC NULLS FIRST
)
SELECT
    quotes.id AS quote_id,
    CASE
        WHEN quotes.status = 'open'
         AND COUNT(*) FILTER (
             WHERE deduplicated_events.type = 'quote_accepted'
         ) > 0
        THEN 'accepted'
        ELSE quotes.status
    END AS status,
    quotes.amount,
    quotes.customer_name,
    quotes.customer_phone,
    quotes.tech_name,
    quotes.created_at,
    MIN(deduplicated_events."timestamp")
        FILTER (WHERE deduplicated_events.type = 'quote_sent') AS quote_sent_at,
    MAX(deduplicated_events."timestamp")
        FILTER (WHERE deduplicated_events.type = 'quote_viewed') AS last_viewed_at,
    COUNT(DISTINCT (
        deduplicated_events."timestamp" AT TIME ZONE COALESCE(
            NULLIF(
                current_setting('follow_up_engine.business_timezone', true),
                ''
            ),
            'UTC'
        )
    )::date)
        FILTER (WHERE deduplicated_events.type = 'quote_viewed') AS view_days,
    MAX(deduplicated_events."timestamp")
        FILTER (WHERE deduplicated_events.type = 'customer_replied') AS last_replied_at,
    GREATEST(
        quotes.last_contact_at,
        MAX(deduplicated_events."timestamp") FILTER (
            WHERE deduplicated_events.type = 'message_sent'
              AND deduplicated_events.direction = 'outbound'
        )
    ) AS last_outbound_at
FROM quotes
LEFT JOIN deduplicated_events ON deduplicated_events.quote_id = quotes.id
GROUP BY
    quotes.id,
    quotes.status,
    quotes.amount,
    quotes.customer_name,
    quotes.customer_phone,
    quotes.tech_name,
    quotes.created_at,
    quotes.last_contact_at;
