-- Operator-only cold archiver. Mount only the original CAS read-only, its own
-- SSH key and the cold identity; no provider sessions, R2 credentials or dumps.
DO $$ BEGIN
 IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname='messaging_cold_archive') THEN
  CREATE ROLE messaging_cold_archive NOLOGIN;
 END IF;
END $$;
GRANT USAGE ON SCHEMA public TO messaging_cold_archive;
GRANT SELECT ON probe_metadata,attachment_residency TO messaging_cold_archive;
GRANT SELECT,INSERT,UPDATE ON attachment_cold_copies TO messaging_cold_archive;
-- No residency, journal, fence or archive mutation: archiving cannot evict.
