-- Durable passive subscriptions; no host receivers, sends or ACKs are enabled.
-- A new opt-in permission is never inherited by existing key ceilings.
ALTER TABLE principals DROP CONSTRAINT supported_permissions;
ALTER TABLE principals ADD CONSTRAINT supported_permissions CHECK(permissions <@ ARRAY[
 'cursor-write','download','read','review-write','send','read-live','contacts-read','profile-read',
 'groups-read','message-edit','message-delete','message-react','message-pin','read-state',
 'chat-manage','contacts-manage','groups-manage','profile-manage','subscription-write']::text[]);
ALTER TABLE api_keys DROP CONSTRAINT key_supported_permissions;
ALTER TABLE api_keys ADD CONSTRAINT key_supported_permissions CHECK(permissions IS NULL OR permissions <@ ARRAY[
 'cursor-write','download','read','review-write','send','read-live','contacts-read','profile-read',
 'groups-read','message-edit','message-delete','message-react','message-pin','read-state',
 'chat-manage','contacts-manage','groups-manage','profile-manage','subscription-write']::text[]);
ALTER TABLE audit_log DROP CONSTRAINT audit_log_operation_check;
ALTER TABLE audit_log ADD CONSTRAINT audit_log_operation_check CHECK(operation IN (
 'capabilities','list_chats','search','get_messages','get_context','get_updates',
 'get_attachment','download','ack_updates','issue_key','revoke_key','set_access','http_boundary',
 'get_reviews','record_review','search_people','search_files','get_file_text',
 'get_file_occurrences','get_file_segments','get_sync_status','resolve_reference',
 'send_message','get_send_status','send_dispatch','set_sending','preview_message',
 'set_key_permissions','set_operations','list_messenger_operations','preview_messenger_operation',
 'execute_messenger_operation','get_messenger_operation','messenger_dispatch',
 'create_subscription','list_subscriptions','get_subscription','update_subscription','get_subscription_events'));

CREATE TABLE subscriptions (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
 principal text NOT NULL REFERENCES principals(id),
 key_id uuid NOT NULL REFERENCES api_keys(id),
 scope_version integer NOT NULL CHECK(scope_version>0),
 key_permission_version integer NOT NULL CHECK(key_permission_version>0),
 version bigint NOT NULL DEFAULT 1 CHECK(version>0),
 state text NOT NULL DEFAULT 'active' CHECK(state IN ('active','paused','closed','expired','access_changed')),
 selector jsonb NOT NULL CHECK(jsonb_typeof(selector)='object' AND octet_length(selector::text)<=16384),
 label text NOT NULL DEFAULT '' CHECK(length(label)<=128),
 start_position bigint NOT NULL CHECK(start_position>=0),
 matched_through bigint NOT NULL CHECK(matched_through>=start_position),
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 expires_at timestamptz,
 blocked_reason text CHECK(blocked_reason IN ('match_budget','journal_gap')),
 gap jsonb CHECK(gap IS NULL OR (jsonb_typeof(gap)='object' AND octet_length(gap::text)<=4096)),
 missing_evidence bigint NOT NULL DEFAULT 0 CHECK(missing_evidence>=0),
 last_matched_at timestamptz
);
CREATE INDEX subscription_originator ON subscriptions(principal,key_id,created_at,id);
CREATE INDEX subscription_matching ON subscriptions(last_matched_at NULLS FIRST,created_at,id) WHERE state='active';

CREATE TABLE subscription_sources (
 subscription uuid NOT NULL REFERENCES subscriptions(id) ON DELETE CASCADE,
 account text NOT NULL,
 chat text NOT NULL,
 sender_ids text[] CHECK(sender_ids IS NULL OR cardinality(sender_ids)<=64),
 PRIMARY KEY(subscription,account,chat),
 FOREIGN KEY(chat,account) REFERENCES chats(id,account)
);
CREATE TABLE subscription_matches (
 subscription uuid NOT NULL REFERENCES subscriptions(id) ON DELETE CASCADE,
 event bigint NOT NULL REFERENCES events(id),
 matched_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 PRIMARY KEY(subscription,event)
);

-- Existing collector processes can continue publishing during a rolling
-- release. Fill missing structural evidence at INSERT, while that exact
-- message revision still exists, never by looking at a later mutable row.
-- Native reply/mention evidence may be unknown at an initial message event;
-- collector metadata arriving later must not retroactively rewrite it.
CREATE FUNCTION public.snapshot_event_identity() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE sender_id text; provider_id text; native_metadata jsonb;
BEGIN
 SELECT m.sender,m.provider_id,md.metadata INTO sender_id,provider_id,native_metadata
 FROM public.messages m LEFT JOIN public.message_metadata md
   ON md.message=m.id AND md.revision=m.revision
 WHERE m.id=NEW.message AND m.account=NEW.account AND m.revision=NEW.revision;
 IF FOUND THEN
   NEW.snapshot := NEW.snapshot || jsonb_build_object('sender',sender_id,'provider_id',provider_id);
   IF native_metadata IS NOT NULL THEN
     NEW.snapshot := NEW.snapshot || jsonb_build_object('metadata',native_metadata);
   END IF;
 END IF;
 RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION public.snapshot_event_identity() FROM PUBLIC;
CREATE TRIGGER snapshot_event_identity BEFORE INSERT ON events
FOR EACH ROW EXECUTE FUNCTION public.snapshot_event_identity();
