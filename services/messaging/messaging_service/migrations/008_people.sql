-- A provider identity is scoped to its account AND observed chat. Names from a
-- hidden chat must never decorate a visible message or leak via a directory.
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE TABLE sender_profiles (
    id text PRIMARY KEY,
    account text NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    chat text NOT NULL REFERENCES chats(id) ON DELETE CASCADE,
    sender text NOT NULL,
    display_name text NOT NULL DEFAULT '',
    username text NOT NULL DEFAULT '',
    source_message text NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
    source_sent_at timestamptz NOT NULL,
    observed_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    search_text text GENERATED ALWAYS AS (lower(display_name || ' ' || username || ' ' || sender)) STORED,
    UNIQUE(account,chat,sender)
);
CREATE INDEX sender_profile_scope ON sender_profiles(account,chat,id);
CREATE INDEX sender_profile_search ON sender_profiles USING gin(search_text gin_trgm_ops);
