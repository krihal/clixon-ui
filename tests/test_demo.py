"""The demo's fake controller, exercised through the real ClixonClient (in-process, no network)."""

from __future__ import annotations

import pytest

from clixon_ui import demo
from clixon_ui.netconfxml import Op


@pytest.fixture
def client():
    c, _ = demo.make_client()
    return c


async def test_devices_and_inventory(client):
    devs = await client.devices()
    assert [d["name"] for d in devs] == ["demo-mx-1", "demo-mx-2", "demo-ptx-1"]
    assert {d["conn-state"] for d in devs} == {"OPEN"}
    inv = await client.inventory()
    assert not inv["device"][0].get("config")  # depth=3 leaves the mounted config out
    assert await client.device_groups() == ["all-routers", "mx-routers"]


async def test_device_config_is_browsable_per_entry(client):
    outline = await client.device_outline("demo-mx-1")
    root = next(iter(outline))
    assert "junos-conf-interfaces:interfaces" in outline[root]
    node = await client.device_node("demo-mx-1", f"{root}/junos-conf-interfaces:interfaces/interface=et-0%2F0%2F0")
    assert node["junos-conf-interfaces:interface"][0]["name"] == "et-0/0/0"


async def test_edit_goes_to_candidate_until_committed(client):
    entry = (await client.candidate_services())["l2c:l2c"][0]
    entry["description"] = "changed"
    await client.put_service("l2c", "l2c", entry["service-name"], {"l2c:l2c": [entry]})
    assert (await client.candidate_services())["l2c:l2c"][0]["description"] == "changed"
    assert (await client.running_services())["l2c:l2c"][0]["description"] != "changed"
    # a leaf named like a list elsewhere (l2c device/interface vs interface-customer's interface list) stays a leaf
    assert (await client.candidate_services())["l2c:l2c"][0]["connection"][0]["endpoint-a"]["device"]["interface"] == "ge-0/1/0"
    tr, diff = await client.commit_diff(None)
    assert tr["result"] == "SUCCESS" and "demo-mx-1:" in diff and "+" in diff
    await client.commit_service(None)
    assert (await client.running_services())["l2c:l2c"][0]["description"] == "changed"


async def test_inventory_add_replace_remove(client):
    await client.inventory_put("device-group", "g", {"name": "g", "description": "d", "device-name": ["demo-mx-1", "demo-mx-2"]})
    assert (await client.inventory_entry("device-group", "g"))["device-name"] == ["demo-mx-1", "demo-mx-2"]
    await client.inventory_delete("device-group", "g")
    assert await client.inventory_entry("device-group", "g") is None


async def test_delete_service_from_candidate(client):
    await client.delete_service("l2c", "l2c", "alpha-beta-l2", ["service-name"])
    assert "l2c:l2c" not in await client.candidate_services()
    assert "l2c:l2c" in await client.running_services()


async def test_rpc_replies_per_device(client):
    tid = await client.run_rpc(device="demo-mx-1", inline={"get-lldp-interface-neighbors": {}})
    assert (await client.wait_transaction(tid))["state"] == "DONE"
    neighbours = (await client.rpc_result(tid))["demo-mx-1"]["lldp-neighbors-information"]["lldp-neighbor-information"]
    assert {n["lldp-local-port-id"] for n in neighbours} >= {"et-0/0/0", "et-0/0/1"}


async def test_connection_change_and_reset():
    c, controller = demo.make_client()
    await c.wait_transaction(await c.connection_change("demo-mx-2", "CLOSE"))
    assert {d["name"]: d["conn-state"] for d in await c.devices()}["demo-mx-2"] == "CLOSED"
    controller.reset()
    assert {d["conn-state"] for d in await c.devices()} == {"OPEN"}


async def test_schema_is_served_for_the_form_pages(client, tmp_path):
    from clixon_ui.schema import load_schema
    schema = await load_schema(client, tmp_path)
    assert {"l2c", "interface-customer"} <= {s.name for s in schema.services}
    assert schema.nacm() is not None and "device" in schema.inventory()


async def test_unknown_request_is_an_error_not_a_crash(client):
    from clixon_ui.client import RestconfError
    with pytest.raises(RestconfError):
        await client.get("clixon-controller:devices/device=nope")
