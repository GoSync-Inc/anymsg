"""Separate authenticated control MCP for rehearsals and explicitly authorized calls."""
import secrets
import os
import stat
from pathlib import Path
from typing import Any

from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations

from core import BriefInput, ServiceError
from outbound import ExternalBrief
from learning import SemanticReview


def control_key(directory):
    """Same file rules as owner.token: no links, private mode, owned by this process."""
    path=Path(directory)/'control.token'
    try:
        fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    except FileExistsError:
        pass
    else:
        with os.fdopen(fd,'w') as output:
            output.write(secrets.token_urlsafe(32));output.flush();os.fsync(output.fileno())
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
    with os.fdopen(fd,'rb') as stream:
        info=os.fstat(stream.fileno())
        if (not stat.S_ISREG(info.st_mode) or info.st_nlink!=1 or info.st_uid!=os.geteuid() or
                stat.S_IMODE(info.st_mode)!=0o600 or info.st_size>256):
            raise ValueError('Unsafe control credential file')
        token=stream.read(257).decode('ascii').strip()
    if not 20<=len(token)<=200 or any(c.isspace() for c in token):
        raise ValueError('Invalid control credential')
    return token


def create_control(service,prepare,start,stop,capabilities,outbound=None,browser_url='http://127.0.0.1:13842/'):
    mcp=MCPServer('Voice Tasks',version='0.2.0',instructions=(
        'Prepare scoped browser rehearsals and external call tasks. Read capabilities and preflight before dialing. '
        'An external call requires the human owner\'s concrete instruction identifying account, recipient and task. '
        'Include the exact instruction reference; tool access and a prepared brief are not authorization. '
        'Calling is restricted to operator-enabled channels/recipients. Never retry an unknown provider outcome or use a fallback channel. '
        'The human must join the local browser and grant microphone access. Results are observations requiring owner review. '
        'Owner attention is asynchronous. Inspect durable requests and prepare a followup only after an actual owner answer. '
        'A prepared external followup still requires authorization for a fresh call. '
        'Supervision supplies advice, never owner approval. This MCP has no owner-decision write capability.'))
    read=ToolAnnotations(readOnlyHint=True,destructiveHint=False,openWorldHint=False)
    write=ToolAnnotations(readOnlyHint=False,destructiveHint=False,openWorldHint=False)

    @mcp.tool(structured_output=True,annotations=read)
    async def get_voice_capabilities()->dict[str,Any]:
        """Inspect model, archive access, channel configuration and actual verification coverage."""
        return await capabilities()

    @mcp.tool(structured_output=True,annotations=write)
    async def prepare_call(brief:BriefInput)->dict[str,Any]:
        """Save a browser task with explicitly selected message IDs. No audio or external call starts."""
        try:
            return await prepare(brief)
        except Exception:
            raise ToolError('Cannot prepare the selected task or archive context.') from None

    @mcp.tool(structured_output=True,annotations=ToolAnnotations(readOnlyHint=False,destructiveHint=False,idempotentHint=True,openWorldHint=False))
    async def start_call(brief_id:str)->dict[str,Any]:
        """Create one browser rehearsal for this exact snapshot. Human joins at the local UI; no dialing."""
        try:
            v=await start(brief_id)
            return {**v,'browser_url':browser_url,'requires_browser_join':True}
        except ServiceError as error:
            raise ToolError(error.code) from None

    @mcp.tool(structured_output=True,annotations=read)
    async def get_call_status(call_id:str)->dict[str,Any]:
        """Read lifecycle, collected answers, and pending owner decisions."""
        try:
            v=service.get_call(call_id)
            return {k:v[k] for k in ('id','status','outcome','answers','decisions','started_at','channel','end_reason','dial_state','provider_call_id','provider_status') if k in v}
        except ServiceError as error:
            raise ToolError(error.code) from None

    @mcp.tool(structured_output=True,annotations=read)
    async def get_owner_requests(limit:int=100,offset:int=0)->dict[str,Any]:
        """Read durable questions after calls end; no owner watching or external notification is required."""
        if limit<1 or limit>100 or offset<0:
            raise ToolError('invalid_page')
        items=service.decision_inbox(limit,offset)
        return {'items':items,'next_offset':offset+limit if len(items)==limit else None,
                'notification_channel':'not_connected','external_action_authorized':False}

    @mcp.tool(structured_output=True,annotations=ToolAnnotations(readOnlyHint=False,destructiveHint=False,idempotentHint=True,openWorldHint=False))
    async def prepare_followup(call_id:str,decision_id:str)->dict[str,Any]:
        """Freeze a followup on the original channel with actual owner answer and exact conditions. External callbacks need fresh authorization; no dialing occurs."""
        try:
            return service.prepare_followup(call_id,decision_id)
        except ServiceError as error:
            raise ToolError(error.code) from None

    @mcp.tool(structured_output=True,annotations=ToolAnnotations(readOnlyHint=False,destructiveHint=False,idempotentHint=True,openWorldHint=False))
    async def stop_call(call_id:str)->dict[str,Any]:
        """Idempotently stop the specified rehearsal or external call and release audio resources."""
        try:
            return await stop(call_id)
        except ServiceError as error:
            raise ToolError(error.code) from None

    @mcp.tool(structured_output=True,annotations=read)
    async def get_call_result(call_id:str)->dict[str,Any]:
        """Read saved snapshot, transcripts, tool events, observations, timings, and owner review."""
        try:
            return service.get_call(call_id)
        except ServiceError as error:
            raise ToolError(error.code) from None

    @mcp.tool(structured_output=True,annotations=read)
    async def get_call_analysis(call_id:str)->dict[str,Any]:
        """Read after-call findings, evidence IDs, analyzer coverage, and proposed style improvement."""
        try:
            return service.learning.report(call_id)
        except ServiceError as error:
            raise ToolError(error.code) from None

    @mcp.tool(structured_output=True,annotations=read)
    async def get_voice_learning()->dict[str,Any]:
        """Read private regression cases, prompt proposals and their actual comparison runs. No activation or external actions."""
        active=service.learning.active_candidate()
        return {'active_candidate':active,'cases':service.learning.cases(),
                'candidates':service.learning.candidates(),'evaluations':service.learning.evaluations()}

    @mcp.tool(structured_output=True,annotations=write)
    async def submit_call_analysis(call_id:str,analysis:SemanticReview)->dict[str,Any]:
        """Save the controlling agent's after-call analysis with exact evidence IDs. Creates draft cases/proposals, never activates them or edits code. Provenance is control_mcp_agent, separate from automatic Gemini analysis."""
        try:
            return service.learning.submit_agent_review(call_id,analysis.model_dump())
        except ServiceError as error:
            raise ToolError(error.code) from None

    if outbound is not None:
        @mcp.tool(structured_output=True,annotations=write)
        async def prepare_external_call(brief:ExternalBrief)->dict[str,Any]:
            """Freeze an outbound Telegram, WhatsApp, phone or SIP task with exact account/destination and selected AnyMsg facts. No dialing occurs."""
            try:return await prepare(brief)
            except Exception:raise ToolError('Cannot prepare selected external task.') from None

        @mcp.tool(structured_output=True,annotations=read)
        async def preflight_call(brief_id:str)->dict[str,Any]:
            """Check enabled route, recipient allowlist, model key and provider configuration. Does not dial. Configured is not live-call verification."""
            try:return await outbound.preflight(brief_id)
            except ServiceError as exc:raise ToolError(exc.code) from None

        @mcp.tool(structured_output=True,annotations=ToolAnnotations(readOnlyHint=False,destructiveHint=False,idempotentHint=True,openWorldHint=True))
        async def dial_call(brief_id:str,authorization_reference:str)->dict[str,Any]:
            """Place ONE real, potentially paid call for this brief, only after the owner's concrete instruction. Repeats return the same attempt; unknown is NEVER retried. A callback needs a fresh authorized brief."""
            try:return await outbound.dial(brief_id,authorization_reference)
            except ServiceError as exc:raise ToolError(exc.code) from None

    return mcp.streamable_http_app(stateless_http=True,json_response=True,max_request_body_size=16384,
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=True,
            allowed_hosts=['127.0.0.1:*','localhost:*','testserver'],
            allowed_origins=['http://127.0.0.1:*','http://localhost:*','http://testserver']))
