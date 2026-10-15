from datetime import datetime, timezone
import asyncio

from .catalog import arguments, CATALOG
from .policy import Denied, key_from_file


class Dispatch:
    def __init__(self, policy, clients, tickets=None):
        self.policy, self.clients, self.tickets = policy, clients, tickets

    async def backend_capabilities(self, snapshot, agent, module):
        target = snapshot["deployment"]["backends"].get(module)
        if target != self.clients.deployment["backends"].get(module):
            raise Denied("backend_configuration_requires_restart", 503)
        binding = agent["credentials"][module]
        credential = key_from_file(binding["key_file"], binding["key_sha256"])
        try:
            async with asyncio.timeout(2):
                result = await self.clients.call(module, "capabilities", credential, {})
        except TimeoutError:
            raise Denied("backend_unavailable", 503) from None
        permissions = result.get("permissions")
        if not isinstance(permissions, list) or not all(isinstance(p, str) for p in permissions):
            raise Denied("backend_capability_contract_unavailable", 503)
        return result

    async def discover(self, key):
        snapshot, agent = self.policy.authenticate(key)
        visible = self.policy.visible(snapshot, agent)
        modules = sorted({CATALOG[name]["module"] for name in visible} - {"gateway"})

        async def one(module):
            try:
                caps = await self.backend_capabilities(snapshot, agent, module)
                return module, caps["permissions"], "ready"
            except Denied:
                return module, [], "unavailable"

        results = await asyncio.gather(*(one(module) for module in modules))
        scopes = {module: set(permissions) for module, permissions, _ in results}
        states = {module: state for module, _, state in results}
        # Recheck current local policy after discovery; a slow backend does not
        # let the old policy snapshot survive a revocation.
        current, principal = self.policy.authenticate(key)
        visible = [n for n in visible if n in self.policy.visible(current, principal)]
        visible = [
            n
            for n in visible
            if CATALOG[n]["module"] == "gateway"
            or (
                states.get(CATALOG[n]["module"]) == "ready"
                and set(CATALOG[n]["scopes"]) <= scopes[CATALOG[n]["module"]]
            )
        ]
        return current, principal, visible, states

    async def call(self, key, name, values):
        snapshot, agent, tool = self.policy.authorize(key, name)
        try:
            args = arguments(tool, values)
        except ValueError:
            raise Denied("invalid_arguments", 422) from None
        if tool["module"] == "gateway":
            snapshot, agent, visible, states = await self.discover(key)
            return {
                "service": "deedkit",
                "version": "0.1.0",
                "principal": agent["id"],
                "policy_revision": snapshot["revision"],
                "tools": visible,
                "modules": sorted({CATALOG[n]["module"] for n in visible} - {"gateway"}),
                "backend_states": states,
                "coverage": "query selected module capabilities; unavailable/stale sources remain unknown",
                "subscriptions": "existing backend identity; passive events do not ACK",
                "provider_mutations": False,
            }
        module = tool["module"]
        target = snapshot["deployment"]["backends"].get(module)
        if target != self.clients.deployment["backends"].get(module):
            raise Denied("backend_configuration_requires_restart", 503)
        binding = agent["credentials"][module]
        credential = key_from_file(binding["key_file"], binding["key_sha256"])
        caps = await self.backend_capabilities(snapshot, agent, module)
        if not set(tool["scopes"]) <= set(caps["permissions"]):
            raise Denied()
        started = datetime.now(timezone.utc).timestamp()
        # Admission is rechecked after all local work; a cached MCP session is
        # never authority. ACK is not retried if its network outcome is unknown.
        current, principal, _ = self.policy.authorize(key, name)
        if current["revision"] != snapshot["revision"] or principal["id"] != agent["id"]:
            raise Denied("policy_changed", 409)
        result = (
            caps
            if tool["operation"] == "capabilities"
            else await self.clients.call(module, tool["operation"], credential, args)
        )
        if tool["operation"] == "get_attachment" and result.get("download_url"):
            result = self.tickets.issue(
                result, key, agent, snapshot, credential, args["attachment_id"], started
            )
        if tool["operation"] == "capabilities":
            # Preserve scoped coverage/account data, while avoiding the upstream
            # catalog advertising methods unavailable through this connection.
            visible = [
                n
                for n in self.policy.visible(snapshot, agent)
                if CATALOG[n]["module"] == module and set(CATALOG[n]["scopes"]) <= set(caps["permissions"])
            ]
            result = dict(result)
            for field in (
                "tool_manifest",
                "messenger_operations",
                "available",
                "features",
                "key_permission_ceiling",
                "permissions",
            ):
                result.pop(field, None)
            result["gateway_tools"] = visible
            result["gateway_actions"] = []
            result["gateway_policy_revision"] = snapshot["revision"]
        return result
