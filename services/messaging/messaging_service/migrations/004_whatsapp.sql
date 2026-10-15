-- WhatsApp IDs are opaque, not Telegram integer offsets. Progress is committed
-- only after the corresponding page has been archived. Never infer deletions.
CREATE TABLE whatsapp_checkpoints (
    account text NOT NULL,
    provider_chat text NOT NULL,
    progress jsonb NOT NULL DEFAULT '{}',
    last_scan_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY(account,provider_chat),
    FOREIGN KEY(account,provider_chat) REFERENCES collector_chats(account,provider_chat)
);
