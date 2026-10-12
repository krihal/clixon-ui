import asyncio

import httpx

from clixon_ui import client as client_mod
from clixon_ui.client import ClixonClient

DEVICES = {"clixon-controller:devices": [{"device": [{"name": "a", "conn-state": "OPEN"}]}]}
TX = {"clixon-controller:transactions": {"transaction": [{"tid": "1", "state": "DONE", "result": "SUCCESS"}]}}


def make(counter: dict, tx=TX):
    def handler(r: httpx.Request):
        counter[r.method] = counter.get(r.method, 0) + 1
        if "transactions" in r.url.path:
            return httpx.Response(200, json=tx)
        if r.method == "POST":
            return httpx.Response(200, json={})
        return httpx.Response(200, json=DEVICES)

    return ClixonClient("https://x:1443", transport=httpx.MockTransport(handler))


async def test_uncached_reads_always_hit_the_controller():
    n: dict = {}
    c = make(n)
    await c.devices()
    await c.devices()
    assert n["GET"] == 4  # two requests per devices() call


async def test_cached_reads_share_one_answer():
    n: dict = {}
    c = make(n)
    first = await c.devices(cached=True)
    second = await c.devices(cached=True)
    assert first == second and n["GET"] == 2


async def test_simultaneous_cached_reads_make_one_request():
    n: dict = {}
    c = make(n)
    await asyncio.gather(*(c.devices(cached=True) for _ in range(5)))
    assert n["GET"] == 2


async def test_cached_value_is_a_copy():
    c = make({})
    (await c.devices(cached=True))[0]["name"] = "changed"
    assert (await c.devices(cached=True))[0]["name"] == "a"


async def test_cache_expires(monkeypatch):
    n: dict = {}
    c = make(n)
    now = [1000.0]
    monkeypatch.setattr(client_mod.time, "monotonic", lambda: now[0])
    await c.devices(cached=True)
    now[0] += client_mod.LIGHT_TTL + 0.1
    await c.devices(cached=True)
    assert n["GET"] == 4


async def test_a_write_clears_the_cache():
    n: dict = {}
    c = make(n)
    await c.devices(cached=True)
    await c.rpc("connection-change", device="a", operation="OPEN")
    await c.devices(cached=True)
    assert n["GET"] == 4


async def test_cache_is_per_user():
    n: dict = {}
    c = make(n)
    who = ["alice"]
    c.user_provider = lambda: who[0]
    await c.devices(cached=True)
    who[0] = "bob"
    await c.devices(cached=True)
    assert n["GET"] == 4


async def test_a_new_transaction_clears_the_cache():
    n: dict = {}
    tx = {"v": TX}
    c = ClixonClient("https://x:1443", transport=httpx.MockTransport(
        lambda r: (n.__setitem__("GET", n.get("GET", 0) + 1), httpx.Response(200, json=tx["v"] if "transactions" in r.url.path else DEVICES))[1]))
    await c.transactions()  # first sight of the transactions
    await c.devices(cached=True)
    before = n["GET"]
    await c.devices(cached=True)
    assert n["GET"] == before  # served from the cache
    tx["v"] = {"clixon-controller:transactions": {"transaction": [*TX["clixon-controller:transactions"]["transaction"],
                                                                  {"tid": "2", "state": "INIT"}]}}
    await c.transactions()
    await c.devices(cached=True)
    assert n["GET"] > before + 1  # the controller did something: asked again
