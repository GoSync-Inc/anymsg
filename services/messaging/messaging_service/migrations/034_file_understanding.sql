-- Search descriptions are independent from retained ASR and extracted detail.
-- No model, provider session or account is enabled by this migration.
CREATE TABLE attachment_understanding (
 attachment text PRIMARY KEY REFERENCES attachments(id) ON DELETE CASCADE,
 source_digest text NOT NULL CHECK(source_digest ~ '^[0-9a-f]{64}$'),
 source_revision integer NOT NULL CHECK(source_revision>0),
 source_artifact_hash text CHECK(source_artifact_hash ~ '^[0-9a-f]{64}$'),
 kind text NOT NULL CHECK(kind IN ('audio','document','image','video')),
 state text NOT NULL DEFAULT 'not_configured' CHECK(state IN
  ('not_configured','queued','leased','ready','failed','superseded','unsupported')),
 profile_hash text CHECK(profile_hash ~ '^[0-9a-f]{64}$'),
 provider text, model text,
 published_profile_hash text CHECK(published_profile_hash ~ '^[0-9a-f]{64}$'),
 published_provider text, published_model text,
 title text NOT NULL DEFAULT '' CHECK(length(title)<=300),
 summary text NOT NULL DEFAULT '' CHECK(length(summary)<=4000),
 text_content text NOT NULL DEFAULT '' CHECK(length(text_content)<=1000000),
 coverage jsonb NOT NULL DEFAULT '{}' CHECK(jsonb_typeof(coverage)='object' AND octet_length(coverage::text)<=16384),
 truncated boolean NOT NULL DEFAULT false,
 attempts integer NOT NULL DEFAULT 0 CHECK(attempts BETWEEN 0 AND 3),
 error_code text,
 lease_token uuid, lease_until timestamptz,
 next_due timestamptz NOT NULL DEFAULT clock_timestamp(),
 indexed_at timestamptz, updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 document tsvector GENERATED ALWAYS AS (to_tsvector('simple',title||' '||summary)) STORED,
 document_russian tsvector GENERATED ALWAYS AS (to_tsvector('russian',title||' '||summary)) STORED,
 document_english tsvector GENERATED ALWAYS AS (to_tsvector('english',title||' '||summary)) STORED,
 document_indonesian tsvector GENERATED ALWAYS AS (to_tsvector('indonesian',title||' '||summary)) STORED,
 CHECK(state<>'ready' OR (length(btrim(title))>0 AND length(btrim(summary))>0 AND source_artifact_hash IS NOT NULL))
);
CREATE INDEX attachment_understanding_document ON attachment_understanding USING gin(document);
CREATE INDEX attachment_understanding_document_ru ON attachment_understanding USING gin(document_russian);
CREATE INDEX attachment_understanding_document_en ON attachment_understanding USING gin(document_english);
CREATE INDEX attachment_understanding_document_id ON attachment_understanding USING gin(document_indonesian);
CREATE INDEX attachment_understanding_due ON attachment_understanding(next_due,attachment)
 WHERE state IN ('not_configured','queued','leased','failed');

CREATE TABLE file_understanding_profiles (
 account text NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
 kind text NOT NULL CHECK(kind IN ('audio','document','image','video')),
 profile_hash text NOT NULL CHECK(profile_hash ~ '^[0-9a-f]{64}$'),
 provider text NOT NULL CHECK(provider ~ '^[a-z_]{1,64}$'),
 model text NOT NULL CHECK(length(model)<=128),
 enabled boolean NOT NULL DEFAULT true,
 pause_reason text, paused_until timestamptz,
 observed_at timestamptz NOT NULL DEFAULT clock_timestamp(), expires_at timestamptz NOT NULL,
 PRIMARY KEY(account,kind)
);

ALTER TABLE processing_budget DROP CONSTRAINT processing_budget_kind_check;
ALTER TABLE processing_budget ADD CHECK(kind IN ('audio','image','document','video'));
ALTER TABLE processing_control DROP CONSTRAINT processing_control_kind_check;
ALTER TABLE processing_control ADD CHECK(kind IN ('audio','image','document','video'));
ALTER TABLE processing_resource_leases DROP CONSTRAINT processing_resource_leases_worker_kind_check;
ALTER TABLE processing_resource_leases ADD CHECK(worker_kind IN ('enrichment','extraction','understanding'));

