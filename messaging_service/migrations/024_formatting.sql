-- Preserve existing plain rows without rewriting their source or request hashes.
ALTER TABLE outgoing_messages ADD COLUMN source_text text;
ALTER TABLE outgoing_messages ADD COLUMN source_format text NOT NULL DEFAULT 'plain'
    CHECK(source_format IN ('plain','markdown','html'));
ALTER TABLE outgoing_messages ADD COLUMN render_artifact jsonb
    CHECK(render_artifact IS NULL OR jsonb_typeof(render_artifact)='object');
ALTER TABLE outgoing_messages ADD COLUMN render_policy jsonb
    CHECK(render_policy IS NULL OR jsonb_typeof(render_policy)='object');
ALTER TABLE outgoing_messages ADD COLUMN preview_digest text
    CHECK(preview_digest IS NULL OR preview_digest ~ '^[a-f0-9]{64}$');
ALTER TABLE outgoing_messages ADD CONSTRAINT outgoing_render_columns CHECK (
    (source_text IS NULL AND source_format='plain' AND render_artifact IS NULL
     AND render_policy IS NULL AND preview_digest IS NULL)
    OR (source_text IS NOT NULL AND render_artifact IS NOT NULL AND render_policy IS NOT NULL
        AND (source_format='plain' OR preview_digest IS NOT NULL))
);
ALTER TABLE audit_log DROP CONSTRAINT audit_log_operation_check;
ALTER TABLE audit_log ADD CONSTRAINT audit_log_operation_check CHECK(operation IN (
 'capabilities','list_chats','search','get_messages','get_context','get_updates',
 'get_attachment','download','ack_updates','issue_key','revoke_key','set_access','http_boundary',
 'get_reviews','record_review','search_people','search_files','get_file_text',
 'get_file_occurrences','get_file_segments','get_sync_status','resolve_reference',
 'send_message','get_send_status','send_dispatch','set_sending','preview_message'));
