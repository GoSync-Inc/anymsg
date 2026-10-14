-- Operator-only grant template. Run in the archive database after migrations.
-- Set the password separately through a private operator connection; no secret here.
DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='messaging_collector') THEN
        CREATE ROLE messaging_collector LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE
            NOREPLICATION NOBYPASSRLS NOINHERIT;
    END IF;
END $$;
GRANT USAGE ON SCHEMA public TO messaging_collector;
GRANT SELECT ON accounts,account_policies,probe_metadata,archive_migrations TO messaging_collector;
GRANT SELECT,INSERT,UPDATE ON collector_leases,collector_chats,provider_tombstones,whatsapp_checkpoints,
    chats,messages,attachments,ingest_receipts,message_revisions,media_jobs,sender_profiles TO messaging_collector;
GRANT SELECT,UPDATE ON source_status,feed_counter TO messaging_collector;
GRANT SELECT,INSERT ON events TO messaging_collector;
-- No access to token hashes, consumers, grants, arbitrary DDL or role administration.
GRANT SELECT,INSERT,UPDATE ON message_metadata,collector_health TO messaging_collector;
GRANT SELECT,INSERT,UPDATE ON message_conflicts TO messaging_collector;
GRANT SELECT,INSERT,UPDATE ON whatsapp_identity_observations TO messaging_collector;
GRANT SELECT,INSERT,UPDATE,DELETE ON media_reservations TO messaging_collector;
-- Outbound dispatch rechecks only policy and public key metadata, never token hashes.
GRANT SELECT(id,accounts,chat_ids,permissions,enabled,scope_version) ON principals TO messaging_collector;
GRANT SELECT(id,principal,revoked,expires_at) ON api_keys TO messaging_collector;
GRANT SELECT ON outgoing_messages TO messaging_collector;
GRANT UPDATE(status,started_at,finished_at,lease_epoch,provider_message_id,error_code)
    ON outgoing_messages TO messaging_collector;
GRANT INSERT(principal,key_id,operation,outcome,error_code,resource_ids,actor) ON audit_log TO messaging_collector;
