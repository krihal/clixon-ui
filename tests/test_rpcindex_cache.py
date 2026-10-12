from clixon_ui import rpcschema
from clixon_ui.rpcschema import RpcIndex
from clixon_ui.schema import module_list

YANG = 'module junos-rpc-x {\n  rpc get-thing {\n    description "Show a thing";\n  }\n}\n'


class FakeClient:
    url = "https://fake:1443"

    def __init__(self, modules):
        self.modules = modules  # [(name, revision)]
        self.listed = 0
        self.fetched: list[tuple] = []

    async def device_schemas(self, device, module=None, revision=None, detail=False):
        if module is None:
            self.listed += 1
            return [{"name": n, "revision": r} for n, r in self.modules]
        self.fetched.append((module, revision))
        return [{"name": module, "revision": revision, "data": YANG.replace("junos-rpc-x", module)}]


async def test_second_call_uses_memory(tmp_path):
    c = FakeClient([("junos-rpc-a", "2024-01-01")])
    index = RpcIndex(c, tmp_path)
    first = await index.for_device("r1")
    await index.for_device("r1")
    assert [r.name for r in first] == ["get-thing"]
    assert c.fetched == [("junos-rpc-a", "2024-01-01")] and c.listed == 1


async def test_a_restart_reads_the_disk_cache(tmp_path):
    await RpcIndex(FakeClient([("junos-rpc-a", "2024-01-01")]), tmp_path).for_device("r1")
    c = FakeClient([("junos-rpc-a", "2024-01-01")])
    rpcs = await RpcIndex(c, tmp_path).for_device("r2")  # another device with the same module, another process
    assert [r.name for r in rpcs] == ["get-thing"] and c.fetched == []


async def test_new_revision_fetches_only_what_is_new(tmp_path, monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(rpcschema.time, "monotonic", lambda: now[0])
    c = FakeClient([("junos-rpc-a", "2024-01-01")])
    index = RpcIndex(c, tmp_path)
    await index.for_device("r1")
    c.modules = [("junos-rpc-a", "2024-01-01"), ("junos-rpc-b", "2025-01-01")]  # software upgrade
    await index.for_device("r1")
    assert c.listed == 1  # not asked yet: checked recently
    now[0] += rpcschema.REVALIDATE_SECONDS + 1
    rpcs = await index.for_device("r1")
    assert c.fetched == [("junos-rpc-a", "2024-01-01"), ("junos-rpc-b", "2025-01-01")]
    assert len(rpcs) == 2


async def test_unchanged_module_list_keeps_the_index(tmp_path, monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(rpcschema.time, "monotonic", lambda: now[0])
    c = FakeClient([("junos-rpc-a", "2024-01-01")])
    index = RpcIndex(c, tmp_path)
    await index.for_device("r1")
    now[0] += rpcschema.REVALIDATE_SECONDS + 1
    await index.for_device("r1")
    assert c.listed == 2 and len(c.fetched) == 1


async def test_refresh_fetches_again(tmp_path):
    c = FakeClient([("junos-rpc-a", "2024-01-01")])
    index = RpcIndex(c, tmp_path)
    await index.for_device("r1")
    await index.for_device("r1", refresh=True)
    assert len(c.fetched) == 2


def test_module_list_includes_submodules_and_is_sorted():
    mods = [{"name": "b", "revision": "2"}, {"name": "a", "submodule": [{"name": "s", "revision": "9"}]}]
    assert module_list(mods) == [("a", ""), ("b", "2"), ("s", "9")]
