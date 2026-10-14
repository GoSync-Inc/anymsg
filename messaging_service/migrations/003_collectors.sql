CREATE TABLE collector_chats (
    account text NOT NULL REFERENCES accounts(id), provider_chat text NOT NULL,
    peer_kind text NOT NULL CHECK(peer_kind IN ('user','chat','channel')),
    high_water bigint NOT NULL DEFAULT 0, history_before bigint,
    history_exhausted boolean NOT NULL DEFAULT false,
    initialized boolean NOT NULL DEFAULT false, last_scan_at timestamptz,
    PRIMARY KEY(account,provider_chat)
);
CREATE TABLE provider_tombstones (
    account text NOT NULL REFERENCES accounts(id), provider_chat text NOT NULL,
    provider_id text NOT NULL, observed_at timestamptz NOT NULL,
    PRIMARY KEY(account,provider_chat,provider_id)
);
CREATE INDEX message_provider_lookup ON messages(account,provider_id);