CREATE FUNCTION public.enqueue_attachment_understanding() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE rev integer; removed boolean;
BEGIN
 SELECT revision,deleted INTO STRICT rev,removed FROM public.messages WHERE id=NEW.message;
 INSERT INTO public.attachment_understanding(attachment,source_digest,source_revision,kind,state)
 VALUES(NEW.id,NEW.digest,rev,CASE WHEN NEW.mime LIKE 'audio/%' OR NEW.mime='application/ogg' THEN 'audio'
  WHEN NEW.mime LIKE 'image/%' THEN 'image' WHEN NEW.mime LIKE 'video/%' THEN 'video' ELSE 'document' END,
  CASE WHEN NEW.active AND NOT removed THEN 'not_configured' ELSE 'superseded' END)
 ON CONFLICT(attachment) DO UPDATE SET source_digest=excluded.source_digest,
  source_revision=excluded.source_revision,kind=excluded.kind,state=excluded.state,
  source_artifact_hash=NULL,title='',summary='',text_content='',coverage='{}',truncated=false,
  published_profile_hash=NULL,published_provider=NULL,published_model=NULL,
  attempts=0,error_code=NULL,lease_token=NULL,lease_until=NULL,profile_hash=NULL,indexed_at=NULL,
  next_due=clock_timestamp(),updated_at=clock_timestamp();
 RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION public.enqueue_attachment_understanding() FROM PUBLIC;
CREATE TRIGGER enqueue_attachment_understanding AFTER INSERT OR UPDATE OF digest,filename,mime,bytes,active
 ON attachments FOR EACH ROW EXECUTE FUNCTION public.enqueue_attachment_understanding();

-- Caption/formatting revisions change the occurrence fence, not CAS content.
-- Archive reconciles media IDs later in this SAME transaction: changed media
-- becomes inactive and attachment invalidators reset every derived stage.
-- Sourcecurrent unleased rows retain exact result/retry metadata; old leases
-- are cancelled without refunding attempts, profile identity, backoff or budget.
-- Stale proof instead resets from the CURRENT attachment identity, as in 024.
CREATE OR REPLACE FUNCTION public.reset_file_extraction_metadata() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog AS $$
BEGIN
 IF NEW.state='queued' AND
  (OLD.state IS DISTINCT FROM NEW.state OR OLD.source_digest IS DISTINCT FROM NEW.source_digest
   OR OLD.source_revision IS DISTINCT FROM NEW.source_revision
   OR OLD.source_filename IS DISTINCT FROM NEW.source_filename
   OR OLD.source_mime IS DISTINCT FROM NEW.source_mime) THEN
  IF NEW.source_revision IS DISTINCT FROM OLD.source_revision AND
   ROW(OLD.source_digest,OLD.source_filename,OLD.source_mime,OLD.source_bytes,OLD.text_content,
    OLD.extraction_metadata,OLD.display_name,OLD.truncated,OLD.extractor,OLD.extraction_profile,OLD.processor_version)
   IS NOT DISTINCT FROM ROW(NEW.source_digest,NEW.source_filename,NEW.source_mime,NEW.source_bytes,NEW.text_content,
    NEW.extraction_metadata,NEW.display_name,NEW.truncated,NEW.extractor,NEW.extraction_profile,NEW.processor_version) THEN
   RETURN NEW;
  END IF;
  NEW.extraction_metadata='{}'::jsonb;
  NEW.display_name='';
 END IF;
 RETURN NEW;
END $$;

