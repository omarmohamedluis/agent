import asyncio
import sys

import pytest

from omi.models import NetworkProfile
from omi.network import NetworkManager, run_command
from omi.store import Store


class Nmcli:
    def __init__(self):
        self.calls = []
        self.profiles = {"unrelated": ["manual-vlan", "vlan", "eth0.20"]}
        self.ip = "192.168.100.10/24"
        self.fail_up = False

    async def __call__(self, *args, timeout):
        assert args[0] == "nmcli"
        self.calls.append(args)
        if "DEVICE,TYPE" in args:
            return "eth0:ethernet\nlo:loopback"
        if args[1:3] == ("-g", "UUID"):
            return "\n".join(self.profiles)
        if "connection.id,connection.type,connection.interface-name" in args:
            return "\n".join(self.profiles[args[-1]])
        if args[1:3] == ("connection", "add"):
            value = lambda key: args[args.index(key)+1]
            self.profiles[value("connection.uuid")] = [value("con-name"), "vlan", value("ifname")]
            return "added"
        if "delete" in args:
            del self.profiles[args[-1]]
            return "deleted"
        if "up" in args and self.fail_up:
            raise RuntimeError("DHCP activation failed")
        if "IP4.ADDRESS" in args:
            return self.ip
        if "GENERAL.CON-UUID" in args:
            return "management-uuid"
        return ""


@pytest.fixture
def rig(tmp_path):
    store = Store(tmp_path / "omi.sqlite3")
    nm = Nmcli()
    manager = NetworkManager(store, runner=nm, ip_timeout=.01, poll_interval=.001)
    yield store, manager, nm
    store.close()


async def test_vlan_actual_nmcli_profile_and_readiness_then_owned_cleanup(rig):
    store, manager, nm = rig
    state = await manager.prepare(NetworkProfile(vlan=100, mode="static", address="192.168.100.10/24"))
    assert state["interface"] == "omi.100"
    added = next(call for call in nm.calls if "add" in call)
    assert added[added.index("connection.autoconnect")+1] == "no"
    assert added[added.index("ipv4.never-default")+1] == "yes"
    assert added[added.index("ipv4.ignore-auto-routes")+1] == "yes"
    assert "management-uuid" not in added
    assert store.get("network.owned")["uuid"] in nm.profiles
    await manager.cleanup()
    assert set(nm.profiles) == {"unrelated"}
    assert store.get("network.owned") is None


async def test_no_dhcp_and_failed_activation_cleanup_and_preserve_management(rig):
    store, manager, nm = rig
    nm.ip = ""
    with pytest.raises(TimeoutError, match="IPv4"):
        await manager.prepare(NetworkProfile(vlan=100))
    assert set(nm.profiles) == {"unrelated"}
    nm.fail_up = True
    with pytest.raises(RuntimeError, match="activation"):
        await manager.prepare(NetworkProfile(vlan=200))
    assert set(nm.profiles) == {"unrelated"}


async def test_static_ip_mismatch_is_failure_not_success(rig):
    store, manager, nm = rig
    with pytest.raises(RuntimeError, match="not present"):
        await manager.prepare(NetworkProfile(vlan=100, mode="static", address="192.168.200.10/24"))
    assert store.get("network.owned") is None


async def test_ownership_changed_externally_never_deletes(rig):
    store, manager, nm = rig
    store.put("network.owned", {"uuid": "unrelated", "name": "omi-service-own", "interface": "omi.100"})
    with pytest.raises(RuntimeError, match="unrelated"):
        await manager.cleanup()
    assert set(nm.profiles) == {"unrelated"}
    assert not any("delete" in call for call in nm.calls)


async def test_untagged_and_management_use_distinct_paths(rig):
    store, manager, nm = rig
    await manager.prepare(NetworkProfile())
    assert not any("add" in call or "modify" in call for call in nm.calls)
    await manager.configure_management(NetworkProfile(mode="static", address="192.168.100.10/24", gateway="192.168.100.1"))
    modify = next(call for call in nm.calls if "modify" in call)
    assert "management-uuid" in modify
    assert "32000" not in modify
    with pytest.raises(ValueError, match="untagged"):
        await manager.configure_management(NetworkProfile(vlan=100))


async def test_subprocess_deadline_kills_actual_child():
    with pytest.raises(asyncio.TimeoutError):
        await run_command(sys.executable, "-c", "import time; time.sleep(30)", timeout=.05)
    result = await run_command(sys.executable, "-c", "print('usable')", timeout=2)
    assert result == "usable"
