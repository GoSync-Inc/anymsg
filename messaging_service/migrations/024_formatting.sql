-- Preserve existing plain rows without rewriting their source or request hashes.
ALTER TABLE outgoing_messages ADD COLUMN source_text text;
ALTER TABLE outgoing_messages ADD COLUMN source_format text NOT NULL DEFAULT 'plain'
    CHECK(source_format IN ('plain','markdown','html'));
ALTER TABLE outgoing_messages ADD COLUMN render_artifact jsonb
    CHECK(render_artifact IS NULL OR jsonb_typeof(render_artifact)='object');
ALTER TABLE outgoing_messages ADD COLUMN render_policy jsonb
    CHECK(render_policy IS NULL OR jsonb_typeof(render_policy)='object');
ALTER TABLE outgoing_messages ADD COLUMN preview_digest text
    CHECK(preview_digest IS NULL OR preview_digest ~ '^[a-f0-9]{64}$');
ALTER TABLE outgoing_messages ADD CONSTRAINT outgoing_render_columns CHECK (
    (source_text IS NULL AND source_format='plain' AND render_artifact IS NULL
     AND render_policy IS NULL AND preview_digest IS NULL)
    OR (source_text IS NOT NULL AND render_artifact IS NOT NULL AND render_policy IS NOT NULL
        AND (source_format='plain' OR preview_digest IS NOT NULL))
);
ALTER TABLE audit_log DROP CONSTRAINT audit_log_operation_check;
ALTER TABLE audit_log ADD CONSTRAINT audit_log_operation_check CHECK(operation IN (
 'capabilities','list_chats','search','get_messages','get_context','get_updates',
 'get_attachment','download','ack_updates','issue_key','revoke_key','set_access','http_boundary',
 'get_reviews','record_review','search_people','search_files','get_file_text',
 'get_file_occurrences','get_file_segments','get_sync_status','resolve_reference',
 'send_message','get_send_status','send_dispatch','set_sending','preview_message'));

-- Native formatting is independent evidence: learning/changing only that proof
-- must not repeat OCR, transcription or model work for the same current original.
-- Legacy hashes and stale derivatives are not proof of unchanged input. Fixed
-- trigger functions retain their existing privileges; callers gain no access to
-- derived text, model metadata, credentials or arbitrary SQL/function arguments.
CREATE OR REPLACE FUNCTION public.invalidate_message_attachment_text() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE retained text[] := '{}';
BEGIN
 IF OLD.revision IS NOT DISTINCT FROM NEW.revision AND OLD.deleted IS NOT DISTINCT FROM NEW.deleted THEN
  RETURN NEW;
 END IF;
 IF OLD.content_hash ~ '^[a-f0-9]{64}$' AND OLD.content_hash=NEW.content_hash AND NOT NEW.deleted
    AND ROW(OLD.body,OLD.deleted,OLD.sender,OLD.direction,OLD.account,OLD.chat,OLD.provider_id,OLD.sent_at)
        IS NOT DISTINCT FROM ROW(NEW.body,NEW.deleted,NEW.sender,NEW.direction,NEW.account,NEW.chat,NEW.provider_id,NEW.sent_at) THEN
  WITH rebound AS (
   UPDATE public.attachment_text f SET source_revision=NEW.revision
   FROM public.attachments a WHERE a.message=NEW.id AND f.attachment=a.id AND a.active
    AND f.source_revision=OLD.revision AND f.source_digest=a.digest
    AND f.source_filename=a.filename AND f.source_mime=a.mime AND f.source_bytes=a.bytes
    AND f.state IN ('ready','failed','unsupported') RETURNING f.attachment
  ) SELECT coalesce(array_agg(attachment),'{}') INTO retained FROM rebound;
 END IF;
 -- In-flight work belongs to the previous revision. Reset its lease and retry
 -- count, never promote a late result or refund recorded daily attempt budgets.
 UPDATE public.attachment_text f SET source_revision=NEW.revision,source_digest=a.digest,
    source_filename=a.filename,source_mime=a.mime,source_bytes=a.bytes,
    state=CASE WHEN a.active AND NOT NEW.deleted THEN 'queued' ELSE 'superseded' END,
    text_content='',truncated=false,extractor=NULL,error_code=NULL,
    attempts=0,next_due=clock_timestamp(),lease_token=NULL,lease_until=NULL,
    updated_at=clock_timestamp(),indexed_at=NULL
 FROM public.attachments a WHERE a.message=NEW.id AND f.attachment=a.id
    AND NOT(f.attachment=ANY(retained));
 RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION public.invalidate_message_attachment_text() FROM PUBLIC;

