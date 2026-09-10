CREATE TABLE IF NOT EXISTS opportunities (
    id                 TEXT PRIMARY KEY,
    contact_key        TEXT CHECK (btrim(contact_key) <> ''),
    contact_name       TEXT,
    contact_phone      TEXT,
    contact_email      TEXT,
    owner_name         TEXT,
    value              NUMERIC(12, 2),
    status             TEXT NOT NULL DEFAULT 'open'
                           CHECK (status IN ('open', 'won', 'lost')),
    created_at         TIMESTAMPTZ NOT NULL,
    last_contact_at    TIMESTAMPTZ,
    preferred_channel  TEXT CHECK (preferred_channel IN ('email', 'sms')),
    CONSTRAINT opportunities_contact_identity CHECK (
        (contact_key IS NULL AND contact_phone IS NULL AND contact_email IS NULL
            AND preferred_channel IS NULL)
        OR (contact_key IS NOT NULL AND (
            NULLIF(btrim(contact_phone), '') IS NOT NULL
            OR NULLIF(btrim(contact_email), '') IS NOT NULL
        ))
    )
);

-- Additive, repeatable migration for databases created before contextual records.
-- The old unnamed route check prevents tracking records with no known recipient.
ALTER TABLE opportunities ALTER COLUMN contact_key DROP NOT NULL;
ALTER TABLE opportunities DROP CONSTRAINT IF EXISTS opportunities_check;
DO $$ BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid = 'opportunities'::regclass
            AND conname = 'opportunities_contact_identity'
    ) THEN
        ALTER TABLE opportunities ADD CONSTRAINT opportunities_contact_identity CHECK (
            (contact_key IS NULL AND contact_phone IS NULL AND contact_email IS NULL
                AND preferred_channel IS NULL)
            OR (contact_key IS NOT NULL AND (
                NULLIF(btrim(contact_phone), '') IS NOT NULL
                OR NULLIF(btrim(contact_email), '') IS NOT NULL
            ))
        );
    END IF;
END $$;
ALTER TABLE opportunities ADD COLUMN IF NOT EXISTS kind
    TEXT NOT NULL DEFAULT 'generic' CHECK (kind ~ '^[a-z][a-z0-9_]{0,63}$');
ALTER TABLE opportunities ADD COLUMN IF NOT EXISTS title
    TEXT CHECK (char_length(title) BETWEEN 1 AND 300);
ALTER TABLE opportunities ADD COLUMN IF NOT EXISTS context
    JSONB NOT NULL DEFAULT '{}'::JSONB
    CHECK (jsonb_typeof(context) = 'object' AND octet_length(context::TEXT) <= 16384);

