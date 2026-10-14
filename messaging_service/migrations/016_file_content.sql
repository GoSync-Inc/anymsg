-- Whole retained text is paginated separately from bounded passage indexes.
-- 32k characters per passage, 1k overlap; each vector stays far below PostgreSQL's 1MiB limit.
ALTER TABLE attachment_text DROP CONSTRAINT attachment_text_text_content_check;
ALTER TABLE attachment_text ADD CONSTRAINT attachment_text_text_content_check CHECK(length(text_content)<=1000000);

ALTER TABLE attachment_text DROP COLUMN document;
ALTER TABLE attachment_text ADD COLUMN document tsvector GENERATED ALWAYS AS
 (to_tsvector('simple',left(source_filename,1024)||' '||left(display_name,1024)||' '||left(text_content,32000))) STORED;
CREATE INDEX attachment_text_document_bounded ON attachment_text USING gin(document);

ALTER TABLE attachment_text DROP COLUMN document_russian;
ALTER TABLE attachment_text ADD COLUMN document_russian tsvector GENERATED ALWAYS AS
 (to_tsvector('russian',left(source_filename,1024)||' '||left(display_name,1024)||' '||left(text_content,32000))) STORED;
CREATE INDEX attachment_text_document_russian_bounded ON attachment_text USING gin(document_russian);

ALTER TABLE attachment_text DROP COLUMN document_english;
ALTER TABLE attachment_text ADD COLUMN document_english tsvector GENERATED ALWAYS AS
 (to_tsvector('english',left(source_filename,1024)||' '||left(display_name,1024)||' '||left(text_content,32000))) STORED;
CREATE INDEX attachment_text_document_english_bounded ON attachment_text USING gin(document_english);

ALTER TABLE attachment_text DROP COLUMN document_indonesian;
ALTER TABLE attachment_text ADD COLUMN document_indonesian tsvector GENERATED ALWAYS AS
 (to_tsvector('indonesian',left(source_filename,1024)||' '||left(display_name,1024)||' '||left(text_content,32000))) STORED;
CREATE INDEX attachment_text_document_indonesian_bounded ON attachment_text USING gin(document_indonesian);

CREATE TABLE attachment_text_chunks (
 attachment text NOT NULL REFERENCES attachment_text(attachment) ON DELETE CASCADE,
 chunk_index integer NOT NULL CHECK(chunk_index>=0 AND chunk_index<34),
 source_digest text NOT NULL,
 source_revision integer NOT NULL,
 start_offset integer NOT NULL CHECK(start_offset>=0 AND start_offset<1000000),
 end_offset integer NOT NULL CHECK(end_offset>=start_offset AND end_offset<=1000000),
 header text NOT NULL CHECK(length(header)<=8192),
 text_content text NOT NULL CHECK(length(text_content)<=32000),
 PRIMARY KEY(attachment,chunk_index),
 CHECK(length(text_content)=end_offset-start_offset),

 document tsvector GENERATED ALWAYS AS (to_tsvector('simple',header||' '||text_content)) STORED,
 document_russian tsvector GENERATED ALWAYS AS (to_tsvector('russian',header||' '||text_content)) STORED,
 document_english tsvector GENERATED ALWAYS AS (to_tsvector('english',header||' '||text_content)) STORED,
 document_indonesian tsvector GENERATED ALWAYS AS (to_tsvector('indonesian',header||' '||text_content)) STORED
);

CREATE INDEX attachment_text_chunks_source ON attachment_text_chunks(attachment,source_digest,source_revision);

CREATE INDEX attachment_text_chunks_document ON attachment_text_chunks USING gin(document);

CREATE INDEX attachment_text_chunks_document_russian ON attachment_text_chunks USING gin(document_russian);

CREATE INDEX attachment_text_chunks_document_english ON attachment_text_chunks USING gin(document_english);

CREATE INDEX attachment_text_chunks_document_indonesian ON attachment_text_chunks USING gin(document_indonesian);

