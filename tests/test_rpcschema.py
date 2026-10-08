from clixon_ui.rpcschema import parse_module

YANG = '''module junos-rpc-demo {
  namespace "x"; prefix d;
  rpc get-demo-information {
    description "Show demo {braces} and more
      text";
    input {
      uses command-forwarding;
      leaf verbosity { type enumeration { enum detail { description "d"; } } }
      container filter { leaf inner { type string; } }
      leaf-list tags { type string; }
    }
    output { leaf out { type string; } }
  }
  rpc request-demo-reset {
    description "Reset it";
  }
}'''


def test_parse_rpcs():
    rpcs = {r.name: r for r in parse_module(YANG, "junos-rpc-demo")}
    assert set(rpcs) == {"get-demo-information", "request-demo-reset"}
    g = rpcs["get-demo-information"]
    assert g.description == "Show demo {braces} and more text"
    assert g.args == ["verbosity", "filter", "tags"]  # top-level input args only, not "inner"
    assert g.area == "demo" and g.read_only and not rpcs["request-demo-reset"].read_only
    assert rpcs["request-demo-reset"].args == [] and rpcs["request-demo-reset"].description == "Reset it"
