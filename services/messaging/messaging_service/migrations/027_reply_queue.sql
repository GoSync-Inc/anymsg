-- Archive-local agent queue state; never marks source messages read.
CREATE TABLE chat_read_observations (
 chat text PRIMARY KEY,
 account text NOT NULL,
 observed_at timestamptz NOT NULL,
 unread_count integer CHECK(unread_count>=0),
 read_inbox_max_id bigint CHECK(read_inbox_max_id>=0),
 basis text NOT NULL CHECK(basis IN ('telegram_dialog','whatsapp_cached_chat')),
 is_self_chat boolean,
 FOREIGN KEY(chat,account) REFERENCES chats(id,account) ON DELETE CASCADE
);
CREATE TABLE reply_dismissals (
 key_id uuid NOT NULL REFERENCES api_keys(id) ON DELETE CASCADE,
 chat text NOT NULL REFERENCES chats(id) ON DELETE CASCADE,
 message text NOT NULL REFERENCES messages(id),
 revision integer NOT NULL CHECK(revision>0),
 incoming_head text NOT NULL REFERENCES messages(id),
 dismissed_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 PRIMARY KEY(key_id,chat)
);
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
 'dismiss_reply'));

-- Bound each direction's newest-message lookup without sorting complete chats.
CREATE INDEX message_reply_order ON messages(account,chat,direction,sent_at DESC,
 (CASE WHEN provider_id ~ '^[0-9]{1,18}$' THEN provider_id::bigint END) DESC NULLS LAST,id DESC)
 WHERE NOT deleted;
