from clixon_ui.netconfxml import Op, edit_config_body, to_xml

NS = {"clixon-controller": "http://clicon.org/controller", "ietf-netconf-acm": "urn:ietf:params:xml:ns:yang:ietf-netconf-acm",
      "ietf-inet-types": "urn:x"}


def test_list_entry_replace_and_namespace_only_where_module_changes():
    xml = to_xml({"clixon-controller:devices": {"device": [Op("replace", {"name": "a", "enabled": True, "port": 22})]}}, NS)
    assert xml.startswith('<devices xmlns="http://clicon.org/controller">')
    assert xml.count("xmlns=") == 1
    assert '<device xmlns:nc="urn:ietf:params:xml:ns:netconf:base:1.0" nc:operation="replace">' in xml
    assert "<enabled>true</enabled>" in xml and "<port>22</port>" in xml


def test_remove_with_keys_only_and_empty_container():
    assert to_xml({"clixon-controller:devices": {"device": [Op("remove", {"name": "a"})]}}, NS).endswith("<name>a</name></device></devices>")
    assert to_xml({"ietf-netconf-acm:nacm": Op("remove", {})}, NS).endswith('nc:operation="remove"/>')


def test_op_around_list_applies_to_each_entry_and_leaf_list_scalars():
    xml = to_xml({"clixon-controller:x": {"v": Op("replace", ["a", "b"])}}, NS)
    assert xml.count('nc:operation="replace"') == 2 and "<v " in xml and ">a</v>" in xml and ">b</v>" in xml


def test_escaping_empty_leaf_and_identityref_prefix():
    xml = to_xml({"clixon-controller:c": {"d": "a<b&c", "e": None, "t": "ietf-inet-types:thing", "ip": "2001:db8::1"}}, NS)
    assert "a&lt;b&amp;c" in xml and "<e/>" in xml
    assert 'xmlns:ietf-inet-types="urn:x"' in xml and "xmlns:2001" not in xml


def test_unknown_module_is_an_error():
    try:
        to_xml({"nope:x": 1}, NS)
    except ValueError as e:
        assert "nope" in str(e)
    else:
        raise AssertionError


def test_body_wraps_in_edit_config_input():
    b = edit_config_body({"clixon-controller:devices": {}}, NS)
    assert "<target><candidate/></target>" in b and "<default-operation>merge</default-operation>" in b
