-- Requested destination/preview remain immutable. Native providers may return a
-- separately verified wire namespace (e.g. WhatsApp PN→LID). Never rewrite IDs.
ALTER TABLE outgoing_messages ADD COLUMN archive_reference jsonb CHECK (
 archive_reference IS NULL OR (
  jsonb_typeof(archive_reference)='object' AND
  archive_reference ?& ARRAY['provider_chat_id','provider_message_id'] AND
  archive_reference - ARRAY['provider_chat_id','provider_message_id'] = '{}'::jsonb AND
  jsonb_typeof(archive_reference->'provider_chat_id')='string' AND
  jsonb_typeof(archive_reference->'provider_message_id')='string' AND
  length(archive_reference->>'provider_chat_id') BETWEEN 1 AND 256 AND
  length(archive_reference->>'provider_message_id') BETWEEN 1 AND 256
 ));
CREATE INDEX outgoing_wire_reference ON outgoing_messages(
 account,(archive_reference->>'provider_chat_id'),(archive_reference->>'provider_message_id'))
 WHERE status='sent' AND archive_reference IS NOT NULL;
