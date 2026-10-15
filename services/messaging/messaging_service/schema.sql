-- This schema belongs only to the isolated synthetic probe database.
CREATE TABLE IF NOT EXISTS probe_metadata (
    name text PRIMARY KEY, value text NOT NULL
);
INSERT INTO probe_metadata VALUES ('schema_version', '1') ON CONFLICT DO NOTHING;
CREATE TABLE IF NOT EXISTS principals (
    id text PRIMARY KEY,
    accounts text[] NOT NULL,
    scope_version integer NOT NULL DEFAULT 1 CHECK (scope_version > 0)
);
CREATE TABLE IF NOT EXISTS api_keys (
    digest text PRIMARY KEY, principal text NOT NULL REFERENCES principals(id),
    expires_at timestamptz NOT NULL, revoked boolean NOT NULL DEFAULT false
);
CREATE TABLE IF NOT EXISTS accounts (
    id text PRIMARY KEY, platform text NOT NULL, label text NOT NULL
);
CREATE TABLE IF NOT EXISTS chats (
    id text PRIMARY KEY, account text NOT NULL REFERENCES accounts(id), title text NOT NULL,
    UNIQUE (id, account)
);
CREATE TABLE IF NOT EXISTS messages (
    id text PRIMARY KEY, account text NOT NULL,
    chat text NOT NULL, provider_id text NOT NULL, sender text NOT NULL,
    body text NOT NULL, sent_at timestamptz NOT NULL, revision integer NOT NULL DEFAULT 1,
    FOREIGN KEY(chat, account) REFERENCES chats(id, account),
    UNIQUE(account, chat, provider_id),
    document tsvector GENERATED ALWAYS AS (to_tsvector('simple', body)) STORED
);
CREATE INDEX IF NOT EXISTS message_fts ON messages USING gin(document);
CREATE INDEX IF NOT EXISTS message_scope_date ON messages(account, sent_at, id);
CREATE TABLE IF NOT EXISTS attachments (
    id text PRIMARY KEY, message text NOT NULL REFERENCES messages(id),
    digest text NOT NULL CHECK(digest ~ '^[a-f0-9]{64}$'),
    filename text NOT NULL, mime text NOT NULL, bytes bigint NOT NULL CHECK(bytes >= 0)
);
CREATE TABLE IF NOT EXISTS feed_counter (
    id integer PRIMARY KEY CHECK(id=1), value bigint NOT NULL DEFAULT 0
);
INSERT INTO feed_counter(id) VALUES(1) ON CONFLICT DO NOTHING;
CREATE TABLE IF NOT EXISTS events (
    id bigint PRIMARY KEY, account text NOT NULL REFERENCES accounts(id),
    message text NOT NULL REFERENCES messages(id), kind text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(), UNIQUE(message, kind)
);
CREATE TABLE IF NOT EXISTS consumers (
    principal text NOT NULL REFERENCES principals(id), name text NOT NULL,
    scope_version integer NOT NULL, position bigint NOT NULL DEFAULT 0,
    version bigint NOT NULL DEFAULT 0, PRIMARY KEY(principal, name, scope_version)
);
CREATE TABLE IF NOT EXISTS deliveries (
    digest text PRIMARY KEY, principal text NOT NULL REFERENCES principals(id),
    consumer text NOT NULL, scope_version integer NOT NULL,
    start_position bigint NOT NULL, end_position bigint NOT NULL,
    expected_version bigint NOT NULL,
    expires_at timestamptz NOT NULL, acked boolean NOT NULL DEFAULT false
);
CREATE INDEX IF NOT EXISTS deliveries_expiry ON deliveries(expires_at);
CREATE TABLE IF NOT EXISTS download_grants (
    digest text PRIMARY KEY, key_digest text NOT NULL REFERENCES api_keys(digest),
    scope_version integer NOT NULL, attachment text NOT NULL REFERENCES attachments(id),
    expires_at timestamptz NOT NULL
);
CREATE INDEX IF NOT EXISTS grants_expiry ON download_grants(expires_at);
