from clixon_ui.rpcutil import is_read_only, rpc_name, substitute, template_vars


def test_vars_declared_and_undeclared():
    t = {"variables": {"variable": [{"name": "peer", "description": "peer ip"}]},
         "config": {"get-route-information": {"table": "${table}", "receive-protocol-name": "bgp", "peer": "${peer}"}}}
    assert template_vars(t) == [("peer", "peer ip"), ("table", "")]


def test_read_only_and_name():
    assert is_read_only({"get-system-information": {}})
    assert not is_read_only({"request-interface-optics-reset": {"transceiver-name": "x"}})
    assert rpc_name({"get-pic-detail": {}}) == "get-pic-detail" and rpc_name(None) == ""


def test_substitute_preview():
    cfg = {"get-interface-information": {"interface-name": "${interface}", "extensive": {}}}
    assert substitute(cfg, {"interface": "et-0/0/10"})["get-interface-information"]["interface-name"] == "et-0/0/10"
    assert substitute(cfg, {})["get-interface-information"]["interface-name"] == "${interface}"
