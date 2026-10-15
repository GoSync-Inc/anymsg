"""Shared safe vocabulary for collector and archive observations."""
from datetime import datetime,timezone

DETAILS={
    'version_conflict':('Conflicting provider observations; current archive revision is retained.','inspect_message_conflicts'),
    'coverage_not_reconciled':('Archived observations do not establish complete provider history.','get_sync_status_for_chat'),
    'whatsapp_catchup_gap':('Provider traversal did not reach a previous checkpoint. Live collection may still work.','inspect_chat_gaps'),
    'whatsapp_history_partial':('WhatsApp history remains a partial provider traversal.','get_sync_status_for_chat'),
    'telegram_history_partial':('Telegram history traversal is in progress or limited.','get_sync_status_for_chat'),
    'telegram_history':('Telegram is traversing archived history.','get_sync_status_for_chat'),
    'telegram_discovery':('Telegram chat catalogue discovery is in progress.','wait_for_discovery'),
    'rate_limited':('Provider requested a cooldown; do not bypass it.','wait_for_provider_cooldown'),
    'configured_chat_unavailable':('At least one configured chat was unavailable.','check_provider_access'),
    'provider_temporarily_unavailable':('A provider operation failed; collection will retry.','check_collector_health'),
    'dialog_discovery_limited':('Chat discovery reached its configured bound.','inspect_catalog_coverage'),
    'whatsapp_chat_unavailable':('Some WhatsApp history operations failed; live capture is independent.','inspect_chat_gaps'),
    'provider_disconnected':('The collector transport closed; bounded retry is scheduled.','wait_for_retry'),
    'provider_not_ready':('The provider has not established a usable connection.','wait_for_retry'),
    'provider_initialization_failed':('The provider failed during startup.','wait_for_retry'),
    'provider_connect_timeout':('Connection exceeded its bounded startup deadline.','wait_for_retry'),
    'provider_page_stalled':('A browser history operation stalled; the owned child is restarted.','wait_for_retry'),
    'whatsapp_logged_out':('WhatsApp disconnected this linked session. Automatic relinking is disabled.','relink_in_owner_terminal'),
    'whatsapp_pairing_required':('WhatsApp requires owner pairing; collector connection is blocked.','relink_in_owner_terminal'),
    'provider_auth_failed':('WhatsApp rejected the linked session; collector connection is blocked.','relink_in_owner_terminal'),
    'whatsapp_identity_mismatch':('The linked account differs from the configured identity.','check_configured_account_identity'),
    'media_timeout':('The media transfer exceeded its deadline; bounded retry is scheduled.','wait_for_media_retry'),
    'media_network_error':('The media CDN request failed; bounded retry is scheduled.','wait_for_media_retry'),
    'media_metadata_unavailable':('The provider did not supply usable media metadata.','wait_for_media_retry'),
    'media_unavailable':('The provider no longer exposes this original.','inspect_source_attachment'),
    'media_reference_changed':('The current provider attachment differs from the queued reference.','reread_message'),
    'invalid_media_path':('The provider media path failed the fixed-host validation.','check_collector_health'),
    'media_size_limit':('The original exceeds the configured download size bound.','inspect_download_limits'),
    'media_hash_mismatch':('Media integrity verification failed; no original was published.','wait_for_media_retry'),
    'media_size_mismatch':('The downloaded original size did not match its descriptor.','wait_for_media_retry'),
    'spool_full':('The durable capture queue reached its bound.','check_capture_backlog'),
    'disk_reserve':('The configured disk reserve prevents additional writes.','check_free_disk'),
    'storage_unavailable':('The local originals or media temporary directory could not be written (missing, permissions or read-only). Provider attempts are not consumed; downloads resume after the storage path is fixed.','check_media_storage_path'),
    'media_storage_unavailable':('The native media temporary directory could not be written. Provider attempts are not consumed.','check_media_storage_path'),
    'paused_storage':('Original download is deferred by local storage admission. Message capture and searchable metadata remain independent; provider attempts are not consumed.','check_media_storage_capacity'),
    'durable_capture_failed':('A callback could not be durably saved; reconnect reconciliation is required.','check_collector_health'),
}


def describe(code):
    explanation,action=DETAILS.get(code,('No specific diagnosis recorded.' if not code else
        'Provider or archive observation; inspect the scoped sync evidence.','get_sync_status_for_chat'))
    return {'explanation':explanation,'next_action':action}


def freshness(scanned,now=None):
    now=now or datetime.now(timezone.utc)
    age=max(0,(now-scanned).total_seconds()) if scanned is not None else None
    return {'state':'scan_unknown' if age is None else 'scan_stale' if age>300 else 'recent_scan',
        'last_successful_scan_at':scanned,'scan_age_seconds':round(age,1) if age is not None else None,
        'warning_after_seconds':300,'basis':'successful_scan_not_last_message_time',
        'notice':'An account scan does not prove every chat is current; inspect the specific chat.'}
