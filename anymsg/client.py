"""Bounded, credential-safe REST calls without automatic retries or redirects."""
from __future__ import annotations

import ipaddress
import json
import math
import re
from urllib.parse import urlsplit

import httpx


SAFE_ERROR_CODES = frozenset({
    'ambiguous_reference','attachment_unavailable','body_timeout','body_too_large',
    'chat_required','consumer_filter_mismatch','consumer_limit','cursor_changed',
    'disconnected','forbidden','invalid_body','invalid_consumer','invalid_cursor',
    'invalid_date_range','invalid_filter','invalid_fuzzy_query','invalid_host',
    'invalid_limit','invalid_origin','invalid_parameters','invalid_query',
    'invalid_receipt','invalid_utc_date','invalid_version','not_found',
    'query_timeout','rate_limited','service_busy','service_unavailable',
    'stale_ack','temporarily_unavailable','unauthorized','version_conflict',
    'invalid_message_text','invalid_request_id','idempotency_conflict','sending_disabled','sender_not_ready',
    'invalid_message_format','unsafe_message_markup','invalid_message_entities',
    'formatting_too_complex','unsupported_message_formatting',
    'lossy_formatting_requires_confirmation','message_too_long','preview_required','preview_mismatch',
    'unsupported_operation','invalid_operation_parameters','operation_disabled','operation_not_ready',
    'operation_target_changed','operation_access_changed','operation_result_unknown',
    'operation_expired','operation_rejected','invalid_operation_result',
})


_MESSENGER_FIELDS = {
    'list_messenger_operations': ({'account'}, {'account'}),
    'preview_messenger_operation': ({'account', 'operation'},
        {'account', 'operation', 'chat_id', 'message_id', 'expected_revision', 'parameters'}),
    'execute_messenger_operation': ({'account', 'operation', 'request_id'},
        {'account', 'operation', 'chat_id', 'message_id', 'expected_revision', 'parameters',
         'request_id', 'preview_digest'}),
    'get_messenger_operation': ({'operation_id'}, {'operation_id'}),
}
_OMITTED = object()


def _messenger_parameters(operation, parameters):
    """Reject malformed/unknown envelopes before transport; backend checks the catalog."""
    required, allowed = _MESSENGER_FIELDS[operation]
    if not isinstance(parameters, dict) or not required <= parameters.keys() or parameters.keys() - allowed:
        raise ValueError('invalid_parameters')
    for name, value in parameters.items():
        if name == 'expected_revision':
            if type(value) is not int or value < 0: raise ValueError('invalid_parameters')
        elif name == 'parameters':
            if not isinstance(value, dict) or any(type(key) is not str for key in value):
                raise ValueError('invalid_parameters')
        elif type(value) is not str:
            raise ValueError('invalid_parameters')
    return parameters


class AnyMsgError(RuntimeError):
    """Safe error metadata. Remote bodies, request parameters and tokens are omitted."""
    def __init__(self, code, *, status=None, request_id=None):
        self.code = code
        self.status = status
        self.request_id = request_id
        super().__init__(code)


def _base_url(value):
    if not isinstance(value, str) or not value or any(ord(c)<33 for c in value):
        raise ValueError("invalid_base_url")
    try:
        parsed = urlsplit(value)
        if parsed.port is not None and not 1 <= parsed.port <= 65535:
            raise ValueError
    except ValueError:
        raise ValueError("invalid_base_url") from None
    local = parsed.hostname == "localhost"
    if parsed.hostname:
        try: local = local or ipaddress.ip_address(parsed.hostname).is_loopback
        except ValueError: pass
    if (not parsed.hostname or parsed.username is not None or parsed.password is not None
            or parsed.query or parsed.fragment
            or parsed.scheme != "https" and not (parsed.scheme == "http" and local)):
        raise ValueError("https_or_loopback_required")
    return value.rstrip("/")


def _bad_constant(_):
    raise ValueError("invalid_json_constant")


