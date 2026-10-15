-- Credential-local event consumption and explicit operator-registered hint delivery.
-- NULL legacy key ceilings intentionally do not inherit new capabilities.
ALTER TABLE principals DROP CONSTRAINT supported_permissions;
ALTER TABLE principals ADD CONSTRAINT supported_permissions CHECK(permissions <@ ARRAY[
 'cursor-write','download','read','review-write','send','read-live','contacts-read','profile-read',
 'groups-read','message-edit','message-delete','message-react','message-pin','read-state',
 'chat-manage','contacts-manage','groups-manage','profile-manage','subscription-write','upload',
 'subscription-ack','feedback-write']::text[]);
ALTER TABLE api_keys DROP CONSTRAINT key_supported_permissions;
ALTER TABLE api_keys ADD CONSTRAINT key_supported_permissions CHECK(permissions IS NULL OR permissions <@ ARRAY[
 'cursor-write','download','read','review-write','send','read-live','contacts-read','profile-read',
 'groups-read','message-edit','message-delete','message-react','message-pin','read-state',
 'chat-manage','contacts-manage','groups-manage','profile-manage','subscription-write','upload',
 'subscription-ack','feedback-write']::text[]);
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
 'dismiss_reply','prepare_file_upload','upload_file_chunk','get_file_upload','request_file_download',
 'fetch_subscription_events','ack_subscription_events','wait_for_subscription_events',
 'configure_subscription_delivery','test_subscription','submit_feedback','list_feedback'));
CREATE TABLE subscription_consumption (
 subscription uuid PRIMARY KEY REFERENCES subscriptions(id) ON DELETE CASCADE,
 acked_through bigint NOT NULL CHECK(acked_through>=0),
 version bigint NOT NULL DEFAULT 1 CHECK(version>0),
 updated_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE TABLE subscription_receipts (
 receipt_hash text PRIMARY KEY CHECK(receipt_hash ~ '^[a-f0-9]{64}$'),
 subscription uuid NOT NULL REFERENCES subscriptions(id) ON DELETE CASCADE,
 principal text NOT NULL REFERENCES principals(id), key_id uuid NOT NULL REFERENCES api_keys(id),
 scope_version integer NOT NULL, key_permission_version integer NOT NULL,
 subscription_version bigint NOT NULL, consumption_version bigint NOT NULL,
 after_position bigint NOT NULL, through_position bigint NOT NULL CHECK(through_position>=after_position),
 expires_at timestamptz NOT NULL DEFAULT clock_timestamp()+interval '5 minutes',
 ack_result jsonb CHECK(ack_result IS NULL OR octet_length(ack_result::text)<=2048)
);
CREATE INDEX subscription_receipt_expiry ON subscription_receipts(subscription,expires_at);
CREATE TABLE subscription_receivers (
 id uuid PRIMARY KEY, principal text NOT NULL REFERENCES principals(id),
 key_id uuid NOT NULL REFERENCES api_keys(id), endpoint text NOT NULL CHECK(length(endpoint)<=2048),
 secret_file text NOT NULL CHECK(length(secret_file)<=4096),
 enabled boolean NOT NULL DEFAULT true, created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 UNIQUE(id,principal,key_id)
);
CREATE TABLE subscription_delivery (
 subscription uuid PRIMARY KEY REFERENCES subscriptions(id) ON DELETE CASCADE,
 receiver uuid NOT NULL REFERENCES subscription_receivers(id),
 configured_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE TABLE subscription_outbox (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
 subscription uuid NOT NULL REFERENCES subscriptions(id) ON DELETE CASCADE,
 subscription_version bigint NOT NULL, receiver uuid NOT NULL REFERENCES subscription_receivers(id),
 through_position bigint NOT NULL CHECK(through_position>=0),
 kind text NOT NULL CHECK(kind IN ('events','test')), request_id text NOT NULL DEFAULT '',
 state text NOT NULL DEFAULT 'pending' CHECK(state IN ('pending','leased','delivered','dead_letter','cancelled')),
 attempts integer NOT NULL DEFAULT 0 CHECK(attempts BETWEEN 0 AND 8),
 next_attempt_at timestamptz NOT NULL DEFAULT clock_timestamp(), lease_token uuid, lease_until timestamptz,
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(), finished_at timestamptz,
 error_code text CHECK(length(error_code)<=64),
 UNIQUE(subscription,subscription_version,kind,through_position,request_id)
);
CREATE INDEX subscription_hint_dispatch ON subscription_outbox(next_attempt_at,created_at,id)
 WHERE state IN ('pending','leased');
CREATE TABLE agent_feedback (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), principal text NOT NULL REFERENCES principals(id),
 key_id uuid NOT NULL REFERENCES api_keys(id), request_id text NOT NULL CHECK(length(request_id) BETWEEN 8 AND 128),
 request_hash text NOT NULL CHECK(request_hash ~ '^[a-f0-9]{64}$'),
 category text NOT NULL CHECK(category IN ('bug','feature','quality','other')),
 description text NOT NULL CHECK(length(description) BETWEEN 1 AND 4000),
 operation text, refs jsonb NOT NULL CHECK(octet_length(refs::text)<=8192),
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(), UNIQUE(key_id,request_id)
);
CREATE INDEX agent_feedback_owner ON agent_feedback(principal,key_id,created_at,id);
