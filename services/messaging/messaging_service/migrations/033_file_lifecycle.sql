-- Original residency is separate from immutable occurrence and derivative identity.
-- Existing originals receive a conservative full retention grace period.
CREATE TABLE attachment_residency (
 digest text PRIMARY KEY CHECK(digest ~ '^[a-f0-9]{64}$'),
 bytes bigint NOT NULL CHECK(bytes>=0),
 state text NOT NULL DEFAULT 'resident' CHECK(state IN ('resident','evicting','evicted')),
 last_accessed_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 pinned boolean NOT NULL DEFAULT false,
 eviction_id uuid,
 evicted_at timestamptz,
 updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 CHECK((state='resident' AND eviction_id IS NULL AND evicted_at IS NULL) OR
       (state='evicting' AND eviction_id IS NOT NULL AND evicted_at IS NULL) OR
       (state='evicted' AND eviction_id IS NOT NULL AND evicted_at IS NOT NULL))
);
CREATE TABLE attachment_eviction_journal (
 id uuid PRIMARY KEY,
 digest text NOT NULL REFERENCES attachment_residency(digest),
 bytes bigint NOT NULL CHECK(bytes>=0),
 root_device bigint NOT NULL CHECK(root_device>=0),
 root_inode bigint NOT NULL CHECK(root_inode>=0),
 state text NOT NULL DEFAULT 'prepared' CHECK(state IN ('prepared','completed','cancelled')),
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 completed_at timestamptz,
 error_code text
);
ALTER TABLE attachment_residency ADD CONSTRAINT residency_eviction_id
 FOREIGN KEY(eviction_id) REFERENCES attachment_eviction_journal(id);
CREATE UNIQUE INDEX one_pending_attachment_eviction ON attachment_eviction_journal(digest)
 WHERE state='prepared';
CREATE INDEX attachment_residency_due ON attachment_residency(last_accessed_at,digest)
 WHERE state='resident' AND NOT pinned;
-- No lease expiry: a captured reference backup must survive sender process death.
CREATE TABLE attachment_backup_fences (
 id text PRIMARY KEY CHECK(id ~ '^[a-zA-Z0-9_.:-]{1,160}$'),
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 ended_at timestamptz
);
INSERT INTO attachment_residency(digest,bytes)
 SELECT digest,min(bytes) FROM attachments GROUP BY digest HAVING min(bytes)=max(bytes);

CREATE FUNCTION public.touch_original(blob_digest text,expected_bytes bigint DEFAULT NULL)
 RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE changed boolean;
BEGIN
 PERFORM pg_advisory_xact_lock(hashtextextended('attachment-residency-v1',0));
 IF blob_digest !~ '^[a-f0-9]{64}$' OR expected_bytes<0 THEN
  RAISE EXCEPTION 'invalid_blob';
 END IF;
 UPDATE public.attachment_residency SET last_accessed_at=clock_timestamp(),updated_at=clock_timestamp()
 WHERE digest=blob_digest AND (expected_bytes IS NULL OR bytes=expected_bytes);
 changed=FOUND;
 RETURN changed;
END $$;
REVOKE ALL ON FUNCTION public.touch_original(text,bigint) FROM PUBLIC;

CREATE FUNCTION public.begin_backup_fence(fence_id text) RETURNS void
 LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
BEGIN
 PERFORM pg_advisory_xact_lock(hashtextextended('attachment-residency-v1',0));
 IF fence_id IS NULL OR fence_id !~ '^[a-zA-Z0-9_.:-]{1,160}$' THEN
  RAISE EXCEPTION 'invalid_backup_fence';
 END IF;
 IF EXISTS(SELECT FROM public.attachment_residency WHERE state='evicting') THEN
  RAISE EXCEPTION 'backup_eviction_pending';
 END IF;
 IF EXISTS(SELECT FROM public.attachment_backup_fences WHERE id=fence_id AND ended_at IS NOT NULL) THEN
  RAISE EXCEPTION 'backup_fence_closed';
 END IF;
 INSERT INTO public.attachment_backup_fences(id) VALUES(fence_id) ON CONFLICT DO NOTHING;
END $$;
CREATE FUNCTION public.end_backup_fence(fence_id text) RETURNS void
 LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
BEGIN
 PERFORM pg_advisory_xact_lock(hashtextextended('attachment-residency-v1',0));
 UPDATE public.attachment_backup_fences SET ended_at=coalesce(ended_at,clock_timestamp()) WHERE id=fence_id;
 IF NOT FOUND THEN RAISE EXCEPTION 'unknown_backup_fence'; END IF;
END $$;
REVOKE ALL ON FUNCTION public.begin_backup_fence(text),public.end_backup_fence(text) FROM PUBLIC;

CREATE FUNCTION public.register_original_residency() RETURNS trigger
 LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
