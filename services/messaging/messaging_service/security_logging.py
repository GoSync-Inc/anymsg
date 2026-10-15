"""Content-free diagnostics for standalone gateway and collector libraries."""
import logging

_COMPONENTS = {'mcp': 'mcp', 'uvicorn': 'http_server',
               'psycopg.pool': 'database_pool', 'httpx': 'http_client', 'httpcore': 'http_client',
               'telethon': 'telegram', 'personal_os.telegram.transport': 'telegram'}


def redact_library_logs():
    # A filter on the parent logger does not filter child records. A record
    # factory also covers loggers lazily created later by the SDK/transport.
    # Install before constructing third-party clients, also in owner pairing.
    previous = logging.getLogRecordFactory()
    if getattr(previous, '_messaging_redaction', False):
        return

    def factory(*args, **kwargs):
        record = previous(*args, **kwargs)
        component = next((label for prefix, label in _COMPONENTS.items()
                          if record.name == prefix or record.name.startswith(prefix + '.')), None)
        if component:
            exception = record.exc_info[0].__name__ if record.exc_info and record.exc_info[0] else 'none'
            # SDK warnings can interpolate an unregistered tool name or a
            # validation error before logging. Never retain that message, its
            # arguments, stack, or exception text in gateway diagnostics.
            record.name = 'personal_os_messaging.library.' + component
            record.msg = 'library_event exception_type=%s'
            record.args = (exception,)
            record.exc_info = record.exc_text = record.stack_info = None
        return record

    factory._messaging_redaction = True
    logging.setLogRecordFactory(factory)
