"""Bounded operational diagnostics; never provider payloads or credentials."""
from datetime import datetime, timezone
from .status_details import describe

TERMINAL = {
    'whatsapp_logged_out':'relink_in_owner_terminal',
    'whatsapp_pairing_required':'relink_in_owner_terminal',
    'provider_auth_failed':'relink_in_owner_terminal',
    'whatsapp_identity_mismatch':'check_configured_account_identity',
    'telegram_pairing_required':'relink_in_owner_terminal',
    'telegram_identity_mismatch':'check_configured_account_identity',
}
CONNECTION_ERRORS = set(TERMINAL) | {'provider_disconnected','provider_not_ready',
    'provider_initialization_failed','provider_connect_timeout','provider_page_stalled',
    'provider_protocol_error','durable_capture_failed','capture_overload','spool_full',
    'disk_reserve','spool_corrupt','collector_lease_lost'}
MEDIA_ERRORS = {'media_unavailable','media_reference_changed','unsupported_media_type',
    'media_hash_mismatch','media_size_mismatch','media_size_limit','media_metadata_unavailable',
    'invalid_media_path','media_timeout','media_network_error'}
PHASE_STATES = {
    'connection':{'connecting','connected','retry_wait','blocked','stopped'},
    'live_capture':{'starting','receiving','backlogged','paused','stopped'},
    'history':{'scanning','waiting','error','stopped'},
}
PHASE_REASONS = CONNECTION_ERRORS | {'provider_ready','capture_ready','capture_draining',
    'collector_shutdown','history_scan','history_scan_finished','provider_temporarily_unavailable',
    'collector_starting','operator_resumed'}


def operations(row, history, files, now=None):
    now=now or datetime.now(timezone.utc)
    phases=dict(row.get('operations') or {})
    lease=row.get('collector_lease_until')
    running=lease is not None and lease>now
    for key in ('connection','live_capture'):
        phase=dict(phases.get(key) or {'state':'unknown','reason_code':'not_checked',
            'changed_at':None,'observed_at':None,'next_action':'check_collector_health'})
        phase['collector_running']=running
        phase['detail']=describe(phase['reason_code'])
        # Retain the actual failure and retry/action evidence even when stopped.
        if not running and phase['state'] not in ('blocked','retry_wait','unknown'):
            phase['reported_state']=phase['state'];phase['state']='not_running'
        phases[key]=phase
    gaps=history.get('gap_chats',0)
    phase=dict(phases.get('history') or {})
    phase.update(state='partial_with_gaps' if gaps else
        'partial_with_errors' if row.get('detail_code') in ('configured_chat_unavailable',
            'whatsapp_chat_unavailable','provider_temporarily_unavailable') else
        'traversing' if history.get('catchup_pending_chats') or
            history.get('configured_window',{}).get('in_progress') or
            history.get('unbounded_history',{}).get('in_progress') else 'partial',
        full_history_verified=False, collector_running=running)
    phases['history']=phase
    counts=files['download'];errors=files.get('download_errors',{})
    diagnostics=[]
    error_states=files.get('download_error_states',{})
    for code,count in sorted(errors.items()):
        states=error_states.get(code)
        if states:
            diagnostics.extend({'reason_code':code,'count':number,'job_state':state,
                **describe(code,job_state=state)} for state,number in sorted(states.items()))
        else:diagnostics.append({'reason_code':code,'count':count,**describe(code)})
    storage_paused=error_states.get('paused_storage',{}).get('queued',errors.get('paused_storage',0))
    retry_queued=sum(states.get('queued',0) for code,states in error_states.items()
        if code not in ('paused_storage','retry_exhausted'))
    phases['media']={'state':'processing' if counts['leased'] else
        'paused_storage' if errors.get('paused_storage') else 'needs_attention' if counts['failed'] else
        'queued' if counts['queued'] else 'idle',
        'counts':counts,'errors':errors,'diagnostics':diagnostics,'independent_of_connection':True,
        'storage_paused_jobs':storage_paused,'storage_paused':bool(storage_paused),
        'terminal_failed_jobs':counts['failed'],'needs_attention':bool(counts['failed']),
        'retry_queued_jobs':retry_queued}
    return phases