CREATE OR REPLACE FUNCTION public.invalidate_message_attachment_text() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
BEGIN
 IF OLD.revision IS NOT DISTINCT FROM NEW.revision AND OLD.deleted IS NOT DISTINCT FROM NEW.deleted THEN RETURN NEW; END IF;
 WITH sources AS (
  SELECT f.attachment,a.active,a.digest,a.filename,a.mime,a.bytes,
   a.active AND NOT OLD.deleted AND NOT NEW.deleted AND f.source_revision=OLD.revision
   AND f.source_digest=a.digest AND f.source_filename=a.filename AND f.source_mime=a.mime AND f.source_bytes=a.bytes
   AND ROW(OLD.account,OLD.chat,OLD.provider_id,OLD.direction,OLD.sent_at) IS NOT DISTINCT FROM ROW(NEW.account,NEW.chat,NEW.provider_id,NEW.direction,NEW.sent_at) AS same_source
  FROM public.attachment_text f JOIN public.attachments a ON a.id=f.attachment WHERE a.message=NEW.id
 )
 UPDATE public.attachment_text f SET source_revision=NEW.revision,source_digest=s.digest,
  source_filename=s.filename,source_mime=s.mime,source_bytes=s.bytes,
  state=CASE WHEN s.same_source THEN CASE WHEN f.state='leased' THEN 'queued' ELSE f.state END
   WHEN s.active AND NOT NEW.deleted THEN 'queued' ELSE 'superseded' END,
  text_content=CASE WHEN s.same_source THEN f.text_content ELSE '' END,
  extraction_metadata=CASE WHEN s.same_source THEN f.extraction_metadata ELSE '{}'::jsonb END,
  display_name=CASE WHEN s.same_source THEN f.display_name ELSE '' END,
  truncated=CASE WHEN s.same_source THEN f.truncated ELSE false END,
  extractor=CASE WHEN s.same_source THEN f.extractor ELSE NULL END,
  indexed_at=CASE WHEN s.same_source THEN f.indexed_at ELSE NULL END,
  attempts=CASE WHEN s.same_source THEN f.attempts ELSE 0 END,
  error_code=CASE WHEN s.same_source THEN f.error_code ELSE NULL END,
  next_due=CASE WHEN s.same_source THEN f.next_due ELSE clock_timestamp() END,
  updated_at=CASE WHEN s.same_source AND f.state<>'leased' THEN f.updated_at ELSE clock_timestamp() END,
  lease_token=NULL,lease_until=NULL
 FROM sources s WHERE f.attachment=s.attachment;
 RETURN NEW;
END $$;

CREATE OR REPLACE FUNCTION public.invalidate_message_enrichment() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
BEGIN
 IF OLD.revision IS NOT DISTINCT FROM NEW.revision AND OLD.deleted IS NOT DISTINCT FROM NEW.deleted THEN RETURN NEW; END IF;
 WITH sources AS (
  SELECT e.attachment,a.active,a.digest,a.filename,a.mime,a.bytes,
   a.active AND NOT OLD.deleted AND NOT NEW.deleted AND e.source_revision=OLD.revision
   AND e.source_digest=a.digest
   AND ROW(OLD.account,OLD.chat,OLD.provider_id,OLD.direction,OLD.sent_at) IS NOT DISTINCT FROM ROW(NEW.account,NEW.chat,NEW.provider_id,NEW.direction,NEW.sent_at) AS same_source
  FROM public.attachment_enrichment e JOIN public.attachments a ON a.id=e.attachment WHERE a.message=NEW.id
 )
 UPDATE public.attachment_enrichment e SET source_revision=NEW.revision,source_digest=s.digest,
  state=CASE WHEN s.same_source THEN CASE WHEN e.state='leased' THEN
   CASE WHEN e.attempts>=3 THEN 'failed' ELSE 'queued' END ELSE e.state END
   WHEN s.active AND NOT NEW.deleted THEN 'unconfigured' ELSE 'superseded' END,
  title=CASE WHEN s.same_source THEN e.title ELSE '' END,
  summary=CASE WHEN s.same_source THEN e.summary ELSE '' END,
  text_content=CASE WHEN s.same_source THEN e.text_content ELSE '' END,
  metadata=CASE WHEN s.same_source THEN e.metadata ELSE '{}'::jsonb END,
  truncated=CASE WHEN s.same_source THEN e.truncated ELSE false END,
  profile_hash=CASE WHEN s.same_source THEN e.profile_hash ELSE NULL END,
  indexed_at=CASE WHEN s.same_source THEN e.indexed_at ELSE NULL END,
  attempts=CASE WHEN s.same_source THEN e.attempts ELSE 0 END,
  error_code=CASE WHEN s.same_source THEN CASE WHEN e.state='leased' AND e.attempts>=3
   THEN 'retry_exhausted' ELSE e.error_code END ELSE NULL END,
  next_due=CASE WHEN s.same_source THEN e.next_due ELSE clock_timestamp() END,
  updated_at=CASE WHEN s.same_source AND e.state<>'leased' THEN e.updated_at ELSE clock_timestamp() END,
  lease_token=NULL,lease_until=NULL
 FROM sources s WHERE e.attachment=s.attachment;
 RETURN NEW;
