-- Opt-in capability: existing principals retain their exact permissions/scopes.
ALTER TABLE principals DROP CONSTRAINT supported_permissions;
ALTER TABLE principals ADD CONSTRAINT supported_permissions
    CHECK (permissions <@ ARRAY['cursor-write','download','read','review-write']::text[]);
ALTER TABLE audit_log DROP CONSTRAINT audit_log_operation_check;
ALTER TABLE audit_log ADD CONSTRAINT audit_log_operation_check CHECK(operation IN (
    'capabilities','list_chats','search','get_messages','get_context','get_updates',
    'get_attachment','download','ack_updates','issue_key','revoke_key','set_access','http_boundary',
    'record_review','get_reviews'));

-- Early probe fixtures predate normalized ingest; preserve their current revision.
INSERT INTO message_revisions(message,revision,snapshot)
    SELECT m.id,m.revision,jsonb_build_object('message',m.id,'account',m.account,
    'chat',m.chat,'body',m.body,'revision',m.revision,'sent_at',m.sent_at,
    'deleted',m.deleted,'direction',m.direction) FROM messages m
    ON CONFLICT DO NOTHING;

CREATE TABLE review_counter (
    id integer PRIMARY KEY CHECK(id=1), value bigint NOT NULL DEFAULT 0
);
INSERT INTO review_counter(id) VALUES(1);
CREATE TABLE reviews (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    position bigint NOT NULL UNIQUE,
    principal text NOT NULL REFERENCES principals(id),
    request_id uuid NOT NULL,
    message text NOT NULL,
    revision integer NOT NULL CHECK(revision>0),
    result_ref text NOT NULL CHECK(length(result_ref) BETWEEN 1 AND 2048),
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    UNIQUE(principal,request_id),
    FOREIGN KEY(message,revision) REFERENCES message_revisions(message,revision)
);
CREATE INDEX review_message_position ON reviews(message,position);
CREATE TABLE review_sources (
    review uuid NOT NULL REFERENCES reviews(id) ON DELETE CASCADE,
    message text NOT NULL,
    revision integer NOT NULL CHECK(revision>0),
    PRIMARY KEY(review,message),
    FOREIGN KEY(message,revision) REFERENCES message_revisions(message,revision)
);
