"""Linux NetworkManager adapter. Only OMI's recorded VLAN UUID is removed."""
from __future__ import annotations

import asyncio
import ipaddress
import os
import re
import time
import uuid
from typing import Awaitable, Callable

from .models import NetworkProfile
from .store import Store


async def run_command(*arguments: str, timeout: float = 40) -> str:
    process = await asyncio.create_subprocess_exec(
        *arguments, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        env={**os.environ, "LC_ALL": "C", "LANG": "C"},
    )
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout)
    except BaseException:
        if process.returncode is None:
            process.kill()
        await process.communicate()
        raise
    if process.returncode:
        raise RuntimeError(f"{arguments[0]} failed ({process.returncode}): {stderr.decode(errors='replace').strip()}")
    return stdout.decode(errors="replace").strip()


class NetworkManager:
    def __init__(self, store: Store, interface: str | None = None, *, runner: Callable[..., Awaitable[str]] = run_command,
                 ip_timeout: float = 35, poll_interval: float = .25):
        self.store = store
        self.interface = interface
        self.runner = runner
        self.ip_timeout = ip_timeout
        self.poll_interval = poll_interval

    async def _nm(self, *args: str, timeout: float = 40) -> str:
        return await self.runner("nmcli", *args, timeout=timeout)

    async def management_interface(self) -> str:
        output = await self._nm("-t", "-f", "DEVICE,TYPE", "device", "status")
        interfaces = [line.split(":", 1)[0] for line in output.splitlines() if line.endswith(":ethernet")]
        selected = self.interface
        if selected is None:
            selected = "eth0" if "eth0" in interfaces else interfaces[0] if len(interfaces) == 1 else None
        if selected not in interfaces or not re.fullmatch(r"[a-zA-Z0-9_.-]{1,15}", selected or ""):
            raise RuntimeError("Select an available wired management interface in settings")
        return selected

    async def cleanup(self) -> None:
        owned = self.store.get("network.owned")
        if not owned:
            return
        known = (await self._nm("-g", "UUID", "connection", "show")).splitlines()
        if owned["uuid"] in known:
            descriptor = (await self._nm("-g", "connection.id,connection.type,connection.interface-name", "connection", "show", "uuid", owned["uuid"])).splitlines()
            expected = [owned["name"], "vlan", owned["interface"]]
            if descriptor != expected:
                raise RuntimeError("Recorded OMI VLAN profile changed externally; refusing to delete an unrelated profile")
            # Deleting an active NM profile disconnects and removes its software VLAN.
            await self._nm("--wait", "15", "connection", "delete", "uuid", owned["uuid"], timeout=20)
            remaining = (await self._nm("-g", "UUID", "connection", "show")).splitlines()
            if owned["uuid"] in remaining:
                raise RuntimeError("NetworkManager did not remove the OMI VLAN profile")
        self.store.delete("network.owned")

    def _ip_arguments(self, profile: NetworkProfile, *, service: bool) -> list[str]:
        arguments = ["ipv4.method", "auto" if profile.mode == "dhcp" else "manual", "ipv4.may-fail", "no",
                     "ipv4.dhcp-timeout", str(max(1, int(self.ip_timeout))), "ipv6.method", "disabled"]
        if service:
            # DHCP service interfaces must not take over default routes or DNS.
            # A manually supplied gateway is explicit and uses a high metric.
            arguments += ["ipv4.never-default", "no" if profile.gateway else "yes", "ipv4.route-metric", "32000",
                          "ipv4.ignore-auto-routes", "yes", "ipv4.ignore-auto-dns", "yes", "ipv4.dns-priority", "2000"]
        if profile.mode == "static":
            arguments += ["ipv4.addresses", profile.address]
        else:
            arguments += ["ipv4.addresses", ""]
        arguments += ["ipv4.gateway", profile.gateway, "ipv4.dns", ",".join(profile.dns)]
        return arguments

    async def _wait_ip(self, interface: str, profile: NetworkProfile) -> dict:
        deadline = time.monotonic() + self.ip_timeout
        while True:
            output = await self._nm("-g", "IP4.ADDRESS", "device", "show", interface)
            addresses = []
            for line in output.splitlines():
                try:
                    address = ipaddress.IPv4Interface(line)
                except ValueError:
                    continue
                if not address.ip.is_unspecified and not address.ip.is_link_local:
                    addresses.append(str(address))
            if addresses:
                if profile.mode == "static" and str(ipaddress.IPv4Interface(profile.address)) not in addresses:
                    raise RuntimeError(f"Configured IP {profile.address} is not present on {interface}")
                return {"interface": interface, "addresses": addresses, "vlan": profile.vlan}
            if time.monotonic() >= deadline:
                raise TimeoutError(f"No usable IPv4 address on {interface} before DHCP/IP deadline")
            await asyncio.sleep(min(self.poll_interval, max(0, deadline - time.monotonic())))

    async def prepare(self, profile: NetworkProfile) -> dict:
        interface = await self.management_interface()
        if profile.vlan is None:
            return await self._wait_ip(interface, NetworkProfile())
        if self.store.get("network.owned"):
            raise RuntimeError("Previous OMI VLAN must be removed before preparing a new one")
        identifier = str(uuid.uuid4())
        vlan_interface = f"omi.{profile.vlan}"
        name = f"omi-service-{identifier}"
        owned = {"uuid": identifier, "interface": vlan_interface, "name": name}
        # Journal ownership before creating it so an interrupted start is recoverable.
        self.store.put("network.owned", owned)
        try:
            await self._nm("connection", "add", "type", "vlan", "con-name", name,
                           "connection.uuid", identifier, "ifname", vlan_interface, "dev", interface,
                           "id", str(profile.vlan), "connection.autoconnect", "no", *self._ip_arguments(profile, service=True))
            await self._nm("--wait", str(max(1, int(self.ip_timeout))), "connection", "up", "uuid", identifier,
                           timeout=self.ip_timeout + 5)
            return await self._wait_ip(vlan_interface, profile)
        except BaseException:
            await self.cleanup()
            raise

    async def configure_management(self, profile: NetworkProfile) -> dict:
        if profile.vlan is not None:
            raise ValueError("Management must be untagged")
        interface = await self.management_interface()
        connection_id = await self._nm("-g", "GENERAL.CON-UUID", "device", "show", interface)
        if not connection_id or connection_id == "--":
            raise RuntimeError("No active management profile; configure Ethernet locally first")
        await self._nm("connection", "modify", "uuid", connection_id, *self._ip_arguments(profile, service=False))
        await self._nm("--wait", str(max(1, int(self.ip_timeout))), "connection", "up", "uuid", connection_id,
                       timeout=self.ip_timeout + 5)
        self.store.put("management", profile.model_dump())
        return await self._wait_ip(interface, profile)


class SimulatedNetwork:
    """Explicit development adapter; cannot issue host network commands."""
    def __init__(self):
        self.events: list[str] = []

    async def cleanup(self) -> None:
        self.events.append("network.cleanup")

    async def prepare(self, profile: NetworkProfile) -> dict:
        self.events.append("network.prepare")
        return {"interface": "simulation", "addresses": [profile.address or "127.0.0.1/8"], "vlan": profile.vlan}

    async def configure_management(self, profile: NetworkProfile) -> dict:
        self.events.append("network.management")
        return await self.prepare(profile)
