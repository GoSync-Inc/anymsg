ALTER TABLE audit_log DROP CONSTRAINT audit_log_operation_check;
ALTER TABLE audit_log ADD CONSTRAINT audit_log_operation_check CHECK(operation IN (
    'capabilities','list_chats','search','get_messages','get_context','get_updates',
    'get_attachment','download','ack_updates','issue_key','revoke_key','set_access','http_boundary',
    'record_review','get_reviews','search_people','search_files','get_file_text'));
