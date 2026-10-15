ALTER TABLE accounts ADD COLUMN synthetic boolean NOT NULL DEFAULT true;
ALTER TABLE messages ADD COLUMN deleted boolean NOT NULL DEFAULT false;
ALTER TABLE messages ADD COLUMN direction text NOT NULL DEFAULT 'incoming'
    CHECK(direction IN ('incoming','outgoing'));
ALTER TABLE messages ADD COLUMN provider_updated_at timestamptz;
ALTER TABLE messages ADD COLUMN content_hash text;
CREATE TABLE account_policies (
    account text PRIMARY KEY REFERENCES accounts(id), enabled boolean NOT NULL DEFAULT false,
    include_chats text[] NOT NULL DEFAULT '{}', exclude_chats text[] NOT NULL DEFAULT '{}',
    discover_chats boolean NOT NULL DEFAULT false
);
CREATE TABLE collector_leases (
    account text PRIMARY KEY REFERENCES accounts(id), owner text NOT NULL,
    epoch bigint NOT NULL, expires_at timestamptz NOT NULL
);
CREATE TABLE source_status (
    account text PRIMARY KEY REFERENCES accounts(id),
    status text NOT NULL DEFAULT 'not_started', last_ingest_at timestamptz,
    coverage text NOT NULL DEFAULT 'unknown', detail_code text NOT NULL DEFAULT 'not_checked'
);
CREATE TABLE ingest_receipts (
    digest text PRIMARY KEY, account text NOT NULL REFERENCES accounts(id),
    received_at timestamptz NOT NULL DEFAULT now(), envelope jsonb NOT NULL,
    outcome text NOT NULL
);
CREATE TABLE message_revisions (
    message text NOT NULL REFERENCES messages(id), revision integer NOT NULL,
    snapshot jsonb NOT NULL, observed_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY(message,revision)
);
ALTER TABLE events DROP CONSTRAINT events_message_kind_key;
ALTER TABLE events ADD COLUMN revision integer NOT NULL DEFAULT 1;
ALTER TABLE events ADD COLUMN origin text NOT NULL DEFAULT 'fixture';
ALTER TABLE events ADD COLUMN snapshot jsonb NOT NULL DEFAULT '{}';
ALTER TABLE events ADD COLUMN event_key text;
CREATE UNIQUE INDEX event_dedupe ON events(event_key);
UPDATE events e SET snapshot=jsonb_build_object('message',m.id,'account',m.account,
    'chat',m.chat,'body',m.body,'revision',m.revision,'sent_at',m.sent_at,
    'deleted',m.deleted,'direction',m.direction),revision=m.revision FROM messages m WHERE m.id=e.message;
INSERT INTO message_revisions(message,revision,snapshot)
    SELECT m.id,m.revision,jsonb_build_object('message',m.id,'account',m.account,
    'chat',m.chat,'body',m.body,'revision',m.revision,'sent_at',m.sent_at,
    'deleted',m.deleted,'direction',m.direction) FROM messages m;
CREATE TABLE media_jobs (
    id text PRIMARY KEY, account text NOT NULL REFERENCES accounts(id),
    message text NOT NULL REFERENCES messages(id), descriptor jsonb NOT NULL,
    state text NOT NULL DEFAULT 'queued' CHECK(state IN ('queued','leased','ready','failed','superseded')),
    attempts integer NOT NULL DEFAULT 0, next_due timestamptz NOT NULL DEFAULT now(),
    lease_owner text, lease_epoch bigint NOT NULL DEFAULT 0, lease_until timestamptz,
    error_code text, created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX media_due ON media_jobs(next_due) WHERE state IN ('queued','leased');
CREATE INDEX media_message ON media_jobs(message);
ALTER TABLE attachments ADD COLUMN active boolean NOT NULL DEFAULT true;
CREATE TABLE archive_migrations (
    version integer PRIMARY KEY, sha256 text NOT NULL, applied_at timestamptz NOT NULL DEFAULT now()
);
