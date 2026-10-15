-- Derived, bounded text from immutable originals. Collection and text extraction
-- remain separate. Parent-message ACLs are applied by every gateway lookup.
CREATE TABLE attachment_text (
    attachment text PRIMARY KEY REFERENCES attachments(id) ON DELETE CASCADE,
    source_digest text NOT NULL CHECK(source_digest ~ '^[a-f0-9]{64}$'),
    source_revision integer NOT NULL CHECK(source_revision > 0),
    source_filename text NOT NULL,
    source_mime text NOT NULL,
    source_bytes bigint NOT NULL CHECK(source_bytes >= 0),
    state text NOT NULL DEFAULT 'queued'
        CHECK(state IN ('queued','leased','ready','unsupported','failed','superseded')),
    text_content text NOT NULL DEFAULT '' CHECK(length(text_content) <= 100000),
    document tsvector GENERATED ALWAYS AS
        (to_tsvector('simple', left(source_filename,1024) || ' ' || text_content)) STORED,
    truncated boolean NOT NULL DEFAULT false,
    extractor text,
    error_code text,
    attempts integer NOT NULL DEFAULT 0 CHECK(attempts >= 0),
    next_due timestamptz NOT NULL DEFAULT clock_timestamp(),
    lease_token uuid,
    lease_until timestamptz,
    updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    indexed_at timestamptz
);
CREATE INDEX attachment_text_document ON attachment_text USING gin(document);
CREATE INDEX attachment_text_due ON attachment_text(next_due,attachment)
    WHERE state IN ('queued','leased');

-- Only these fixed triggers may enqueue under the collector role. There are no
-- caller-supplied identifiers, SQL strings, paths or arguments. The trusted
-- migration owner owns these functions; ordinary roles must not own them.
CREATE FUNCTION public.enqueue_attachment_text() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE rev integer; removed boolean;
BEGIN
    SELECT m.revision,m.deleted INTO STRICT rev,removed
        FROM public.messages m WHERE m.id=NEW.message;
    INSERT INTO public.attachment_text(attachment,source_digest,source_revision,
        source_filename,source_mime,source_bytes,state)
        VALUES(NEW.id,NEW.digest,rev,NEW.filename,NEW.mime,NEW.bytes,
            CASE WHEN NEW.active AND NOT removed THEN 'queued' ELSE 'superseded' END)
    ON CONFLICT(attachment) DO UPDATE SET source_digest=excluded.source_digest,
        source_revision=excluded.source_revision,source_filename=excluded.source_filename,
        source_mime=excluded.source_mime,source_bytes=excluded.source_bytes,
        state=excluded.state,text_content='',truncated=false,extractor=NULL,error_code=NULL,
        attempts=0,next_due=clock_timestamp(),lease_token=NULL,lease_until=NULL,
        updated_at=clock_timestamp(),indexed_at=NULL;
    RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION public.enqueue_attachment_text() FROM PUBLIC;
CREATE TRIGGER enqueue_attachment_text AFTER INSERT OR UPDATE OF
    digest,filename,mime,bytes,active ON attachments
    FOR EACH ROW EXECUTE FUNCTION public.enqueue_attachment_text();

CREATE FUNCTION public.invalidate_message_attachment_text() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
BEGIN
    IF OLD.revision IS DISTINCT FROM NEW.revision OR OLD.deleted IS DISTINCT FROM NEW.deleted THEN
        UPDATE public.attachment_text f SET source_revision=NEW.revision,
            state=CASE WHEN a.active AND NOT NEW.deleted THEN 'queued' ELSE 'superseded' END,
            text_content='',truncated=false,extractor=NULL,error_code=NULL,
            attempts=0,next_due=clock_timestamp(),lease_token=NULL,lease_until=NULL,
            updated_at=clock_timestamp(),indexed_at=NULL
            FROM public.attachments a WHERE a.message=NEW.id AND f.attachment=a.id;
    END IF;
    RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION public.invalidate_message_attachment_text() FROM PUBLIC;
CREATE TRIGGER invalidate_message_attachment_text AFTER UPDATE OF revision,deleted ON messages
    FOR EACH ROW EXECUTE FUNCTION public.invalidate_message_attachment_text();

INSERT INTO attachment_text(attachment,source_digest,source_revision,source_filename,
    source_mime,source_bytes,state)
    SELECT a.id,a.digest,m.revision,a.filename,a.mime,a.bytes,
        CASE WHEN a.active AND NOT m.deleted THEN 'queued' ELSE 'superseded' END
    FROM attachments a JOIN messages m ON m.id=a.message;
