-- Provider identifiers and query-scoped evidence, never agent administration.
ALTER TABLE chats ADD COLUMN provider_chat text;
CREATE UNIQUE INDEX chat_provider_lookup ON chats(account,provider_chat);
ALTER TABLE collector_chats ADD COLUMN last_head_scan_at timestamptz;
ALTER TABLE collector_chats ADD COLUMN last_error_code text;
ALTER TABLE collector_chats ADD COLUMN last_error_at timestamptz;
CREATE TABLE message_conflicts (
    receipt_digest text PRIMARY KEY REFERENCES ingest_receipts(digest),
    message text NOT NULL REFERENCES messages(id),
    observed_revision integer NOT NULL,
    candidate_updated_at timestamptz NOT NULL,
    observed_at timestamptz NOT NULL,
    resolved_at timestamptz,
    resolved_revision integer,
    CHECK((resolved_at IS NULL)=(resolved_revision IS NULL))
);
CREATE INDEX conflict_message ON message_conflicts(message,observed_at);
CREATE INDEX conflict_active ON message_conflicts(message) WHERE resolved_at IS NULL;
ALTER TABLE audit_log DROP CONSTRAINT audit_log_operation_check;
ALTER TABLE audit_log ADD CONSTRAINT audit_log_operation_check CHECK(operation IN (
    'capabilities','list_chats','search','get_messages','get_context','get_updates',
    'get_attachment','download','ack_updates','issue_key','revoke_key','set_access','http_boundary',
    'record_review','get_reviews','search_people','search_files','get_file_text','get_sync_status',
    'get_file_occurrences','get_file_segments','resolve_reference'));
