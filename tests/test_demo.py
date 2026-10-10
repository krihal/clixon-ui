"""The demo's fake controller, exercised through the real ClixonClient (in-process, no network)."""

from __future__ import annotations

import pytest

from clixon_ui import demo
from clixon_ui.demo import seed
from clixon_ui.netconfxml import Op


@pytest.fixture
def client():
    c, _ = demo.make_client()
    return c


async def test_devices_and_inventory(client):
    devs = await client.devices()
    assert len(devs) == 10 and [d["name"] for d in devs] == sorted(seed.DEVICES)
    assert {d["conn-state"] for d in devs} == {"OPEN"}
    inv = await client.inventory()
    assert not inv["device"][0].get("config")  # depth=3 leaves the mounted config out
    assert await client.device_groups() == ["access-switches", "all-switches", "arista-eos", "core-routers"]


async def test_device_config_is_browsable_per_entry(client):
    outline = await client.device_outline("demo-eos-1")
    assert len(outline) > 1  # OpenConfig: several top-level containers, no single root
    node = await client.device_node("demo-eos-1", "openconfig-interfaces:interfaces/interface=Ethernet1")
    assert node["openconfig-interfaces:interface"][0]["name"] == "Ethernet1"
    vlan = await client.device_node("demo-eos-1", "openconfig-network-instance:network-instances/network-instance=default/vlans/vlan=100")
    assert vlan["openconfig-network-instance:vlan"][0]["vlan-id"] == 100


async def test_edit_goes_to_candidate_until_committed(client):
    entry = (await client.candidate_services())["demo-vlan:vlan"][0]
    entry["description"] = "changed"
    await client.put_service("demo-vlan", "vlan", entry["service-name"], {"demo-vlan:vlan": [entry]})
    cand = (await client.candidate_services())["demo-vlan:vlan"][0]
    assert cand["description"] == "changed" and (await client.running_services())["demo-vlan:vlan"][0]["description"] != "changed"
    # types survive the XML round trip: numbers stay numbers, composite-key list entries stay intact
    assert cand["vlan-id"] == 100 and [m["interface"] for m in cand["member"]] == ["Ethernet10", "Ethernet20"]
    tr, diff = await client.commit_diff(None)
    assert tr["result"] == "SUCCESS" and "demo-eos-1:" in diff and "demo-eos-2:" in diff
    assert "access-vlan 100" not in diff  # only the VLAN name line changed
    await client.commit_service(None)
    assert (await client.running_services())["demo-vlan:vlan"][0]["description"] == "changed"


async def test_new_service_instance_and_boolean_leaf(client):
    peer = {"service-name": "transit-b", "device": "demo-eos-2", "neighbor-address": "192.0.2.9", "peer-as": 64497, "shutdown": True}
    await client.put_service("demo-bgp-peering", "bgp-peering", "transit-b", {"demo-bgp-peering:bgp-peering": [peer]})
    got = [e for e in (await client.candidate_services())["demo-bgp-peering:bgp-peering"] if e["service-name"] == "transit-b"][0]
    assert got["peer-as"] == 64497 and got["shutdown"] is True
    _, diff = await client.commit_diff(f"bgp-peering[service-name='transit-b']")
    assert "demo-eos-2:" in diff and "192.0.2.9" in diff


async def test_inventory_add_replace_remove(client):
    await client.inventory_put("device-group", "g", {"name": "g", "description": "d", "device-name": ["demo-mx-1", "demo-eos-2"]})
    assert (await client.inventory_entry("device-group", "g"))["device-name"] == ["demo-mx-1", "demo-eos-2"]
    await client.inventory_delete("device-group", "g")
    assert await client.inventory_entry("device-group", "g") is None


async def test_delete_service_from_candidate(client):
    await client.delete_service("demo-bgp-peering", "bgp-peering", "transit-a", ["service-name"])
    assert "demo-bgp-peering:bgp-peering" not in await client.candidate_services()
    assert "demo-bgp-peering:bgp-peering" in await client.running_services()


async def test_rpc_replies_per_device(client):
    tid = await client.run_rpc(device="demo-eos-1", inline={"get-lldp-neighbors": {}})
    assert (await client.wait_transaction(tid))["state"] == "DONE"
    reply = (await client.rpc_result(tid))["demo-eos-1"]
    assert {i["name"] for i in reply["lldp"]["interfaces"]["interface"]} == {"Ethernet1", "Ethernet2", "Ethernet3", "Ethernet4"}


async def test_connection_change_and_reset():
    c, controller = demo.make_client()
    await c.wait_transaction(await c.connection_change("demo-eos-2", "CLOSE"))
    assert {d["name"]: d["conn-state"] for d in await c.devices()}["demo-eos-2"] == "CLOSED"
    controller.reset()
    assert {d["conn-state"] for d in await c.devices()} == {"OPEN"}


async def test_schema_is_served_for_the_form_pages(client, tmp_path):
    from clixon_ui.schema import load_schema
    schema = await load_schema(client, tmp_path)
    assert {s.name for s in schema.services} == {"bgp-peering", "interface-config", "lag", "ntp", "static-route", "syslog", "vlan"}
    assert [p.name for p in schema.properties()] == ["demo-defaults"]
    assert schema.nacm() is not None and "device" in schema.inventory()


