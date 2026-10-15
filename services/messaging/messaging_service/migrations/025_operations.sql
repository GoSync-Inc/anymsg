-- New capabilities require both an explicit key grant and account opt-in.
-- Existing credentials retain only the five legacy capabilities; new principal
-- permissions can never silently grant administrative access to an old key.
ALTER TABLE principals DROP CONSTRAINT supported_permissions;
ALTER TABLE principals ADD CONSTRAINT supported_permissions CHECK(permissions <@ ARRAY[
 'cursor-write','download','read','review-write','send','read-live','contacts-read','profile-read',
 'groups-read','message-edit','message-delete','message-react','message-pin','read-state',
 'chat-manage','contacts-manage','groups-manage','profile-manage']::text[]);
ALTER TABLE api_keys ADD COLUMN permissions text[];
UPDATE api_keys SET permissions=ARRAY['cursor-write','download','read','review-write','send']::text[];
ALTER TABLE api_keys ADD CONSTRAINT key_supported_permissions CHECK(permissions IS NULL OR permissions <@ ARRAY[
 'cursor-write','download','read','review-write','send','read-live','contacts-read','profile-read',
 'groups-read','message-edit','message-delete','message-react','message-pin','read-state',
 'chat-manage','contacts-manage','groups-manage','profile-manage']::text[]);
ALTER TABLE api_keys ADD COLUMN permission_version integer NOT NULL DEFAULT 1 CHECK(permission_version>0);
-- Pending legacy text sends were reviewed under key version 1. New workers
-- fence them against explicit key-ceiling changes without rewriting payloads.
ALTER TABLE outgoing_messages ADD COLUMN key_permission_version integer NOT NULL DEFAULT 1
 CHECK(key_permission_version>0);
ALTER TABLE account_policies ADD COLUMN operations_enabled text[] NOT NULL DEFAULT '{}'
 CHECK(cardinality(operations_enabled)<=100);
ALTER TABLE audit_log DROP CONSTRAINT audit_log_operation_check;
ALTER TABLE audit_log ADD CONSTRAINT audit_log_operation_check CHECK(operation IN (
 'capabilities','list_chats','search','get_messages','get_context','get_updates',
 'get_attachment','download','ack_updates','issue_key','revoke_key','set_access','http_boundary',
 'get_reviews','record_review','search_people','search_files','get_file_text',
 'get_file_occurrences','get_file_segments','get_sync_status','resolve_reference',
 'send_message','get_send_status','send_dispatch','set_sending','preview_message',
 'set_key_permissions','set_operations','list_messenger_operations','preview_messenger_operation',
 'execute_messenger_operation','get_messenger_operation','messenger_dispatch'));

CREATE TABLE messenger_commands (
 id text PRIMARY KEY DEFAULT ('op_'||replace(gen_random_uuid()::text,'-','')),
 principal text NOT NULL REFERENCES principals(id), key_id uuid NOT NULL REFERENCES api_keys(id),
 scope_version integer NOT NULL CHECK(scope_version>0), key_permission_version integer NOT NULL CHECK(key_permission_version>0),
 request_id text NOT NULL CHECK(request_id ~ '^[a-zA-Z0-9_.:-]{8,128}$'),
 request_hash text NOT NULL CHECK(request_hash ~ '^[a-f0-9]{64}$'),
 account text NOT NULL REFERENCES accounts(id),
 chat text, message text REFERENCES messages(id), expected_revision integer NOT NULL DEFAULT 0 CHECK(expected_revision>=0),
 operation text NOT NULL CHECK(operation ~ '^[a-z_]{1,64}$'),
 permission text NOT NULL CHECK(permission ~ '^[a-z-]{1,64}$'),
 mutation boolean NOT NULL,
 payload jsonb NOT NULL CHECK(jsonb_typeof(payload)='object' AND octet_length(payload::text)<=65536),
 preview_digest text CHECK(preview_digest IS NULL OR preview_digest ~ '^[a-f0-9]{64}$'),
 status text NOT NULL DEFAULT 'queued' CHECK(status IN('queued','dispatching','succeeded','failed','expired','cancelled','unknown')),
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 expires_at timestamptz NOT NULL DEFAULT clock_timestamp()+interval '10 minutes',
 started_at timestamptz, finished_at timestamptz, lease_epoch bigint,
 result jsonb CHECK(result IS NULL OR (jsonb_typeof(result)='object' AND octet_length(result::text)<=65536)),
 error_code text CHECK(error_code ~ '^[a-z_]{1,64}$'),
 FOREIGN KEY(chat,account) REFERENCES chats(id,account),
 UNIQUE(principal,key_id,request_id)
);
CREATE INDEX messenger_commands_pending ON messenger_commands(account,created_at,id) WHERE status='queued';
CREATE INDEX messenger_commands_originator ON messenger_commands(principal,key_id,created_at,id);
