-- Agent uploads are scoped staged originals, never fabricated archive messages.
ALTER TABLE principals DROP CONSTRAINT supported_permissions;
ALTER TABLE principals ADD CONSTRAINT supported_permissions CHECK(permissions <@ ARRAY[
 'cursor-write','download','read','review-write','send','read-live','contacts-read','profile-read',
 'groups-read','message-edit','message-delete','message-react','message-pin','read-state',
 'chat-manage','contacts-manage','groups-manage','profile-manage','subscription-write','upload']::text[]);
ALTER TABLE api_keys DROP CONSTRAINT key_supported_permissions;
ALTER TABLE api_keys ADD CONSTRAINT key_supported_permissions CHECK(permissions IS NULL OR permissions <@ ARRAY[
 'cursor-write','download','read','review-write','send','read-live','contacts-read','profile-read',
 'groups-read','message-edit','message-delete','message-react','message-pin','read-state',
 'chat-manage','contacts-manage','groups-manage','profile-manage','subscription-write','upload']::text[]);
CREATE TABLE file_uploads (
 id text PRIMARY KEY DEFAULT ('up_'||replace(gen_random_uuid()::text,'-','')) CHECK(id ~ '^up_[a-f0-9]{32}$'),
 principal text NOT NULL REFERENCES principals(id), key_id uuid NOT NULL REFERENCES api_keys(id),
 account text NOT NULL REFERENCES accounts(id), chat text NOT NULL,
 filename text NOT NULL, mime text NOT NULL,
 bytes bigint NOT NULL CHECK(bytes BETWEEN 1 AND 33554432),
 sha256 text NOT NULL CHECK(sha256 ~ '^[a-f0-9]{64}$'),
 received bigint NOT NULL DEFAULT 0 CHECK(received>=0 AND received<=bytes),
 state text NOT NULL DEFAULT 'uploading' CHECK(state IN ('uploading','ready','expired')),
 volume_key text NOT NULL,
 request_id text NOT NULL CHECK(request_id ~ '^[a-zA-Z0-9_.:-]{8,128}$'),
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 expires_at timestamptz NOT NULL DEFAULT clock_timestamp()+interval '1 hour',
 FOREIGN KEY(chat,account) REFERENCES chats(id,account),
 UNIQUE(key_id,request_id)
);
CREATE INDEX file_upload_expiry ON file_uploads(expires_at) WHERE state<>'expired';
-- Preserve the preexisting audit vocabulary without rewriting prior migrations.
ALTER TABLE audit_log DROP CONSTRAINT audit_log_operation_check;
ALTER TABLE audit_log ADD CONSTRAINT audit_log_operation_check CHECK(operation IN (
 'capabilities','list_chats','search','get_messages','get_context','get_updates',
 'get_attachment','download','ack_updates','issue_key','revoke_key','set_access','http_boundary',
 'get_reviews','record_review','search_people','search_files','get_file_text',
 'get_file_occurrences','get_file_segments','get_sync_status','resolve_reference',
 'send_message','get_send_status','send_dispatch','set_sending','preview_message',
 'set_key_permissions','set_operations','list_messenger_operations','preview_messenger_operation',
 'execute_messenger_operation','get_messenger_operation','messenger_dispatch',
 'create_subscription','list_subscriptions','get_subscription','update_subscription','get_subscription_events',
 'dismiss_reply','prepare_file_upload','upload_file_chunk','get_file_upload'));
