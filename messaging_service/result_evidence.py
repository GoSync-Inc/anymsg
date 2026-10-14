"""Consistent, bounded message/file evidence from already authorized rows."""
from .file_index import file_response_fields


def decorate(c,rows):
    if not rows:return rows
    ids=list({r['id'] for r in rows})
    files=c.execute('''SELECT j.message,j.id AS attachment_id,a.id,a.filename,a.mime,a.bytes,a.digest,
        j.state AS job_state,j.error_code AS download_error_code,
        coalesce(a.filename,j.descriptor->>'filename') AS source_name,
        coalesce(f.state,'unknown') AS extraction_state,CASE WHEN coalesce(e.state,'unconfigured')='unconfigured' AND EXISTS(SELECT FROM processing_profiles pp WHERE pp.account=m.account AND pp.enabled AND pp.kind=CASE WHEN a.mime LIKE 'audio/%%' OR a.mime='application/ogg' THEN 'audio' WHEN a.mime LIKE 'image/%%' THEN 'image' ELSE 'document' END) THEN 'queued' ELSE coalesce(e.state,'unconfigured') END AS enrichment_state
        FROM media_jobs j JOIN messages m ON m.id=j.message
        LEFT JOIN attachments a ON a.id=j.id AND a.active
        LEFT JOIN attachment_text f ON f.attachment=a.id AND f.source_digest=a.digest
            AND f.source_revision=m.revision AND f.source_filename=a.filename AND f.source_mime=a.mime
            AND f.source_bytes=a.bytes AND f.state<>'superseded'
        LEFT JOIN attachment_enrichment e ON e.attachment=a.id AND e.source_digest=a.digest
            AND e.source_revision=m.revision AND e.state<>'superseded'
        WHERE j.message=ANY(%s) AND j.state<>'superseded' AND NOT m.deleted ORDER BY j.id''',(ids,)).fetchall()
    # Synthetic fixtures and restored legacy originals may predate media jobs.
    files+=c.execute('''SELECT a.message,a.id AS attachment_id,a.id,a.filename,a.mime,a.bytes,a.digest,
        'ready' AS job_state,NULL AS download_error_code,a.filename AS source_name,
        coalesce(f.state,'unknown') AS extraction_state,CASE WHEN coalesce(e.state,'unconfigured')='unconfigured' AND EXISTS(SELECT FROM processing_profiles pp WHERE pp.account=m.account AND pp.enabled AND pp.kind=CASE WHEN a.mime LIKE 'audio/%%' OR a.mime='application/ogg' THEN 'audio' WHEN a.mime LIKE 'image/%%' THEN 'image' ELSE 'document' END) THEN 'queued' ELSE coalesce(e.state,'unconfigured') END AS enrichment_state
        FROM attachments a JOIN messages m ON m.id=a.message
        LEFT JOIN attachment_text f ON f.attachment=a.id AND f.source_digest=a.digest
            AND f.source_revision=m.revision AND f.source_filename=a.filename AND f.source_mime=a.mime
            AND f.source_bytes=a.bytes AND f.state<>'superseded'
        LEFT JOIN attachment_enrichment e ON e.attachment=a.id AND e.source_digest=a.digest
            AND e.source_revision=m.revision AND e.state<>'superseded'
        WHERE a.message=ANY(%s) AND a.active AND NOT m.deleted
        AND NOT EXISTS(SELECT 1 FROM media_jobs j WHERE j.id=a.id) ORDER BY a.id''',(ids,)).fetchall()
    grouped={}
    for file in files:
        downloaded=file['id'] is not None
        state='ready' if downloaded else {'leased':'processing','ready':'unavailable'}.get(file['job_state'],file['job_state'])
        item=file_response_fields({'id':file['attachment_id'],'attachment_id':file['attachment_id'],
            'filename':file['source_name'],'mime':file['mime'],'bytes':file['bytes'],
            'sha256':file['digest'],'digest':file['digest'],'error_code':file['download_error_code']},
            download_status=state,extraction_status=file['extraction_state'] if downloaded else 'awaiting_download',
            enrichment_status={'leased':'processing','unconfigured':'not_configured'}.get(file['enrichment_state'],file['enrichment_state'])
                if downloaded else 'awaiting_download')
        # Legacy pending state preserved for existing clients.
        item['state']=file['job_state'];grouped.setdefault(file['message'],[]).append(item)
    conflicts={r['message']:r for r in c.execute('''SELECT message,
        count(*) FILTER(WHERE resolved_at IS NULL) AS active_conflicts,
        count(*) FILTER(WHERE resolved_at IS NOT NULL) AS historical_conflicts,
        max(observed_at) AS last_conflict_at FROM message_conflicts
        WHERE message=ANY(%s) GROUP BY message''',(ids,))}
    current={r['id']:r['revision'] for r in c.execute('SELECT id,revision FROM messages WHERE id=ANY(%s)',(ids,))}
    for row in rows:
        row['attachments']=grouped.get(row['id'],[])
        conflict=conflicts.get(row['id'],{'active_conflicts':0,'historical_conflicts':0,'last_conflict_at':None})
        row['evidence']={k:v for k,v in conflict.items() if k!='message'}
        changed=current.get(row['id'])!=row['revision']
        if changed:row['attachments']=[]
        row['evidence'].update(archive_revision=row['revision'],current_revision_at_evidence_read=current.get(row['id']),
            revision_changed_during_read=changed,provider_currentness_verified=False,
            next_action='reread_message' if changed else
                'inspect_chat_coverage_and_request_reconciliation' if conflict['active_conflicts'] else 'read_context')
    return rows
