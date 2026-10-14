-- Run as owner. No password here. Assign LOGIN membership separately using secrets.
DO $$ BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname='messaging_gateway') THEN
        CREATE ROLE messaging_gateway NOLOGIN;
    END IF;
END $$;
GRANT USAGE ON SCHEMA public TO messaging_gateway;
GRANT SELECT ON probe_metadata,accounts,principals,api_keys,chats,messages,attachments,
    events,feed_counter,source_status,media_jobs,message_revisions TO messaging_gateway;
GRANT SELECT,INSERT,UPDATE,DELETE ON consumers,deliveries,download_grants TO messaging_gateway;
GRANT SELECT ON reviews,review_sources TO messaging_gateway;
GRANT SELECT ON sender_profiles TO messaging_gateway;
GRANT SELECT ON attachment_text TO messaging_gateway;
GRANT INSERT(position,principal,request_id,message,revision,result_ref) ON reviews TO messaging_gateway;
GRANT INSERT(review,message,revision) ON review_sources TO messaging_gateway;
GRANT SELECT,UPDATE(value) ON review_counter TO messaging_gateway;
GRANT INSERT(principal,key_id,operation,outcome,error_code,resource_ids) ON audit_log TO messaging_gateway;
-- Audit rows are append-only to the gateway. No SELECT/UPDATE/DELETE; no grants
-- on credentials, ACL mutation, DDL, provider sessions or collector ownership.
GRANT SELECT ON message_metadata,attachment_enrichment,collector_health,collector_chats,whatsapp_checkpoints TO messaging_gateway;

GRANT SELECT ON attachment_text_chunks,attachment_enrichment_chunks TO messaging_gateway;
GRANT SELECT ON message_conflicts TO messaging_gateway;

GRANT SELECT ON processing_profiles TO messaging_gateway;
GRANT SELECT(account,expires_at) ON collector_leases TO messaging_gateway;
GRANT SELECT ON whatsapp_identity_observations TO messaging_gateway;
GRANT SELECT ON account_policies TO messaging_gateway;
GRANT SELECT ON outgoing_messages TO messaging_gateway;
GRANT SELECT(epoch) ON collector_leases TO messaging_gateway;
GRANT INSERT(principal,key_id,scope_version,request_id,request_hash,account,chat,
    provider_chat,body,reply_provider_id) ON outgoing_messages TO messaging_gateway;