CREATE FUNCTION public.rebuild_attachment_text_chunks() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
BEGIN
 DELETE FROM public.attachment_text_chunks WHERE attachment=NEW.attachment;
 IF NEW.state='ready' THEN
  INSERT INTO public.attachment_text_chunks(attachment,chunk_index,source_digest,source_revision,
      start_offset,end_offset,header,text_content)
  SELECT NEW.attachment,chunk_offset/31000,NEW.source_digest,NEW.source_revision,
      chunk_offset,least(chunk_offset+32000,length(NEW.text_content)),left(NEW.source_filename,1024)||' '||left(NEW.display_name,1024),
      substring(NEW.text_content FROM chunk_offset+1 FOR 32000)
  FROM generate_series(0,greatest(length(NEW.text_content)-1001,0),31000) AS chunk_offset;
 END IF;
 RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION public.rebuild_attachment_text_chunks() FROM PUBLIC;
CREATE TRIGGER rebuild_attachment_text_chunks AFTER INSERT OR UPDATE OF text_content,state,source_digest,source_revision,source_filename,display_name ON attachment_text
 FOR EACH ROW EXECUTE FUNCTION public.rebuild_attachment_text_chunks();
INSERT INTO attachment_text_chunks(attachment,chunk_index,source_digest,source_revision,start_offset,end_offset,header,text_content)
 SELECT attachment,chunk_offset/31000,source_digest,source_revision,chunk_offset,
     least(chunk_offset+32000,length(text_content)),left(source_filename,1024)||' '||left(display_name,1024),substring(text_content FROM chunk_offset+1 FOR 32000)
 FROM attachment_text CROSS JOIN LATERAL generate_series(0,greatest(length(text_content)-1001,0),31000) AS chunk_offset
 WHERE state='ready';

ALTER TABLE attachment_enrichment DROP COLUMN document;
ALTER TABLE attachment_enrichment ADD COLUMN document tsvector GENERATED ALWAYS AS
 (to_tsvector('simple',title||' '||summary||' '||left(coalesce(metadata->>'document_type',''),80)||' '||left(text_content,32000))) STORED;
CREATE INDEX attachment_enrichment_document_bounded ON attachment_enrichment USING gin(document);

ALTER TABLE attachment_enrichment DROP COLUMN document_russian;
ALTER TABLE attachment_enrichment ADD COLUMN document_russian tsvector GENERATED ALWAYS AS
 (to_tsvector('russian',title||' '||summary||' '||left(coalesce(metadata->>'document_type',''),80)||' '||left(text_content,32000))) STORED;
CREATE INDEX attachment_enrichment_document_russian_bounded ON attachment_enrichment USING gin(document_russian);

ALTER TABLE attachment_enrichment DROP COLUMN document_english;
ALTER TABLE attachment_enrichment ADD COLUMN document_english tsvector GENERATED ALWAYS AS
 (to_tsvector('english',title||' '||summary||' '||left(coalesce(metadata->>'document_type',''),80)||' '||left(text_content,32000))) STORED;
CREATE INDEX attachment_enrichment_document_english_bounded ON attachment_enrichment USING gin(document_english);

ALTER TABLE attachment_enrichment DROP COLUMN document_indonesian;
ALTER TABLE attachment_enrichment ADD COLUMN document_indonesian tsvector GENERATED ALWAYS AS
 (to_tsvector('indonesian',title||' '||summary||' '||left(coalesce(metadata->>'document_type',''),80)||' '||left(text_content,32000))) STORED;
CREATE INDEX attachment_enrichment_document_indonesian_bounded ON attachment_enrichment USING gin(document_indonesian);

