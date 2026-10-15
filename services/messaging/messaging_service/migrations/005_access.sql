-- Existing managed keys keep their explicit account scope and read/download/ACK.
-- NULL chat_ids means all chats in those accounts; an empty array grants none.
ALTER TABLE principals ADD COLUMN chat_ids text[];
ALTER TABLE principals ADD COLUMN permissions text[] NOT NULL
    DEFAULT ARRAY['cursor-write','download','read']::text[];
ALTER TABLE principals ADD COLUMN enabled boolean NOT NULL DEFAULT true;
ALTER TABLE principals ADD CONSTRAINT supported_permissions
    CHECK (permissions <@ ARRAY['cursor-write','download','read']::text[]);
ALTER TABLE api_keys ADD COLUMN id uuid NOT NULL DEFAULT gen_random_uuid();
ALTER TABLE api_keys ADD CONSTRAINT api_keys_public_id UNIQUE(id);

CREATE TABLE audit_log (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    occurred_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    principal text,
    key_id uuid,
    actor text NOT NULL DEFAULT 'gateway' CHECK(actor IN ('gateway','operator')),
    db_role name NOT NULL DEFAULT current_user,
    operation text NOT NULL CHECK(operation IN (
        'capabilities','list_chats','search','get_messages','get_context','get_updates',
        'get_attachment','download','ack_updates','issue_key','revoke_key','set_access','http_boundary')),
    outcome text NOT NULL CHECK(outcome IN ('ok','denied','error')),
    error_code text CHECK(error_code ~ '^[a-z_]{1,64}$'),
    resource_ids text[] NOT NULL DEFAULT '{}' CHECK(cardinality(resource_ids)<=100)
);
CREATE INDEX audit_time ON audit_log(occurred_at,id);
CREATE INDEX audit_principal_time ON audit_log(principal,occurred_at,id);
CREATE INDEX message_chat_date ON messages(account,chat,sent_at,id);
