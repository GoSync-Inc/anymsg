"""Bounded, credential-safe REST calls without automatic retries or redirects."""
from __future__ import annotations

import ipaddress
import json
import math
import re
from datetime import datetime
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
    'invalid_subscription','subscription_limit','subscription_conflict','subscription_closed',
    'subscription_access_changed','invalid_subscription_state',
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
_SUBSCRIPTION_FIELDS = {
    'create_subscription': ({'chat_ids'}, {'chat_ids','sender_ids','kinds','direction','origins','label','expires_at'}),
    'list_subscriptions': (set(), {'limit'}),
    'get_subscription': ({'subscription_id'}, {'subscription_id'}),
    'update_subscription': ({'subscription_id','version','action'},
        {'subscription_id','version','action','resume_policy'}),
    'get_subscription_events': ({'subscription_id'}, {'subscription_id','after','limit'}),
}
_SUBSCRIPTION_KINDS = frozenset({'message.created','message.edited','message.deleted','message.status',
    'attachment.ready','file.text.ready','file.transcript.ready','file.description.ready','file.processing.failed'})
_SUBSCRIPTION_ORIGINS = frozenset({'live','backfill','reconcile','fixture'})


def _subscription_reference(value):
    return isinstance(value,str) and bool(re.fullmatch(r'[^\x00-\x20\x7f]{1,256}',value))


def _subscription_parameters(operation, parameters):
    """Strict structural envelope checks; authorization and lifecycle stay on the server."""
    required,allowed=_SUBSCRIPTION_FIELDS[operation]
    if not isinstance(parameters,dict) or not required<=parameters.keys() or parameters.keys()-allowed:
        raise ValueError('invalid_parameters')
    for name,value in parameters.items():
        valid=True
        if name in ('chat_ids','sender_ids','kinds','origins'):
            if value is None and name!='chat_ids':continue
            maximum=9 if name=='kinds' else 4 if name=='origins' else 64
            valid=isinstance(value,list) and 1<=len(value)<=maximum
            if valid:
                valid=all(_subscription_reference(item) if name in ('chat_ids','sender_ids')
                    else isinstance(item,str) and item in (_SUBSCRIPTION_KINDS if name=='kinds'
                        else _SUBSCRIPTION_ORIGINS) for item in value)
        elif name in ('limit','version','after'):
            minimum,maximum={'limit':(1,100),'version':(1,9007199254740991),'after':(0,9007199254740991)}[name]
            valid=type(value) is int and minimum<=value<=maximum
        elif name=='subscription_id':valid=_subscription_reference(value)
        elif name=='label':valid=isinstance(value,str) and len(value)<=128 and not re.search(r'[\x00-\x1f\x7f]',value)
        elif name in ('action','direction','resume_policy'):
            valid=isinstance(value,str) and value in {
                'action':('pause','resume','close'), 'direction':('','incoming','outgoing'),
                'resume_policy':('catch_up','skip')}[name]
        elif name=='expires_at':
            if value is None:continue
            valid=isinstance(value,str) and 20<=len(value)<=64 and bool(re.fullmatch(
                r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})',value))
            if valid:
                try:
                    parsed=datetime.fromisoformat(value.replace('Z','+00:00'))
                    valid=parsed.tzinfo is not None and parsed.utcoffset() is not None
                except ValueError:valid=False
        if not valid:raise ValueError('invalid_parameters')


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
        if operation in _SUBSCRIPTION_FIELDS:
            _subscription_parameters(operation,parameters)
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

    def create_subscription(self, *, chat_ids, sender_ids=None, kinds=None,
                            direction='incoming', origins=None, label='', expires_at=None):
        """Persist explicit archived sources from the committed boundary; no push or provider action.

        Requires read and explicit subscription-write. A timeout can leave creation
        uncertain: inspect list_subscriptions before retrying. Names are not sender IDs.
        """
        return self.call('create_subscription',dict(chat_ids=chat_ids,sender_ids=sender_ids,
            kinds=kinds,direction=direction,origins=origins,label=label,expires_at=expires_at))

    def list_subscriptions(self, *, limit=50):
        """Read only this originating credential's definitions, up to 100."""
        return self.call('list_subscriptions',{'limit':limit})

    def get_subscription(self, *, subscription_id):
        """Read lifecycle and archive matcher frontier; this does not prove provider freshness."""
        return self.call('get_subscription',{'subscription_id':subscription_id})

    def update_subscription(self, *, subscription_id, version, action, resume_policy='catch_up'):
        """Version-checked pause/resume/close. After uncertainty, read before retrying."""
        return self.call('update_subscription',dict(subscription_id=subscription_id,version=version,
            action=action,resume_policy=resume_policy))

    def get_subscription_events(self, *, subscription_id, after=0, limit=50):
        """Inspect durable matched references; never ACK, wake an agent or mark provider read."""
        return self.call('get_subscription_events',dict(subscription_id=subscription_id,after=after,limit=limit))