CREATE TABLE attachment_enrichment_chunks (
 attachment text NOT NULL REFERENCES attachment_enrichment(attachment) ON DELETE CASCADE,
 chunk_index integer NOT NULL CHECK(chunk_index>=0 AND chunk_index<34),
 source_digest text NOT NULL,
 source_revision integer NOT NULL,
 start_offset integer NOT NULL CHECK(start_offset>=0 AND start_offset<1000000),
 end_offset integer NOT NULL CHECK(end_offset>=start_offset AND end_offset<=1000000),
 header text NOT NULL CHECK(length(header)<=8192),
 text_content text NOT NULL CHECK(length(text_content)<=32000),
 PRIMARY KEY(attachment,chunk_index),
 CHECK(length(text_content)=end_offset-start_offset),

 document tsvector GENERATED ALWAYS AS (to_tsvector('simple',header||' '||text_content)) STORED,
 document_russian tsvector GENERATED ALWAYS AS (to_tsvector('russian',header||' '||text_content)) STORED,
 document_english tsvector GENERATED ALWAYS AS (to_tsvector('english',header||' '||text_content)) STORED,
 document_indonesian tsvector GENERATED ALWAYS AS (to_tsvector('indonesian',header||' '||text_content)) STORED
);

CREATE INDEX attachment_enrichment_chunks_source ON attachment_enrichment_chunks(attachment,source_digest,source_revision);

CREATE INDEX attachment_enrichment_chunks_document ON attachment_enrichment_chunks USING gin(document);

CREATE INDEX attachment_enrichment_chunks_document_russian ON attachment_enrichment_chunks USING gin(document_russian);

CREATE INDEX attachment_enrichment_chunks_document_english ON attachment_enrichment_chunks USING gin(document_english);

CREATE INDEX attachment_enrichment_chunks_document_indonesian ON attachment_enrichment_chunks USING gin(document_indonesian);

CREATE FUNCTION public.rebuild_attachment_enrichment_chunks() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
BEGIN
 DELETE FROM public.attachment_enrichment_chunks WHERE attachment=NEW.attachment;
 IF NEW.state='ready' THEN
  INSERT INTO public.attachment_enrichment_chunks(attachment,chunk_index,source_digest,source_revision,
      start_offset,end_offset,header,text_content)
  SELECT NEW.attachment,chunk_offset/31000,NEW.source_digest,NEW.source_revision,
      chunk_offset,least(chunk_offset+32000,length(NEW.text_content)),NEW.title||' '||NEW.summary||' '||left(coalesce(NEW.metadata->>'document_type',''),80),
      substring(NEW.text_content FROM chunk_offset+1 FOR 32000)
  FROM generate_series(0,greatest(length(NEW.text_content)-1001,0),31000) AS chunk_offset;
 END IF;
 RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION public.rebuild_attachment_enrichment_chunks() FROM PUBLIC;
CREATE TRIGGER rebuild_attachment_enrichment_chunks AFTER INSERT OR UPDATE OF text_content,state,source_digest,source_revision,title,summary,metadata ON attachment_enrichment
 FOR EACH ROW EXECUTE FUNCTION public.rebuild_attachment_enrichment_chunks();
INSERT INTO attachment_enrichment_chunks(attachment,chunk_index,source_digest,source_revision,start_offset,end_offset,header,text_content)
 SELECT attachment,chunk_offset/31000,source_digest,source_revision,chunk_offset,
     least(chunk_offset+32000,length(text_content)),title||' '||summary||' '||left(coalesce(metadata->>'document_type',''),80),substring(text_content FROM chunk_offset+1 FOR 32000)
 FROM attachment_enrichment CROSS JOIN LATERAL generate_series(0,greatest(length(text_content)-1001,0),31000) AS chunk_offset
 WHERE state='ready';

ALTER TABLE audit_log DROP CONSTRAINT audit_log_operation_check;
ALTER TABLE audit_log ADD CONSTRAINT audit_log_operation_check CHECK(operation IN (
 'capabilities','list_chats','search','get_messages','get_context','get_updates',
 'get_attachment','download','ack_updates','issue_key','revoke_key','set_access','http_boundary',
 'record_review','get_reviews','search_people','search_files','get_file_text','get_file_occurrences',
 'get_sync_status','get_file_segments'));