BEGIN
 PERFORM pg_advisory_xact_lock(hashtextextended('attachment-residency-v1',0));
 IF EXISTS(SELECT FROM public.attachment_residency WHERE digest=NEW.digest AND bytes<>NEW.bytes) THEN
  RAISE EXCEPTION 'blob_identity_mismatch';
 END IF;
 INSERT INTO public.attachment_residency(digest,bytes) VALUES(NEW.digest,NEW.bytes)
 ON CONFLICT(digest) DO UPDATE SET last_accessed_at=clock_timestamp(),updated_at=clock_timestamp();
 PERFORM public.register_resident_original(NEW.digest,NEW.bytes);
 RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION public.register_original_residency() FROM PUBLIC;
CREATE TRIGGER register_original_residency AFTER INSERT OR UPDATE OF digest,bytes ON attachments
 FOR EACH ROW EXECUTE FUNCTION public.register_original_residency();

-- Callable only by a verified-original publisher. It cannot change CAS identity.
CREATE FUNCTION public.register_resident_original(blob_digest text,expected_bytes bigint)
 RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
BEGIN
 PERFORM pg_advisory_xact_lock(hashtextextended('attachment-residency-v1',0));
 IF NOT EXISTS(SELECT FROM public.attachments WHERE digest=blob_digest AND bytes=expected_bytes) THEN
  RAISE EXCEPTION 'unknown_original';
 END IF;
 UPDATE public.attachment_eviction_journal SET state='cancelled',completed_at=clock_timestamp(),
  error_code='original_republished' WHERE digest=blob_digest AND state='prepared';
 UPDATE public.attachment_residency SET state='resident',eviction_id=NULL,evicted_at=NULL,
  last_accessed_at=clock_timestamp(),updated_at=clock_timestamp()
 WHERE digest=blob_digest AND bytes=expected_bytes;
 RETURN FOUND;
END $$;
REVOKE ALL ON FUNCTION public.register_resident_original(text,bigint) FROM PUBLIC;

-- Only successful previous receipts with a persisted managed eviction qualify.
-- Demand admission happens first; attempts, backoff and descriptors are untouched.
CREATE FUNCTION public.queue_managed_original(attachment_id text) RETURNS boolean
 LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
BEGIN
 PERFORM pg_advisory_xact_lock(hashtextextended('attachment-residency-v1',0));
 UPDATE public.media_jobs j SET state='queued'
 FROM public.attachments a,public.messages m,public.attachment_residency r,
      public.attachment_eviction_journal e
 WHERE j.id=attachment_id AND j.id=a.id AND a.message=m.id AND a.active AND NOT m.deleted
  AND j.state='ready' AND j.attempts<5 AND j.requested_until>clock_timestamp()
  AND j.request_key_digest IS NOT NULL AND a.digest=r.digest AND a.bytes=r.bytes
  AND r.state='evicted' AND e.id=r.eviction_id AND e.state='completed'
  AND e.digest=r.digest AND e.bytes=r.bytes;
 RETURN FOUND;
END $$;
REVOKE ALL ON FUNCTION public.queue_managed_original(text) FROM PUBLIC;

-- The lifecycle role can lock only the bounded registered sources of one digest.
-- It receives no UPDATE capability over archive IDs or derivative identities.
CREATE FUNCTION public.lock_lifecycle_sources(blob_digest text) RETURNS boolean
 LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE source_ids text[]; message_ids text[]; source_accounts text[]; item text;
BEGIN
 PERFORM pg_advisory_xact_lock(hashtextextended('attachment-residency-v1',0));
 PERFORM pg_advisory_xact_lock(74509139);
 SELECT array_agg(id),array_agg(message),array_agg(account) INTO source_ids,message_ids,source_accounts
 FROM (SELECT a.id,a.message,m.account FROM public.attachments a
       JOIN public.messages m ON m.id=a.message WHERE a.digest=blob_digest ORDER BY a.id LIMIT 1001) bounded;
 IF coalesce(array_length(source_ids,1),0)>1000 THEN RETURN false; END IF;
 FOR item IN SELECT DISTINCT value FROM unnest(source_accounts) AS value ORDER BY value LOOP
  PERFORM pg_advisory_xact_lock(hashtextextended('archive:'||item,0));
 END LOOP;
 PERFORM id FROM public.messages WHERE id=ANY(message_ids) ORDER BY id FOR UPDATE;
 PERFORM id FROM public.attachments WHERE id=ANY(source_ids) ORDER BY id FOR UPDATE;
 PERFORM attachment FROM public.attachment_text WHERE attachment=ANY(source_ids) ORDER BY attachment FOR UPDATE;
 PERFORM attachment FROM public.attachment_enrichment WHERE attachment=ANY(source_ids) ORDER BY attachment FOR UPDATE;
 PERFORM attachment FROM public.attachment_understanding WHERE attachment=ANY(source_ids) ORDER BY attachment FOR UPDATE;
 PERFORM id FROM public.media_jobs WHERE id=ANY(source_ids) ORDER BY id FOR UPDATE;
 RETURN true;
END $$;
REVOKE ALL ON FUNCTION public.lock_lifecycle_sources(text) FROM PUBLIC;
