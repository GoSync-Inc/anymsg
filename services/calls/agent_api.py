"""Principal-scoped control surface. Owner UI and decisions are never agent tools."""

from datetime import datetime, timezone
import hashlib
import hmac
import json
import os
from pathlib import Path
import stat

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from fastapi.responses import JSONResponse

from anymsg import AnyMsg
from core import BriefInput, ServiceError
from outbound import ExternalBrief


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Empty(Strict):
    pass


class Prepare(Strict):
    brief: BriefInput


class PrepareExternal(Strict):
    brief: ExternalBrief


class Brief(Strict):
    brief_id: str = Field(min_length=1, max_length=100)


class Dial(Brief):
    authorization_reference: str = Field(min_length=3, max_length=1500)


class Call(Strict):
    call_id: str = Field(min_length=1, max_length=100)


class Followup(Call):
    decision_id: str = Field(min_length=1, max_length=100)


class Page(Strict):
    limit: int = Field(default=100, ge=1, le=100)
    offset: int = Field(default=0, ge=0)


METHODS = {
    "capabilities": (Empty, ""),
    "prepare_call": (Prepare, "calls:prepare"),
    "prepare_external_call": (PrepareExternal, "calls:prepare"),
    "start_call": (Brief, "calls:start"),
    "preflight_call": (Brief, "calls:read"),
    "dial_call": (Dial, "calls:dial"),
    "get_call_status": (Call, "calls:read"),
    "get_call_result": (Call, "calls:read"),
    "get_call_analysis": (Call, "calls:read"),
    "get_owner_requests": (Page, "calls:read"),
    "prepare_followup": (Followup, "calls:prepare"),
    "stop_call": (Call, "calls:stop"),
}
SCOPES = {scope for _, scope in METHODS.values()} - {""}


class Principal(Strict):
    id: str = Field(min_length=1, max_length=128)
    key_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    enabled: bool
    expires_at: str
    scopes: list[str]
    channels: list[str] = Field(default_factory=list)
    destinations: dict[str, list[str]] = Field(default_factory=dict)
    archive_config: str | None = None


class RegistryDocument(Strict):
    version: int
    revision: int = Field(ge=1)
    principals: list[Principal]


def private_bytes(path):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(fd)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_mode & 0o077
            or info.st_size > 65536
        ):
            raise ValueError("unsafe_private_configuration")
        data = os.read(fd, 65537)
        if len(data) > 65536:
            raise ValueError("unsafe_private_configuration")
        after = os.fstat(fd)
        if (info.st_mtime_ns, info.st_ctime_ns, info.st_size) != (
            after.st_mtime_ns,
            after.st_ctime_ns,
            after.st_size,
        ):
            raise ValueError("changing_private_configuration")
        return data
    finally:
        os.close(fd)


class AgentRegistry:
    def __init__(self, path):
        self.path, self.revision, self.digest = path, 0, ""

    def authenticate(self, token):
        if not self.path:
            raise ServiceError("agent_access_not_configured", 401)
        try:
            raw = private_bytes(self.path)

            def unique(pairs):
                result = {}
                for key, value in pairs:
                    if key in result:
                        raise ValueError("duplicate_configuration_key")
                    result[key] = value
                return result

            doc = RegistryDocument.model_validate(
                json.loads(raw, object_pairs_hook=unique)
            )
            digest = hashlib.sha256(raw).hexdigest()
            if (
                doc.version != 1
                or doc.revision < self.revision
                or (doc.revision == self.revision and digest != self.digest)
            ):
                raise ValueError("invalid_policy_revision")
            ids, keys = set(), set()
            for p in doc.principals:
                expiry = datetime.fromisoformat(p.expires_at.replace("Z", "+00:00"))
                if (
                    p.id in ids
                    or p.key_sha256 in keys
                    or expiry.tzinfo is None
                    or set(p.scopes) - SCOPES
                ):
                    raise ValueError("invalid_principal")
                if set(p.channels) - {
                    "browser",
                    "phone",
                    "sip",
                    "telegram",
                    "whatsapp",
                }:
                    raise ValueError("invalid_channel")
                if set(p.destinations) - set(p.channels) or any(
                    "*" in d for d in p.destinations.values()
                ):
                    raise ValueError("invalid_destination")
                if p.archive_config and not Path(p.archive_config).is_absolute():
                    raise ValueError("invalid_archive_configuration")
                ids.add(p.id)
                keys.add(p.key_sha256)
            self.revision, self.digest = doc.revision, digest
        except (OSError, ValueError, ValidationError):
            raise ServiceError("agent_policy_unavailable", 503) from None
        hashed = hashlib.sha256(token.encode()).hexdigest()
        for principal in doc.principals:
            if hmac.compare_digest(principal.key_sha256, hashed):
                if principal.enabled and datetime.fromisoformat(
                    principal.expires_at.replace("Z", "+00:00")
                ) > datetime.now(timezone.utc):
                    return principal
        raise ServiceError("unauthorized", 401)


