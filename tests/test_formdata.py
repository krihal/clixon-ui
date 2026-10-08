import pytest
from pyang import context, repository

from clixon_ui.formdata import Lookup, entry_from_json, entry_to_json, service_to_json, validate
from clixon_ui.schema import Schema

CTRL = """module clixon-controller { namespace "urn:c"; prefix ctrl;
  container services { }
  container devices { list device { key name; leaf name { type string; } } } }"""
SVC = """module demo { namespace "urn:demo"; prefix d;
  import clixon-controller { prefix ctrl; }
  typedef pct { type uint8 { range "0..100"; } }
  augment /ctrl:services {
    list demo { key service-name;
      leaf service-name { type string { pattern '[A-Z]+'; } }
      leaf router { type leafref { path "/ctrl:devices/ctrl:device/ctrl:name"; } }
      leaf load { type pct; }
      leaf big { type uint64; }
      leaf power { type decimal64 { fraction-digits 2; } }
      leaf mode { type enumeration { enum fast; enum slow; } mandatory true; }
      leaf enabled { type empty; }
      leaf-list tags { type string; }
      choice kind { case a { leaf a-leaf { type string; } } case b { leaf b-leaf { type uint16; } } }
      container opt { presence "x"; leaf v { type string; } }
      list port { key id; leaf id { type uint8; } leaf descr { type string; } }
    } } }"""


@pytest.fixture(scope="module")
def schema(tmp_path_factory):
    d = tmp_path_factory.mktemp("yang")
    ctx = context.Context(repository.FileRepository(str(d), use_env=False))
    for name, text in (("clixon-controller", CTRL), ("demo", SVC)):
        ctx.add_module(f"{name}.yang", text, in_format="yang")
    ctx.validate()
    return Schema(ctx)


@pytest.fixture
def demo(schema):
    return schema.service("demo")


LOOKUP = Lookup({"devices": {"device": [{"name": "r1"}, {"name": "r2"}]}})


def test_roundtrip_and_types(demo):
    j = {"service-name": "ABC", "router": "r1", "load": 5, "big": "123", "power": "1.50", "mode": "fast",
         "enabled": [None], "tags": ["x", "y"], "b-leaf": 7, "opt": {"v": "q"}, "port": [{"id": 1, "descr": "d"}]}
    form = entry_from_json(demo, j)
    assert form["enabled"] is True and form["big"] == 123 and form["power"] == 1.5
    assert entry_to_json(demo, form) == j  # uint64 and decimal as strings, empty as [null]


def test_top_level_prefixed(demo):
    assert service_to_json(demo, {"service-name": "A"}) == {"demo:demo": [{"service-name": "A"}]}


def test_empty_things_dropped(demo):
    assert entry_to_json(demo, {"service-name": "A", "tags": [], "port": [{}], "router": ""}) == {"service-name": "A"}
    # an enabled presence container is meaningful even when empty
    assert entry_to_json(demo, {"service-name": "A", "opt": {}}) == {"service-name": "A", "opt": {}}


def test_validation(demo):
    ok = {"service-name": "ABC", "mode": "fast"}
    assert validate(demo, ok, LOOKUP) == []
    errs = validate(demo, {"service-name": "abc", "router": "zz", "load": 101, "mode": "warp", "port": [{"id": 1}, {"id": 1}]}, LOOKUP)
    text = "\n".join(errs)
    for needle in ("pattern", "'zz' does not exist", "≤ 100", "one of: fast, slow", "duplicate key"):
        assert needle in text, needle


def test_required(demo):
    errs = validate(demo, {}, LOOKUP)
    assert any("service-name: required" in e for e in errs) and any("mode: required" in e for e in errs)


def test_presence_container_not_required_when_absent(demo):
    assert not [e for e in validate(demo, {"service-name": "A", "mode": "fast"}, LOOKUP) if "opt" in e]


def test_leafref_lookup():
    assert LOOKUP.values("/ctrl:devices/ctrl:device/ctrl:name") == ["r1", "r2"]
    assert LOOKUP.values("../x") is None


def test_preserved_nodes_survive_put(demo):
    created = {"created": {"path": ["/devices/device[name=\"r1\"]/config/x"]}}
    body = service_to_json(demo, {"service-name": "A"}, created)
    assert body == {"demo:demo": [{"service-name": "A", **created}]}
