-- Operator-only installation. No credentials/session/message mutation rights.
DO $$ BEGIN
 IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname='messaging_enricher') THEN
 CREATE ROLE messaging_enricher NOLOGIN;
 END IF;
END $$;
GRANT USAGE ON SCHEMA public TO messaging_enricher;
GRANT SELECT ON attachments,messages,attachment_text TO messaging_enricher;
GRANT SELECT,UPDATE ON attachment_enrichment TO messaging_enricher;
GRANT SELECT,INSERT,UPDATE ON enrichment_budget TO messaging_enricher;

GRANT SELECT,INSERT,UPDATE ON processing_profiles TO messaging_enricher;

GRANT SELECT,INSERT,UPDATE ON processing_resource_leases TO messaging_enricher;
GRANT SELECT,INSERT,UPDATE ON processing_budget,processing_control TO messaging_enricher;
-- Occurrence-scoped readiness publication; no subscription or key access.
GRANT SELECT ON message_metadata,message_revisions,events TO messaging_enricher;
GRANT SELECT,UPDATE(value) ON feed_counter TO messaging_enricher;
GRANT INSERT ON events TO messaging_enricher;
