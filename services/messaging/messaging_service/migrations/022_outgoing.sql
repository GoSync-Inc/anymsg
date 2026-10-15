-- Sending is opt-in. Existing credentials and collection policies remain read-only.
ALTER TABLE principals DROP CONSTRAINT supported_permissions;
ALTER TABLE principals ADD CONSTRAINT supported_permissions CHECK
 (permissions <@ ARRAY['cursor-write','download','read','review-write','send']::text[]);
ALTER TABLE account_policies ADD COLUMN send_enabled boolean NOT NULL DEFAULT false;
ALTER TABLE audit_log DROP CONSTRAINT audit_log_operation_check;
ALTER TABLE audit_log ADD CONSTRAINT audit_log_operation_check CHECK(operation IN (
 'capabilities','list_chats','search','get_messages','get_context','get_updates',
 'get_attachment','download','ack_updates','issue_key','revoke_key','set_access','http_boundary',
 'get_reviews','record_review','search_people','search_files','get_file_text',
 'get_file_occurrences','get_file_segments','get_sync_status','resolve_reference',
 'send_message','get_send_status','send_dispatch','set_sending'));
ALTER TABLE audit_log DROP CONSTRAINT audit_log_actor_check;
ALTER TABLE audit_log ADD CONSTRAINT audit_log_actor_check CHECK(actor IN ('gateway','operator','collector'));

CREATE TABLE outgoing_messages (
 id text PRIMARY KEY DEFAULT ('send_' || replace(gen_random_uuid()::text,'-','')),
 principal text NOT NULL REFERENCES principals(id),
 key_id uuid NOT NULL REFERENCES api_keys(id),
 scope_version integer NOT NULL,
 request_id text NOT NULL CHECK(request_id ~ '^[a-zA-Z0-9_.:-]{8,128}$'),
 request_hash text NOT NULL CHECK(request_hash ~ '^[a-f0-9]{64}$'),
 account text NOT NULL REFERENCES accounts(id),
 chat text NOT NULL,
 provider_chat text NOT NULL,
 body text NOT NULL CHECK(length(body) BETWEEN 1 AND 4000),
 reply_provider_id text,
 status text NOT NULL DEFAULT 'queued' CHECK(status IN
  ('queued','dispatching','sent','failed','expired','cancelled','unknown')),
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 expires_at timestamptz NOT NULL DEFAULT clock_timestamp()+interval '10 minutes',
 started_at timestamptz,
 finished_at timestamptz,
 lease_epoch bigint,
 provider_message_id text,
 error_code text CHECK(error_code ~ '^[a-z_]{1,64}$'),
 FOREIGN KEY(chat,account) REFERENCES chats(id,account),
 UNIQUE(principal,request_id)
);
CREATE INDEX outgoing_pending ON outgoing_messages(account,created_at,id) WHERE status='queued';
CREATE INDEX outgoing_principal_time ON outgoing_messages(principal,created_at);
