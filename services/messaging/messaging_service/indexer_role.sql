-- Run as owner after schema34. No provider credentials, API keys or archive writes.
DO $$ BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname='messaging_indexer') THEN
        CREATE ROLE messaging_indexer NOLOGIN;
    END IF;
END $$;
GRANT USAGE ON SCHEMA public TO messaging_indexer;
GRANT SELECT ON attachments,messages TO messaging_indexer;
GRANT SELECT ON attachment_residency,attachment_eviction_journal TO messaging_indexer;
GRANT SELECT,UPDATE ON attachment_text TO messaging_indexer;

GRANT SELECT,INSERT,UPDATE ON processing_resource_leases TO messaging_indexer;
-- Occurrence-scoped readiness publication; no subscription or key access.
GRANT SELECT ON message_metadata,message_revisions,events TO messaging_indexer;
GRANT SELECT,UPDATE(value) ON feed_counter TO messaging_indexer;
GRANT INSERT ON events TO messaging_indexer;
