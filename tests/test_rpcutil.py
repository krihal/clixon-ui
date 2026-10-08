from clixon_ui.rpcutil import cli_request, is_read_only, is_read_only_cli, rpc_name, substitute, template_vars


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


def test_cli_read_only_detection():
    assert is_read_only_cli("show version") and is_read_only_cli("  SHOW interfaces terse | match ge-")
    assert not is_read_only_cli("request system reboot") and not is_read_only_cli("clear bgp neighbor")
    assert not is_read_only_cli("show configuration | save /tmp/x") and not is_read_only_cli("ping 1.1.1.1") and not is_read_only_cli("")


def test_cli_request_shape():
    r = cli_request("  show version ")
    assert r["inline"] == {"command": "show version"} and r["read_only"] and r["label"] == "show version"
