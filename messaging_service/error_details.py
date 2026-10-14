"""Safe error classification; no SQL, payloads, filenames or credentials."""
from uuid import uuid4

from psycopg import OperationalError
from psycopg.errors import QueryCanceled
from psycopg_pool import PoolTimeout


def classified(error):
    if isinstance(error,QueryCanceled):return 'query_timeout'
    if isinstance(error,PoolTimeout):return 'service_busy'
    if isinstance(error,OperationalError):return 'service_unavailable'
    return getattr(error,'code','temporarily_unavailable')


def payload(error,operation='',request_id=None):
    code=classified(error)
    retry=code in ('query_timeout','service_busy','service_unavailable','temporarily_unavailable')
    info={'error':code,'request_id':request_id or uuid4().hex,'retryable':retry}
    if hasattr(error,'fields'):info['fields']=error.fields
    if retry:info['retry_after_seconds']=2
    if operation in ('search','search_files'):
        info['next_action']='narrow_account_chat_or_date_filters'
        info['fallback']='get_messages_for_known_chat_then_get_context' if operation=='search' else 'get_file_text_for_known_attachment_then_get_context'
        info['results_state']='not_returned_due_to_error'
    elif code in ('not_found','forbidden'):
        info['next_action']='check_reference_and_permitted_scope'
    elif code=='cursor_changed':info['next_action']='restart_query_without_cursor'
    else:info['next_action']='retry_after_delay' if retry else 'check_parameters'
    return info
