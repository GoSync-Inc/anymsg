-- Provider acknowledgements are independent of message ingestion and revisions.
-- Account-only foreign keys retain receipts which arrive before chat discovery.
CREATE TABLE message_receipts (
    id text PRIMARY KEY CHECK(id ~ '^[a-f0-9]{64}$'),
    account text NOT NULL REFERENCES accounts(id),
    platform text NOT NULL CHECK(platform IN ('telegram','whatsapp')),
    provider_chat text NOT NULL,
    provider_id text,
    max_id bigint,
    state text NOT NULL CHECK(state IN ('pending','error','server','delivered','read','played')),
    basis text NOT NULL CHECK(basis IN ('telegram_outbox_boundary','whatsapp_message_ack')),
    audience text NOT NULL CHECK(audience IN ('private_peer','provider_aggregate')),
    observed_at timestamptz NOT NULL,
    provider_at timestamptz CHECK(provider_at IS NULL),
    recorded_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    CHECK((platform='telegram' AND provider_id IS NULL AND max_id IS NOT NULL AND max_id>0
        AND state='read' AND basis='telegram_outbox_boundary' AND audience='private_peer')
        OR (platform='whatsapp' AND provider_id IS NOT NULL AND max_id IS NULL
            AND basis='whatsapp_message_ack'))
);
CREATE INDEX receipt_boundary_lookup ON message_receipts(account,provider_chat,max_id)
    WHERE platform='telegram';
CREATE INDEX receipt_exact_lookup ON message_receipts(account,provider_chat,provider_id,state,observed_at)
    WHERE platform='whatsapp';
-- Cursor pages must not repeatedly sort or scan the complete outgoing chat.
CREATE INDEX message_receipt_range ON messages(account,chat,
    (CASE WHEN provider_id ~ '^[1-9][0-9]{0,18}$' THEN provider_id::numeric ELSE NULL END),id)
    WHERE direction='outgoing' AND NOT deleted;

CREATE TABLE receipt_event_jobs (
    receipt text PRIMARY KEY REFERENCES message_receipts(id),
    account text NOT NULL REFERENCES accounts(id),
    previous_max bigint NOT NULL DEFAULT 0 CHECK(previous_max>=0),
    cursor_id bigint NOT NULL DEFAULT 0 CHECK(cursor_id>=0),
    complete boolean NOT NULL DEFAULT false
);
CREATE INDEX receipt_jobs_pending ON receipt_event_jobs(account,receipt) WHERE NOT complete;

-- Exactly one durable publication frontier per existing message. Receipt events
-- never change the message revision or the consumers' independent ACK positions.
CREATE TABLE receipt_event_publications (
    message text PRIMARY KEY REFERENCES messages(id),
    receipt text NOT NULL REFERENCES message_receipts(id),
    state text NOT NULL CHECK(state IN ('pending','error','server','delivered','read','played')),
    published_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
-- events.kind is unconstrained in the existing schema; message.status is additive.