class AgentAPI:
    def __init__(self, service, registry, start, stop, outbound, archive_factory=None):
        self.service, self.registry = service, registry
        self.start, self.stop, self.outbound = start, stop, outbound
        self.archive_factory = archive_factory or self.archive

    @staticmethod
    def archive(principal):
        if not principal.archive_config:
            raise ServiceError("scoped_archive_not_configured", 403)
        config = json.loads(private_bytes(principal.archive_config))
        token = private_bytes(config["token_file"]).decode().strip()
        if not token or any(c.isspace() for c in token):
            raise ServiceError("scoped_archive_unavailable", 503)
        return AnyMsg(config["base_url"], token)

    def owned(self, principal, kind, identity):
        try:
            value = (
                self.service.get_brief(identity)
                if kind == "brief"
                else self.service.get_call(identity)
            )
        except ServiceError:
            raise ServiceError("not_found_or_forbidden", 403) from None
        brief = value if kind == "brief" else value["brief"]
        if brief.get("owner_principal") != principal.id:
            raise ServiceError("not_found_or_forbidden", 403)
        return value

    @staticmethod
    def route(principal, brief):
        if brief["channel"] not in principal.channels:
            raise ServiceError("not_found_or_forbidden", 403)
        if brief["channel"] != "browser" and brief[
            "destination"
        ] not in principal.destinations.get(brief["channel"], []):
            raise ServiceError("not_found_or_forbidden", 403)

    async def call(self, token, name, arguments):
        principal = self.registry.authenticate(token)
        if name not in METHODS:
            raise ServiceError("not_found_or_forbidden", 403)
        model, scope = METHODS[name]
        if scope and scope not in principal.scopes:
            raise ServiceError("not_found_or_forbidden", 403)
        try:
            args = model.model_validate(arguments)
        except ValidationError:
            raise ServiceError("invalid_arguments", 422) from None
        if name == "capabilities":
            return {
                "service": "deedkit-calls",
                "principal": principal.id,
                "permissions": principal.scopes,
                "channels": principal.channels,
                "scope": "owned_briefs_and_calls",
                "archive_configured": bool(principal.archive_config),
                "shared_learning": False,
                "owner_decision_write": False,
                "live_call_verification": "not_claimed",
            }
        if name in {"prepare_call", "prepare_external_call"}:
            selected = args.brief
            self.route(principal, {"channel": "browser", **selected.model_dump()})
            messages = []
            if selected.source_message_ids:
                if not principal.archive_config:
                    raise ServiceError("scoped_archive_not_configured", 403)
                archive = self.archive_factory(principal)
                try:
                    messages = [
                        await archive.message(identity)
                        for identity in dict.fromkeys(selected.source_message_ids)
                    ]
                except Exception:
                    raise ServiceError("scoped_archive_unavailable", 503) from None
                finally:
                    await archive.close()
            # The backend identity and route ceiling are rechecked after context IO.
            current = self.registry.authenticate(token)
            if current != principal:
                raise ServiceError("policy_changed", 409)
            return self.service.prepare(
                selected, messages, owner_principal=principal.id, use_learning=False
            )
        if name == "get_owner_requests":
            return {
                "items": self.service.decision_inbox(
                    args.limit, args.offset, owner_principal=principal.id
                ),
                "notification_channel": "not_connected",
                "external_action_authorized": False,
            }
        if isinstance(args, Brief):
            brief = self.owned(principal, "brief", args.brief_id)
            self.route(principal, brief)
            if name == "start_call":
                return await self.start(args.brief_id)
            if name == "preflight_call":
                return await self.outbound.preflight(args.brief_id)
            if name == "dial_call":

                def admission():
                    if self.registry.authenticate(token) != principal:
                        raise ServiceError("policy_changed", 409)

                return await self.outbound.dial(
                    args.brief_id, args.authorization_reference, admission=admission
                )
        call = self.owned(principal, "call", args.call_id)
        if name == "prepare_followup":
            self.route(principal, call["brief"])
            return self.service.prepare_followup(args.call_id, args.decision_id)
        if name == "stop_call":
            return await self.stop(args.call_id)
        if name == "get_call_analysis":
            return self.service.learning.report(args.call_id)
        if name == "get_call_status":
            return {
                key: call[key]
                for key in (
                    "id",
                    "status",
                    "outcome",
                    "answers",
                    "decisions",
                    "started_at",
                    "channel",
                    "end_reason",
                    "dial_state",
                    "provider_status",
                )
                if key in call
            }
        return call

    async def endpoint(self, operation, request):
        try:
            auth = request.headers.get("authorization", "")
            if len(
                request.headers.getlist("authorization")
            ) != 1 or not auth.startswith("Bearer "):
                raise ServiceError("unauthorized", 401)
            token = auth[7:]
            if not token or len(token) > 512 or any(c.isspace() for c in token):
                raise ServiceError("unauthorized", 401)
            self.registry.authenticate(token)
            data = bytearray()
            async for part in request.stream():
                data.extend(part)
                if len(data) > 16384:
                    raise ServiceError("body_too_large", 413)
            return JSONResponse(await self.call(token, operation, json.loads(data)))
        except ServiceError as error:
            return JSONResponse({"error": error.code}, status_code=error.status)
        except (ValueError, TypeError):
            return JSONResponse({"error": "invalid_arguments"}, status_code=422)
        except Exception:
            return JSONResponse({"error": "service_unavailable"}, status_code=503)
