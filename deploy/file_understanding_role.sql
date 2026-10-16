-- Apply as database owner after schema 34. Grant this NOLOGIN role to a
-- dedicated login; never share collector, model-key or operator credentials.
DO $$ BEGIN
 IF NOT EXISTS(SELECT FROM pg_roles WHERE rolname='messaging_file_understanding') THEN
  CREATE ROLE messaging_file_understanding NOLOGIN;
 END IF;
END $$;
GRANT USAGE ON SCHEMA public TO messaging_file_understanding;
GRANT SELECT ON attachments,messages,attachment_text,attachment_enrichment,attachment_residency
 TO messaging_file_understanding;
GRANT SELECT,UPDATE ON attachment_understanding TO messaging_file_understanding;
GRANT SELECT,INSERT,UPDATE ON file_understanding_profiles,enrichment_budget,
 processing_budget,processing_control,processing_resource_leases TO messaging_file_understanding;
