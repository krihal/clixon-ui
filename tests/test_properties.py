from clixon_ui.formdata import entry_from_json, property_entries, property_key, property_to_json
from clixon_ui.schema import Node


def _leaf(name, module):
    return Node(kind="leaf", name=name, module=module)


def test_container_property_body():
    p = Node(kind="container", name="bgp-peer", module="bgp", children=[_leaf("naming", "bgp")])
    assert property_to_json(p, {"naming": "x"}) == {"bgp:bgp-peer": {"naming": "x"}}


def test_list_property_entries_and_keys():
    p = Node(kind="list", name="customer", module="services", keys=["customer"],
             children=[_leaf("customer", "services"), _leaf("note", "services")])
    data = {"customer": [entry_from_json(p, {"customer": "A", "note": "n"}), {}]}
    assert property_key(p, data["customer"][0]) == "A"
    assert property_entries(p, data) == {"A": {"services:customer": [{"customer": "A", "note": "n"}]}}
    assert property_to_json(p, data) == {"services:customer": [{"customer": "A", "note": "n"}]}


def test_validate_ignores_mandatory_leaf_of_unused_case():
    from clixon_ui.formdata import validate
    path = Node(kind="leaf", name="path", module="m", mandatory=True)
    rpc = Node(kind="leaf", name="rpc-name", module="m")
    choice = Node(kind="choice", name="rule-type", module="m", children=[
        Node(kind="case", name="data-node", module="m", children=[path]),
        Node(kind="case", name="protocol-operation", module="m", children=[rpc])])
    rule = Node(kind="container", name="rule", module="m", children=[choice])
    assert validate(rule, {"rpc-name": "x"}) == []
    assert validate(rule, {}) == []