CREATE OR REPLACE FUNCTION public.invalidate_message_enrichment() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE retained text[] := '{}'; same_content boolean;
BEGIN
 IF OLD.revision IS NOT DISTINCT FROM NEW.revision AND OLD.deleted IS NOT DISTINCT FROM NEW.deleted THEN
  RETURN NEW;
 END IF;
 same_content := OLD.content_hash ~ '^[a-f0-9]{64}$' AND OLD.content_hash=NEW.content_hash AND NOT NEW.deleted
    AND ROW(OLD.body,OLD.deleted,OLD.sender,OLD.direction,OLD.account,OLD.chat,OLD.provider_id,OLD.sent_at)
        IS NOT DISTINCT FROM ROW(NEW.body,NEW.deleted,NEW.sender,NEW.direction,NEW.account,NEW.chat,NEW.provider_id,NEW.sent_at);
 IF same_content THEN
  WITH rebound AS (
   UPDATE public.attachment_enrichment e SET source_revision=NEW.revision
   FROM public.attachments a WHERE a.message=NEW.id AND e.attachment=a.id AND a.active
    AND e.source_revision=OLD.revision AND e.source_digest=a.digest
    AND e.state IN ('ready','failed','unsupported') RETURNING e.attachment
  ) SELECT coalesce(array_agg(attachment),'{}') INTO retained FROM rebound;
 END IF;
 UPDATE public.attachment_enrichment e SET source_revision=NEW.revision,source_digest=a.digest,
    state=CASE WHEN NOT a.active OR NEW.deleted THEN 'superseded'
        WHEN same_content AND e.state='leased' THEN 'queued' ELSE 'unconfigured' END,
    title='',summary='',text_content='',metadata='{}',truncated=false,error_code=NULL,
    attempts=0,lease_token=NULL,lease_until=NULL,profile_hash=NULL,indexed_at=NULL,
    next_due=clock_timestamp(),updated_at=clock_timestamp()
 FROM public.attachments a WHERE a.message=NEW.id AND e.attachment=a.id
    AND NOT(e.attachment=ANY(retained));
 RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION public.invalidate_message_enrichment() FROM PUBLIC;

-- A source-revision-only rebind of complete ready passages does not parse their
-- text again or regenerate stored tsvectors. Missing/stale passages or changed
-- indexed inputs still take the original bounded rebuild path.
CREATE OR REPLACE FUNCTION public.rebuild_attachment_text_chunks() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE expected integer;
BEGIN
 expected := greatest(length(NEW.text_content)-1001,0)/31000+1;
 IF TG_OP='UPDATE' AND OLD.state='ready' AND NEW.state='ready'
    AND NEW.source_revision IS DISTINCT FROM OLD.source_revision
    AND ROW(OLD.attachment,OLD.source_digest,OLD.source_filename,OLD.source_mime,OLD.source_bytes,OLD.display_name,OLD.text_content,
        OLD.processor_version,OLD.extraction_profile,OLD.extraction_metadata,OLD.extractor,OLD.truncated)
        IS NOT DISTINCT FROM ROW(NEW.attachment,NEW.source_digest,NEW.source_filename,NEW.source_mime,NEW.source_bytes,NEW.display_name,NEW.text_content,
        NEW.processor_version,NEW.extraction_profile,NEW.extraction_metadata,NEW.extractor,NEW.truncated)
    AND (SELECT count(*)=expected AND bool_and(source_digest=OLD.source_digest
         AND source_revision=OLD.source_revision AND chunk_index<expected
         AND start_offset=chunk_index*31000 AND end_offset=least(chunk_index*31000+32000,length(NEW.text_content))
         AND header=left(NEW.source_filename,1024)||' '||left(NEW.display_name,1024)
         AND text_content=substring(NEW.text_content FROM chunk_index*31000+1 FOR 32000))
         FROM public.attachment_text_chunks WHERE attachment=NEW.attachment) THEN
  UPDATE public.attachment_text_chunks SET source_revision=NEW.source_revision
   WHERE attachment=NEW.attachment AND source_revision=OLD.source_revision AND source_digest=OLD.source_digest;
  RETURN NEW;
 END IF;
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

CREATE OR REPLACE FUNCTION public.rebuild_attachment_enrichment_chunks() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE expected integer;
BEGIN
 expected := greatest(length(NEW.text_content)-1001,0)/31000+1;
 IF TG_OP='UPDATE' AND OLD.state='ready' AND NEW.state='ready'
    AND NEW.source_revision IS DISTINCT FROM OLD.source_revision
    AND ROW(OLD.attachment,OLD.source_digest,OLD.title,OLD.summary,OLD.text_content,OLD.metadata,
        OLD.profile_hash,OLD.kind,OLD.provider,OLD.model,OLD.truncated)
        IS NOT DISTINCT FROM ROW(NEW.attachment,NEW.source_digest,NEW.title,NEW.summary,NEW.text_content,NEW.metadata,
        NEW.profile_hash,NEW.kind,NEW.provider,NEW.model,NEW.truncated)
    AND (SELECT count(*)=expected AND bool_and(source_digest=OLD.source_digest
         AND source_revision=OLD.source_revision AND chunk_index<expected
         AND start_offset=chunk_index*31000 AND end_offset=least(chunk_index*31000+32000,length(NEW.text_content))
         AND header=NEW.title||' '||NEW.summary||' '||left(coalesce(NEW.metadata->>'document_type',''),80)
         AND text_content=substring(NEW.text_content FROM chunk_index*31000+1 FOR 32000))
         FROM public.attachment_enrichment_chunks WHERE attachment=NEW.attachment) THEN
  UPDATE public.attachment_enrichment_chunks SET source_revision=NEW.source_revision
   WHERE attachment=NEW.attachment AND source_revision=OLD.source_revision AND source_digest=OLD.source_digest;
  RETURN NEW;
 END IF;
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
