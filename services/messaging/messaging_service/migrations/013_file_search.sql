-- Reuse only raw, context-free extraction under an explicit processor profile.
-- Existing ready text remains searchable; no implicit OCR backfill or AI calls.
ALTER TABLE attachment_text ADD COLUMN processor_version text NOT NULL DEFAULT 'bounded-v1';
ALTER TABLE attachment_text ADD COLUMN extraction_profile text;
ALTER TABLE attachment_text ADD COLUMN extraction_metadata jsonb NOT NULL DEFAULT '{}';
ALTER TABLE attachment_text ADD COLUMN display_name text NOT NULL DEFAULT '';
-- Replace the generated simple vector so explicit decoded names are indexed too.
ALTER TABLE attachment_text DROP COLUMN document;
ALTER TABLE attachment_text ADD COLUMN document tsvector GENERATED ALWAYS AS
    (to_tsvector('simple',left(source_filename,1024)||' '||left(display_name,1024)||' '||text_content)) STORED;
CREATE INDEX attachment_text_document ON attachment_text USING gin(document);
ALTER TABLE attachment_text ADD COLUMN document_russian tsvector GENERATED ALWAYS AS
    (to_tsvector('russian',left(source_filename,1024)||' '||left(display_name,1024)||' '||text_content)) STORED;
ALTER TABLE attachment_text ADD COLUMN document_english tsvector GENERATED ALWAYS AS
    (to_tsvector('english',left(source_filename,1024)||' '||left(display_name,1024)||' '||text_content)) STORED;
ALTER TABLE attachment_text ADD COLUMN document_indonesian tsvector GENERATED ALWAYS AS
    (to_tsvector('indonesian',left(source_filename,1024)||' '||left(display_name,1024)||' '||text_content)) STORED;
CREATE INDEX attachment_text_russian ON attachment_text USING gin(document_russian);
CREATE INDEX attachment_text_english ON attachment_text USING gin(document_english);
CREATE INDEX attachment_text_indonesian ON attachment_text USING gin(document_indonesian);
CREATE INDEX attachment_text_reuse ON attachment_text(source_digest,extraction_profile,processor_version)
    WHERE state='ready';
CREATE INDEX attachment_occurrences ON attachments(digest,message,id) WHERE active;

-- Reset ancillary metadata in the same transaction as the existing 009 trigger.
-- This function operates only on its NEW row and needs no additional privilege.
CREATE FUNCTION public.reset_file_extraction_metadata() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog AS $$
BEGIN
    IF NEW.state='queued' AND
       (OLD.state IS DISTINCT FROM NEW.state OR OLD.source_digest IS DISTINCT FROM NEW.source_digest
        OR OLD.source_revision IS DISTINCT FROM NEW.source_revision
        OR OLD.source_filename IS DISTINCT FROM NEW.source_filename
        OR OLD.source_mime IS DISTINCT FROM NEW.source_mime) THEN
        NEW.extraction_metadata='{}'::jsonb;
        NEW.display_name='';
    END IF;
    RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION public.reset_file_extraction_metadata() FROM PUBLIC;
CREATE TRIGGER reset_file_extraction_metadata BEFORE UPDATE ON attachment_text
    FOR EACH ROW EXECUTE FUNCTION public.reset_file_extraction_metadata();