class Client:
    """Call an AnyMsg REST root, e.g. https://archive.example/anymsg.

    The token is sent only in Authorization. Configure the REST root, not /mcp
    or /v1; each call appends /v1/<operation>. Reads never mark source messages
    read. send_message requires exact owner confirmation by the acting agent.
    Ambiguous writes are never automatically retried by this SDK.
    """
    def __init__(self, base_url, token, *, timeout=20, max_response_bytes=8*1024**2,
                 transport=None):
        self.base_url = _base_url(base_url)
        if (not isinstance(token,str) or not token or len(token)>8192
                or any(ord(c)<33 or ord(c)>126 for c in token)):
            raise ValueError("invalid_bearer_token")
        if (type(timeout) not in (int,float) or not math.isfinite(timeout) or not 0<timeout<=300
                or type(max_response_bytes) is not int or not 1024<=max_response_bytes<=32*1024**2):
            raise ValueError("invalid_client_limits")
        self.max_response_bytes = max_response_bytes
        self._http = httpx.Client(timeout=timeout, follow_redirects=False, trust_env=False,
            headers={"Authorization":"Bearer "+token,"Accept":"application/json","Accept-Encoding":"identity"},transport=transport)

    def __enter__(self):
        return self

    def __exit__(self,*_):
        self.close()

    def close(self):
        self._http.close()

    def call(self, operation, parameters=None):
        if not isinstance(operation,str) or not re.fullmatch("[a-z][a-z0-9_]{0,63}",operation):
            raise ValueError("invalid_operation")
        parameters = {} if parameters is None else parameters
        if not isinstance(parameters,dict) or "token" in parameters or any(not isinstance(k,str) for k in parameters):
            raise ValueError("invalid_parameters")
        if operation in _MESSENGER_FIELDS:
            _messenger_parameters(operation, parameters)
        try:
            body=json.dumps(parameters,ensure_ascii=False,allow_nan=False,separators=(",",":")).encode("utf-8")
        except (ValueError,TypeError,RecursionError):
            raise ValueError("invalid_parameters") from None
        if len(body)>16384:
            raise ValueError("request_too_large")
        try:
            with self._http.stream("POST",self.base_url+"/v1/"+operation,content=body,
                    headers={"Content-Type":"application/json"}) as response:
                if 300<=response.status_code<400:
                    raise AnyMsgError("redirect_rejected",status=response.status_code)
                if response.headers.get("content-encoding", "identity").lower() != "identity":
                    raise AnyMsgError("unsupported_content_encoding",status=response.status_code)
                data=bytearray()
                for chunk in response.iter_bytes(chunk_size=65536):
                    data.extend(chunk)
                    if len(data)>self.max_response_bytes:
                        raise AnyMsgError("response_too_large",status=response.status_code)
                try:
                    value=json.loads(data,parse_constant=_bad_constant)
                    if not isinstance(value,dict):raise ValueError
                except (ValueError,TypeError,RecursionError):
                    raise AnyMsgError("invalid_response",status=response.status_code) from None
                if not 200<=response.status_code<300:
                    code=value.get("error",value.get("code"))
                    if not isinstance(code,str) or code not in SAFE_ERROR_CODES:code="http_error"
                    request_id=value.get("request_id")
                    if not isinstance(request_id,str) or not re.fullmatch("[0-9a-f]{32}",request_id):request_id=None
                    raise AnyMsgError(code,status=response.status_code,request_id=request_id)
                return value
        except httpx.HTTPError:
            raise AnyMsgError("request_failed") from None

    def capabilities(self):
        return self.call("capabilities")

    def search(self, query, **filters):
        return self.call("search",{"query":query,**filters})

    def preview_message(self,*,account,chat_id,text,reply_to='',format='plain',
                        link_preview=False,allow_lossy=False):
        """Render a scoped draft without queuing or contacting a messenger.

        The digest binds content and policy; it is not owner approval. Review
        visible text, formatting, warnings and link destinations before a send.
        """
        return self.call('preview_message',dict(account=account,chat_id=chat_id,text=text,
            reply_to=reply_to,format=format,link_preview=link_preview,allow_lossy=allow_lossy))

    def send_message(self,*,account,chat_id,text,request_id,reply_to='',format='plain',
                     preview_digest='',link_preview=False,allow_lossy=False):
        """Submit exactly confirmed content; rich sends must match a reviewed preview.

        Keep request_id until the result is known. The digest never proves human
        approval; the acting agent obtains exact owner confirmation separately.
        """
        parameters=dict(account=account,chat_id=chat_id,text=text,
            request_id=request_id,reply_to=reply_to)
        # Default plain calls remain compatible with older strict servers.
        # Nondefault values pass unchanged so the server can reject bad types.
        if (format!='plain' or preview_digest!='' or link_preview is not False
                or allow_lossy is not False):
            parameters.update(format=format,preview_digest=preview_digest,
                link_preview=link_preview,allow_lossy=allow_lossy)
        return self.call('send_message',parameters)

    def get_send_status(self,send_id):
        return self.call('get_send_status',{'send_id':send_id})

    def list_messenger_operations(self, *, account):
        """Read this account's allowlisted operations and your independently scoped grants."""
        return self.call('list_messenger_operations', {'account': account})

    def preview_messenger_operation(self, *, account, operation, chat_id='', message_id='',
                                   expected_revision=0, parameters=_OMITTED):
        """Passively validate intent. A preview digest is not human approval."""
        return self.call('preview_messenger_operation', dict(account=account, operation=operation,
            chat_id=chat_id, message_id=message_id, expected_revision=expected_revision,
            parameters={} if parameters is _OMITTED else parameters))

    def execute_messenger_operation(self, *, account, operation, request_id, chat_id='',
                                   message_id='', expected_revision=0, parameters=_OMITTED,
                                   preview_digest=''):
        """Queue exact confirmed intent; poll the receipt and never retry ambiguous effects.

        Named live-read permissions are separate. For every mutation, the acting
        agent obtains exact owner confirmation; the digest only binds the payload.
        """
        return self.call('execute_messenger_operation', dict(account=account, operation=operation,
            chat_id=chat_id, message_id=message_id, expected_revision=expected_revision,
            parameters={} if parameters is _OMITTED else parameters,
            request_id=request_id, preview_digest=preview_digest))

    def get_messenger_operation(self, *, operation_id):
        """Read this key's scoped operation receipt without invoking the provider again."""
        return self.call('get_messenger_operation', {'operation_id': operation_id})
