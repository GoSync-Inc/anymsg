-- A bounded initial history window is not proof that provider history ended.
ALTER TABLE collector_chats ADD COLUMN history_since timestamptz;
ALTER TABLE collector_chats ADD COLUMN window_complete boolean NOT NULL DEFAULT false;
ALTER TABLE collector_chats ADD COLUMN last_attempt_at timestamptz;
CREATE INDEX collector_chat_attempt ON collector_chats(account,last_attempt_at,provider_chat);