END $$;

CREATE FUNCTION public.invalidate_message_understanding() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
BEGIN
 IF OLD.revision IS NOT DISTINCT FROM NEW.revision AND OLD.deleted IS NOT DISTINCT FROM NEW.deleted THEN RETURN NEW; END IF;
 WITH sources AS (
  SELECT u.attachment,a.active,a.digest,a.filename,a.mime,a.bytes,
   a.active AND NOT OLD.deleted AND NOT NEW.deleted AND u.source_revision=OLD.revision
   AND u.source_digest=a.digest
   AND ROW(OLD.account,OLD.chat,OLD.provider_id,OLD.direction,OLD.sent_at) IS NOT DISTINCT FROM ROW(NEW.account,NEW.chat,NEW.provider_id,NEW.direction,NEW.sent_at) AS same_source
  FROM public.attachment_understanding u JOIN public.attachments a ON a.id=u.attachment WHERE a.message=NEW.id
 )
 UPDATE public.attachment_understanding u SET source_revision=NEW.revision,source_digest=s.digest,
  state=CASE WHEN s.same_source THEN CASE WHEN u.state='leased' THEN
   CASE WHEN u.attempts>=3 THEN 'failed' ELSE 'queued' END ELSE u.state END
   WHEN s.active AND NOT NEW.deleted THEN 'not_configured' ELSE 'superseded' END,
  source_artifact_hash=CASE WHEN s.same_source THEN u.source_artifact_hash ELSE NULL END,
  title=CASE WHEN s.same_source THEN u.title ELSE '' END,
  summary=CASE WHEN s.same_source THEN u.summary ELSE '' END,
  text_content=CASE WHEN s.same_source THEN u.text_content ELSE '' END,
  coverage=CASE WHEN s.same_source THEN u.coverage ELSE '{}'::jsonb END,
  truncated=CASE WHEN s.same_source THEN u.truncated ELSE false END,
  published_profile_hash=CASE WHEN s.same_source THEN u.published_profile_hash ELSE NULL END,
  published_provider=CASE WHEN s.same_source THEN u.published_provider ELSE NULL END,
  published_model=CASE WHEN s.same_source THEN u.published_model ELSE NULL END,
  profile_hash=CASE WHEN s.same_source THEN u.profile_hash ELSE NULL END,
  provider=CASE WHEN s.same_source THEN u.provider ELSE NULL END,
  model=CASE WHEN s.same_source THEN u.model ELSE NULL END,
  indexed_at=CASE WHEN s.same_source THEN u.indexed_at ELSE NULL END,
  attempts=CASE WHEN s.same_source THEN u.attempts ELSE 0 END,
  error_code=CASE WHEN s.same_source THEN CASE WHEN u.state='leased' AND u.attempts>=3
   THEN 'retry_exhausted' ELSE u.error_code END ELSE NULL END,
  next_due=CASE WHEN s.same_source THEN u.next_due ELSE clock_timestamp() END,
  updated_at=CASE WHEN s.same_source AND u.state<>'leased' THEN u.updated_at ELSE clock_timestamp() END,
  lease_token=NULL,lease_until=NULL
 FROM sources s WHERE u.attachment=s.attachment;
 RETURN NEW;
