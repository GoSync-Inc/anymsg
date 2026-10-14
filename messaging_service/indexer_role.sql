-- Run as owner after schema9. No provider credentials, API keys or archive writes.
DO $$ BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname='messaging_indexer') THEN
        CREATE ROLE messaging_indexer NOLOGIN;
    END IF;
END $$;
GRANT USAGE ON SCHEMA public TO messaging_indexer;
GRANT SELECT ON attachments,messages TO messaging_indexer;
GRANT SELECT,UPDATE ON attachment_text TO messaging_indexer;

GRANT SELECT,INSERT,UPDATE ON processing_resource_leases TO messaging_indexer;