-- Workspaces are saved filters within this engine, not tenant boundaries.
-- They have no foreign keys to records, so deleting one only removes the view.
CREATE TABLE IF NOT EXISTS workspaces (
    id           UUID PRIMARY KEY,
    name         TEXT NOT NULL CHECK (char_length(btrim(name)) BETWEEN 1 AND 120),
    description  TEXT NOT NULL DEFAULT '' CHECK (char_length(description) <= 2000),
    kinds        TEXT[] NOT NULL DEFAULT ARRAY[]::TEXT[]
                     CHECK (cardinality(kinds) <= 100 AND array_position(kinds, NULL) IS NULL),
    created_at   TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS application_settings (
    key         TEXT PRIMARY KEY,
    value       JSONB NOT NULL,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS activities (
    id              TEXT PRIMARY KEY,
    type            TEXT NOT NULL,
    opportunity_id  TEXT NOT NULL REFERENCES opportunities(id),
    occurred_at     TIMESTAMPTZ NOT NULL,
    channel         TEXT CHECK (channel IN ('email', 'sms')),
    direction       TEXT CHECK (direction IN ('inbound', 'outbound'))
);

ALTER TABLE activities ADD COLUMN IF NOT EXISTS summary
    TEXT CHECK (char_length(summary) BETWEEN 1 AND 2000);
ALTER TABLE activities ADD COLUMN IF NOT EXISTS source_url
    TEXT CHECK (char_length(source_url) <= 2048 AND source_url ~ '^https?://');
ALTER TABLE activities ADD COLUMN IF NOT EXISTS classification
    TEXT NOT NULL DEFAULT 'unknown' CHECK (classification IN ('human', 'automated', 'unknown'));

CREATE TABLE IF NOT EXISTS sync_runs (
    id               UUID PRIMARY KEY,
    run_at           TIMESTAMPTZ NOT NULL,
    candidate_count  INTEGER NOT NULL CHECK (candidate_count >= 0),
    created_at       TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- Selected-record drafting materializes a candidate without replacing the
-- published CLI queue. Existing runs retain their original queue semantics.
ALTER TABLE sync_runs ADD COLUMN IF NOT EXISTS scope
    TEXT NOT NULL DEFAULT 'queue' CHECK (scope IN ('queue', 'selection'));

CREATE TABLE IF NOT EXISTS candidates (
    id                      UUID PRIMARY KEY,
    sync_run_id             UUID NOT NULL REFERENCES sync_runs(id),
    run_at                  TIMESTAMPTZ NOT NULL,
    primary_opportunity_id  TEXT NOT NULL REFERENCES opportunities(id),
    contact_key             TEXT NOT NULL CHECK (btrim(contact_key) <> ''),
    contact_address         TEXT NOT NULL CHECK (btrim(contact_address) <> ''),
    contact_name            TEXT,
    channel                 TEXT NOT NULL CHECK (channel IN ('email', 'sms')),
    reason                  TEXT NOT NULL,
    score                   NUMERIC NOT NULL,
    other_opportunity_ids   TEXT[] NOT NULL DEFAULT ARRAY[]::TEXT[]
);

CREATE TABLE IF NOT EXISTS drafts (
    id                      UUID PRIMARY KEY,
    candidate_id            UUID NOT NULL UNIQUE REFERENCES candidates(id),
    contact_key             TEXT NOT NULL CHECK (btrim(contact_key) <> ''),
    contact_address         TEXT NOT NULL CHECK (btrim(contact_address) <> ''),
    contact_name            TEXT,
    channel                 TEXT NOT NULL CHECK (channel IN ('email', 'sms')),
    primary_opportunity_id  TEXT NOT NULL REFERENCES opportunities(id),
    opportunity_ids         TEXT[] NOT NULL,
    body                    TEXT NOT NULL CHECK (btrim(body) <> ''),
    status                  TEXT NOT NULL DEFAULT 'pending'
                                CHECK (status IN ('pending', 'approved', 'rejected')),
    created_at              TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    reviewed_at             TIMESTAMPTZ,
    CHECK (cardinality(opportunity_ids) > 0),
    CHECK (primary_opportunity_id = ANY(opportunity_ids))
);

CREATE TABLE IF NOT EXISTS outbox (
    id                BIGINT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
    draft_id          UUID NOT NULL UNIQUE REFERENCES drafts(id),
    contact_key       TEXT NOT NULL CHECK (btrim(contact_key) <> ''),
    contact_address   TEXT NOT NULL CHECK (btrim(contact_address) <> ''),
    contact_name      TEXT,
    channel           TEXT NOT NULL CHECK (channel IN ('email', 'sms')),
    opportunity_ids   TEXT[] NOT NULL,
    body              TEXT NOT NULL CHECK (btrim(body) <> ''),
    status            TEXT NOT NULL DEFAULT 'pending'
                          CHECK (status IN ('pending', 'sent', 'failed')),
    created_at        TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    sent_at           TIMESTAMPTZ,
    CHECK (cardinality(opportunity_ids) > 0)
);

-- Existing reservations predate explicit authorization provenance. Do not
-- retroactively claim that a human or an automatic policy approved them.
ALTER TABLE outbox ADD COLUMN IF NOT EXISTS authorization_mode
    TEXT NOT NULL DEFAULT 'legacy_unknown'
    CHECK (authorization_mode IN ('human', 'automatic', 'legacy_unknown'));

-- A receipt records an external sender's result; it never grants approval.
CREATE TABLE IF NOT EXISTS outbox_receipts (
    outbox_id           BIGINT PRIMARY KEY REFERENCES outbox(id),
    sender              TEXT NOT NULL CHECK (sender ~ '^[A-Za-z0-9][A-Za-z0-9_.:@/-]{0,199}$'),
    provider_message_id TEXT NOT NULL CHECK (char_length(provider_message_id) BETWEEN 1 AND 300),
    sent_at             TIMESTAMPTZ NOT NULL,
    recorded_at         TIMESTAMPTZ NOT NULL,
    UNIQUE (sender, provider_message_id)
);

CREATE INDEX IF NOT EXISTS idx_outbox_pending_connector
    ON outbox(id) WHERE status = 'pending' AND authorization_mode IN ('human', 'automatic');

DROP INDEX IF EXISTS idx_activities_opportunity_id;
DROP INDEX IF EXISTS idx_activities_occurred_at;
DROP INDEX IF EXISTS idx_opportunities_status;
DROP INDEX IF EXISTS idx_candidates_run_at;
DROP INDEX IF EXISTS idx_candidates_contact_key;
DROP INDEX IF EXISTS idx_drafts_status_created_at;

CREATE INDEX IF NOT EXISTS idx_opportunities_contact_key
    ON opportunities(contact_key);
CREATE INDEX IF NOT EXISTS idx_activities_reduce
    ON activities(opportunity_id, occurred_at, id);
CREATE INDEX IF NOT EXISTS idx_sync_runs_latest
    ON sync_runs(run_at DESC, created_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS idx_candidates_latest_run
    ON candidates(
        sync_run_id,
        score DESC,
        primary_opportunity_id,
        contact_key
    );
CREATE INDEX IF NOT EXISTS idx_outbox_contact_cooldown
    ON outbox(contact_key, created_at DESC) INCLUDE (draft_id);

CREATE OR REPLACE VIEW opportunity_states AS
WITH cutoff AS (
    SELECT COALESCE(
        NULLIF(current_setting('respawned.as_of', true), '')::TIMESTAMPTZ,
        'infinity'::TIMESTAMPTZ
    ) AS as_of
),
deduplicated AS (
    -- The primary key normally prevents duplicates. Keeping deterministic
    -- collapse here also protects projections fed by imported/staged data.
    SELECT DISTINCT ON (id)
        id,
        type,
        opportunity_id,
        occurred_at,
        channel,
        direction,
        summary,
        source_url,
        classification
    FROM activities
    ORDER BY
        id,
        occurred_at ASC,
        opportunity_id ASC,
        type ASC,
        channel ASC NULLS FIRST,
        direction ASC NULLS FIRST,
        classification ASC,
        summary ASC NULLS FIRST,
        source_url ASC NULLS FIRST
),
projected AS (
    SELECT *
    FROM deduplicated
    WHERE occurred_at <= (SELECT as_of FROM cutoff)
),
aggregated AS (
    SELECT
        opportunity_id,
        (
            ARRAY_AGG(
                type ORDER BY occurred_at DESC, id DESC
            ) FILTER (WHERE type IN ('opportunity_won', 'opportunity_lost'))
        )[1] AS latest_terminal_type,
        MIN(occurred_at)
            FILTER (WHERE type = 'opportunity_created') AS activity_created_at,
        MAX(occurred_at)
            FILTER (WHERE type = 'content_viewed') AS last_viewed_at,
        COALESCE(
            ARRAY_AGG(occurred_at ORDER BY occurred_at, id)
                FILTER (WHERE type = 'content_viewed'),
            ARRAY[]::TIMESTAMPTZ[]
        ) AS view_timestamps,
        MAX(occurred_at)
            FILTER (
                WHERE classification <> 'automated'
                    AND (type = 'contact_replied'
                        OR (classification = 'human' AND direction = 'inbound'))
            ) AS last_replied_at,
        MAX(occurred_at) FILTER (
            WHERE type = 'message_sent' AND direction = 'outbound'
        ) AS last_message_sent_at,
        (
            ARRAY_AGG(
                channel ORDER BY occurred_at DESC, id DESC
            ) FILTER (WHERE channel IS NOT NULL)
        )[1] AS last_activity_channel,
        COALESCE(
            JSONB_AGG(
                JSONB_BUILD_OBJECT(
                    'activity_id', id,
                    'activity_type', type,
                    'occurred_at', occurred_at,
                    'channel', channel,
                    'direction', direction,
                    'summary', summary,
                    'source_url', source_url,
                    'classification', classification
                ) ORDER BY occurred_at, id
            ),
            '[]'::JSONB
        ) AS activities
    FROM projected
    GROUP BY opportunity_id
)
SELECT
    opportunities.id AS opportunity_id,
    CASE
        WHEN opportunities.status <> 'open' THEN opportunities.status
        WHEN aggregated.latest_terminal_type = 'opportunity_won' THEN 'won'
        WHEN aggregated.latest_terminal_type = 'opportunity_lost' THEN 'lost'
        ELSE opportunities.status
    END AS status,
    opportunities.value,
    opportunities.contact_key,
    opportunities.contact_name,
    opportunities.contact_phone,
    opportunities.contact_email,
    opportunities.owner_name,
    COALESCE(aggregated.activity_created_at, opportunities.created_at)
        AS created_at,
    aggregated.last_viewed_at,
    aggregated.last_replied_at,
    GREATEST(
        CASE WHEN opportunities.last_contact_at <= (SELECT as_of FROM cutoff)
            THEN opportunities.last_contact_at END,
        aggregated.last_message_sent_at
    ) AS last_outbound_at,
    COALESCE(aggregated.view_timestamps, ARRAY[]::TIMESTAMPTZ[])
        AS view_timestamps,
    COALESCE(
        opportunities.preferred_channel,
        aggregated.last_activity_channel
    ) AS preferred_channel,
    COALESCE(aggregated.activities, '[]'::JSONB) AS activities,
    aggregated.last_activity_channel,
    opportunities.kind,
    opportunities.title,
    opportunities.context
FROM opportunities
LEFT JOIN aggregated ON aggregated.opportunity_id = opportunities.id;