END $$;

REVOKE ALL ON FUNCTION public.invalidate_message_understanding() FROM PUBLIC;
CREATE TRIGGER invalidate_message_understanding AFTER UPDATE OF revision,deleted ON messages
 FOR EACH ROW WHEN(OLD.revision IS DISTINCT FROM NEW.revision OR OLD.deleted IS DISTINCT FROM NEW.deleted)
 EXECUTE FUNCTION public.invalidate_message_understanding();

-- ASR or extraction may change without a new message revision. Invalidate in
-- that same write transaction so old metadata cannot leak through search.
CREATE FUNCTION public.invalidate_understanding_detail() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE detail_changed boolean:=true; image_changed boolean:=true;
BEGIN
 IF TG_OP='UPDATE' THEN
  IF TG_TABLE_NAME='attachment_text' THEN
   detail_changed=ROW(OLD.source_digest,OLD.text_content,OLD.extraction_metadata,OLD.truncated)
    IS DISTINCT FROM ROW(NEW.source_digest,NEW.text_content,NEW.extraction_metadata,NEW.truncated);
   image_changed=false;
  ELSE
   detail_changed=ROW(OLD.source_digest,OLD.text_content,OLD.metadata,OLD.truncated)
    IS DISTINCT FROM ROW(NEW.source_digest,NEW.text_content,NEW.metadata,NEW.truncated);
   image_changed=detail_changed OR ROW(OLD.title,OLD.summary) IS DISTINCT FROM ROW(NEW.title,NEW.summary);
  END IF;
 END IF;
 UPDATE public.attachment_understanding u SET state=CASE WHEN a.active AND NOT m.deleted THEN 'not_configured' ELSE 'superseded' END,
  source_artifact_hash=NULL,title='',summary='',text_content='',coverage='{}',truncated=false,
  published_profile_hash=NULL,published_provider=NULL,published_model=NULL,
  attempts=0,error_code=NULL,lease_token=NULL,lease_until=NULL,profile_hash=NULL,indexed_at=NULL,
  next_due=clock_timestamp(),updated_at=clock_timestamp()
 FROM public.attachments a,public.messages m WHERE u.attachment=NEW.attachment AND a.id=u.attachment AND m.id=a.message
 AND ((TG_TABLE_NAME='attachment_text' AND u.kind='document' AND detail_changed)
   OR (TG_TABLE_NAME='attachment_enrichment' AND u.kind='audio' AND detail_changed)
   OR (TG_TABLE_NAME='attachment_enrichment' AND u.kind='image' AND image_changed));
 RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION public.invalidate_understanding_detail() FROM PUBLIC;
CREATE TRIGGER invalidate_understanding_text AFTER INSERT OR UPDATE OF state,text_content,source_digest,source_revision,extraction_metadata,truncated
 ON attachment_text FOR EACH ROW EXECUTE FUNCTION public.invalidate_understanding_detail();
CREATE TRIGGER invalidate_understanding_enrichment AFTER INSERT OR UPDATE OF state,text_content,source_digest,source_revision,metadata,truncated,title,summary,profile_hash
 ON attachment_enrichment FOR EACH ROW EXECUTE FUNCTION public.invalidate_understanding_detail();

INSERT INTO attachment_understanding(attachment,source_digest,source_revision,kind,state)
 SELECT a.id,a.digest,m.revision,CASE WHEN a.mime LIKE 'audio/%' OR a.mime='application/ogg' THEN 'audio'
  WHEN a.mime LIKE 'image/%' THEN 'image' WHEN a.mime LIKE 'video/%' THEN 'video' ELSE 'document' END,
  CASE WHEN a.active AND NOT m.deleted THEN 'not_configured' ELSE 'superseded' END
 FROM attachments a JOIN messages m ON m.id=a.message;

DO $$ BEGIN
 IF EXISTS(SELECT FROM pg_roles WHERE rolname='messaging_gateway') THEN
  GRANT SELECT ON attachment_understanding,file_understanding_profiles TO messaging_gateway;
 END IF;
END $$;