async def test_unknown_request_is_an_error_not_a_crash(client):
    from clixon_ui.client import RestconfError
    with pytest.raises(RestconfError):
        await client.get("clixon-controller:devices/device=nope")


@pytest.fixture
def oc_network():
    """network_sources.install() replaces the global Juniper sources; put them back for the other tests."""
    from clixon_ui import network, network_views
    saved = dict(network.SOURCES), dict(network.OVERLAYS), network_views.DEFAULT_SOURCE, network_views.DEFAULT_OVERLAY
    yield
    network.SOURCES.clear(), network.OVERLAYS.clear()
    network.SOURCES.update(saved[0]), network.OVERLAYS.update(saved[1])
    network_views.DEFAULT_SOURCE, network_views.DEFAULT_OVERLAY = saved[2], saved[3]


async def test_network_sources_parse_the_demo_replies(client, oc_network):
    from clixon_ui import network
    from clixon_ui.demo import network_sources
    network_sources.install()
    assert list(network.SOURCES) == ["demo-lldp"]
    adjs, ports = [], []
    devices = tuple(seed.DEVICES)
    for dev in devices:
        tid = await client.run_rpc(device=dev, inline=network.SOURCES["demo-lldp"].rpc)
        adjs += network_sources.LLDP.parse(dev, (await client.rpc_result(tid))[dev])
        tid = await client.run_rpc(device=dev, inline={"get-isis-state": {}})
        ports += network_sources.ISIS.parse(dev, {"get-isis-state": (await client.rpc_result(tid))[dev]})
    g = network.build_graph(adjs, list(devices))
    network.apply_overlay(g, ports)
    internal = [link for link in g.links if link.b_managed]
    assert len(internal) == len(seed.LINKS) // 2 == 16 and all(link.status == "confirmed" for link in internal)  # Junos and OpenConfig ends agree
    assert sorted(network.isis_status(link) for link in internal) == ["differs"] + ["up"] * 15
    assert len([link for link in g.links if not link.b_managed]) == 3


async def test_each_vendor_gets_its_own_models(client):
    # the management VLAN has members on an Arista and a Junos device
    _, diff = await client.commit_diff("vlan[service-name='management-vlan']")  # unchanged: nothing to show
    assert diff == ""
    entry = [e for e in (await client.candidate_services())["demo-vlan:vlan"] if e["service-name"] == "management-vlan"][0]
    entry["vlan-id"] = 11
    await client.put_service("demo-vlan", "vlan", "management-vlan", {"demo-vlan:vlan": [entry]})
    _, diff = await client.commit_diff("vlan[service-name='management-vlan']")
    sections = {line[:-1]: i for i, line in enumerate(diff.splitlines()) if line.endswith(":") and not line.startswith(("+", "-", " "))}
    assert set(sections) == {"demo-eos-1", "demo-mx-1"}
    junos, oc = diff.split("demo-mx-1:")[1], diff.split("demo-eos-1:")[1].split("demo-mx-1:")[0]
    assert "vlans {" in junos and "vlan-id 11" in junos  # Junos native
    assert "access-vlan 11" in oc and "vlan-id 11" in oc  # OpenConfig


async def test_device_configs_and_rpc_lists_differ_per_vendor():
    c, controller = demo.make_client()
    assert len(await c.device_outline("demo-mx-1")) == 1  # Junos: one root container
    junos_rpcs = {s["name"] for s in await c.device_schemas("demo-mx-1")}
    eos_rpcs = {s["name"] for s in await c.device_schemas("demo-eos-1")}
    assert "junos-rpc-system" in junos_rpcs and "junos-rpc-system" not in eos_rpcs
    assert {"demo-rpc-lldp", "demo-rpc-isis"} <= junos_rpcs & eos_rpcs


async def test_every_service_type_renders_for_every_vendor(client):
    """Change one instance of each type in the candidate: the commit diff must show the change on the right devices."""
    changes = {"interface-config": ("alpha-uplink", "mtu", 9000, "demo-eos-1:", "mtu 9000"),
               "static-route": ("edge-defaults", "description", "x", None, None),  # description is not on the device: nothing to show
               "lag": ("mx1-core-lag", "min-links", 2, "demo-mx-1:", "minimum-links 2"),
               "syslog": ("site-syslog", "server", [{"host": "192.0.2.99", "port": 514, "severity": "error"}], "demo-eos-1:", "192.0.2.99"),
               "ntp": ("site-ntp", "server", ["203.0.113.5"], "demo-mx-2:", "203.0.113.5")}
    for kind, (name, leaf, value, device, text) in changes.items():
        module = f"demo-{kind}"
        entry = [e for e in (await client.candidate_services())[f"{module}:{kind}"] if e["service-name"] == name][0]
        entry[leaf] = value
        await client.put_service(module, kind, name, {f"{module}:{kind}": [entry]})
        _, diff = await client.commit_diff(f"{kind}[service-name='{name}']")
        if device:
            assert device in diff and text in diff, (kind, diff)
        else:
            assert diff == ""


async def test_service_properties_roundtrip(client):
    props = (await client.candidate_services())["properties"]["demo-defaults:demo-defaults"]
    assert props["default-mtu"] == 9214
    await client.put_property("demo-defaults", "demo-defaults", {"demo-defaults:demo-defaults": {**props, "default-mtu": 9000}})
    assert (await client.candidate_services())["properties"]["demo-defaults:demo-defaults"]["default-mtu"] == 9000
