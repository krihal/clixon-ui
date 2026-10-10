import json

import httpx
import pytest

from clixon_ui.client import ClixonClient, RestconfError

DEVICES = {"clixon-controller:devices": [
    {"device": [{"name": "b", "conn-state": "CLOSED"}]},
    {"device": [{"name": "a", "conn-state": "OPEN"}]},
]}


def make(handler):
    return ClixonClient("https://x:1443", transport=httpx.MockTransport(handler))


async def test_devices_flattened_and_sorted():
    c = make(lambda r: httpx.Response(200, json=DEVICES))
    assert [d["name"] for d in await c.devices()] == ["a", "b"]


async def test_rpc_body_and_tid():
    seen = {}

    def h(r: httpx.Request):
        seen["url"], seen["body"] = str(r.url), json.loads(r.content)
        return httpx.Response(200, json={"clixon-controller:output": {"tid": 7}})

    assert await make(h).connection_change("ptx-ac-1", "OPEN") == 7
    assert seen["url"].endswith("/restconf/operations/clixon-controller:connection-change")
    assert seen["body"] == {"clixon-controller:input": {"device": "ptx-ac-1", "operation": "OPEN"}}


async def test_error_message():
    err = {"ietf-restconf:errors": {"error": {"error-message": "boom"}}}
    with pytest.raises(RestconfError, match="boom"):
        await make(lambda r: httpx.Response(400, json=err)).devices()


async def test_wait_transaction_done():
    t = {"clixon-controller:transactions": {"transaction": [{"tid": "3", "state": "DONE", "result": "SUCCESS"}]}}
    tr = await make(lambda r: httpx.Response(200, json=t)).wait_transaction(3, timeout=2)
    assert tr["result"] == "SUCCESS"


async def test_get_retries_502_then_succeeds():
    calls = []

    def h(r: httpx.Request):
        calls.append(1)
        return httpx.Response(502, text="<html><body>502 Bad Gateway</body></html>") if len(calls) < 3 else httpx.Response(200, json=DEVICES)

    assert len(await make(h).devices()) == 2 and len(calls) == 4  # 2 failed + state ok + config ok


async def test_html_error_is_readable():
    c = make(lambda r: httpx.Response(502, text="<html><head><title>502 Bad Gateway</title></head></html>"))
    with pytest.raises(RestconfError, match="web server answered HTTP 502"):
        await c.devices()


async def test_user_sent_as_header_and_view_only_guard():
    seen = []

    def h(r: httpx.Request):
        seen.append((r.method, r.headers.get("x-forwarded-user"), r.headers.get("http_authorization")))
        return httpx.Response(200, json={})

    c = make(h)
    c.user_provider = lambda: "kim"
    await c.get("clixon-controller:devices")
    assert seen[0][1] == "kim" and seen[0][2] == "kim"

    def deny():
        raise PermissionError("view-only")

    c.write_guard = deny
    await c.get("clixon-controller:devices")  # reads pass
    with pytest.raises(PermissionError):
        await c.local_commit()
    assert len(seen) == 2  # nothing was sent for the refused write
