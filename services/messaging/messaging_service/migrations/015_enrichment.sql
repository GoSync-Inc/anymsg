-- Optional model-derived data. No model/provider is enabled by this migration.
CREATE TABLE message_metadata (
    message text PRIMARY KEY REFERENCES messages(id) ON DELETE CASCADE,
    revision integer NOT NULL,
    metadata jsonb NOT NULL DEFAULT '{}',
    observed_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE TABLE attachment_enrichment (
    attachment text PRIMARY KEY REFERENCES attachments(id) ON DELETE CASCADE,
    source_digest text NOT NULL,
    source_revision integer NOT NULL,
    state text NOT NULL DEFAULT 'unconfigured' CHECK(state IN
        ('unconfigured','queued','leased','ready','failed','unsupported','superseded')),
    profile_hash text,
    kind text CHECK(kind IN ('audio','image','document')),
    provider text, model text,
    title text NOT NULL DEFAULT '' CHECK(length(title)<=300),
    summary text NOT NULL DEFAULT '' CHECK(length(summary)<=4000),
    text_content text NOT NULL DEFAULT '' CHECK(length(text_content)<=1000000),
    metadata jsonb NOT NULL DEFAULT '{}',
    truncated boolean NOT NULL DEFAULT false,
    error_code text,
    attempts integer NOT NULL DEFAULT 0,
    lease_token uuid, lease_until timestamptz,
    next_due timestamptz NOT NULL DEFAULT clock_timestamp(),
    updated_at timestamptz NOT NULL DEFAULT clock_timestamp(), indexed_at timestamptz,
    document tsvector GENERATED ALWAYS AS (to_tsvector('simple',title||' '||summary||' '||text_content)) STORED,
    document_russian tsvector GENERATED ALWAYS AS (to_tsvector('russian',title||' '||summary||' '||text_content)) STORED,
    document_english tsvector GENERATED ALWAYS AS (to_tsvector('english',title||' '||summary||' '||text_content)) STORED,
    document_indonesian tsvector GENERATED ALWAYS AS (to_tsvector('indonesian',title||' '||summary||' '||text_content)) STORED
);
CREATE INDEX enrichment_due ON attachment_enrichment(next_due,attachment) WHERE state IN ('unconfigured','queued','leased');
CREATE INDEX enrichment_document ON attachment_enrichment USING gin(document);
CREATE INDEX enrichment_document_russian ON attachment_enrichment USING gin(document_russian);
CREATE INDEX enrichment_document_english ON attachment_enrichment USING gin(document_english);
CREATE INDEX enrichment_document_indonesian ON attachment_enrichment USING gin(document_indonesian);
CREATE FUNCTION public.enqueue_attachment_enrichment() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE rev integer; removed boolean;
BEGIN
 SELECT revision,deleted INTO STRICT rev,removed FROM public.messages WHERE id=NEW.message;
 INSERT INTO public.attachment_enrichment(attachment,source_digest,source_revision,state)
 VALUES(NEW.id,NEW.digest,rev,CASE WHEN NEW.active AND NOT removed THEN 'unconfigured' ELSE 'superseded' END)
 ON CONFLICT(attachment) DO UPDATE SET source_digest=excluded.source_digest,source_revision=excluded.source_revision,
 state=excluded.state,title='',summary='',text_content='',metadata='{}',truncated=false,error_code=NULL,
 attempts=0,lease_token=NULL,lease_until=NULL,profile_hash=NULL,indexed_at=NULL,updated_at=clock_timestamp();
 RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION public.enqueue_attachment_enrichment() FROM PUBLIC;
CREATE TRIGGER enqueue_attachment_enrichment AFTER INSERT OR UPDATE OF digest,filename,mime,bytes,active ON attachments
FOR EACH ROW EXECUTE FUNCTION public.enqueue_attachment_enrichment();
CREATE FUNCTION public.invalidate_message_enrichment() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
BEGIN
 UPDATE public.attachment_enrichment e SET source_revision=NEW.revision,
 state=CASE WHEN a.active AND NOT NEW.deleted THEN 'unconfigured' ELSE 'superseded' END,
 title='',summary='',text_content='',metadata='{}',truncated=false,error_code=NULL,
 attempts=0,lease_token=NULL,lease_until=NULL,profile_hash=NULL,indexed_at=NULL,updated_at=clock_timestamp()
 FROM public.attachments a WHERE a.message=NEW.id AND e.attachment=a.id;
 RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION public.invalidate_message_enrichment() FROM PUBLIC;
CREATE TRIGGER invalidate_message_enrichment AFTER UPDATE OF revision,deleted ON messages
FOR EACH ROW WHEN (OLD.revision IS DISTINCT FROM NEW.revision OR OLD.deleted IS DISTINCT FROM NEW.deleted)
EXECUTE FUNCTION public.invalidate_message_enrichment();
INSERT INTO attachment_enrichment(attachment,source_digest,source_revision,state)
SELECT a.id,a.digest,m.revision,CASE WHEN a.active AND NOT m.deleted THEN 'unconfigured' ELSE 'superseded' END
FROM attachments a JOIN messages m ON m.id=a.message;
-- Hard request-attempt ceiling; failed/ambiguous requests still consume budget.
CREATE TABLE enrichment_budget(day date PRIMARY KEY,attempts integer NOT NULL CHECK(attempts>=0));
ALTER TABLE audit_log DROP CONSTRAINT audit_log_operation_check;
ALTER TABLE audit_log ADD CONSTRAINT audit_log_operation_check CHECK(operation IN (
    'capabilities','list_chats','search','get_messages','get_context','get_updates',
    'get_attachment','download','ack_updates','issue_key','revoke_key','set_access','http_boundary',
    'record_review','get_reviews','search_people','search_files','get_file_text','get_file_occurrences','get_sync_status'));
