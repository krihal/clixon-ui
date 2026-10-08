from clixon_ui.confview import outline, to_lines


def test_junos_style_text():
    cfg = {"junos-conf-interfaces:interfaces": {
        "apply-groups": ["ETH"],
        "interface": [{"name": "et-0/0/10", "description": "to core", "disable": [None], "unit": [{"name": "0", "family": {"inet": {}}}]}]}}
    lines, trunc = to_lines(cfg)
    assert not trunc
    assert lines == [
        "interfaces {",
        "    apply-groups [ ETH ];",
        '    interface et-0/0/10 {',
        '        description "to core";',
        "        disable;",
        "        unit 0 {",
        "            family {",
        "                inet;",
        "            }",
        "        }",
        "    }",
        "}",
    ]


def test_truncation():
    lines, trunc = to_lines({"a": {f"k{i}": i for i in range(100)}}, limit=10)
    assert trunc and len(lines) == 10


def test_outline_paths():
    cfg = {"junos-conf-policy-options:policy-options": {"prefix-list": [{"name": "AS 1/2", "prefix-list-item": [{}]}]}}
    n = outline(cfg)
    po = n[0]
    assert po["id"] == "junos-conf-policy-options:policy-options" and po["label"] == "policy-options"
    pl = po["children"][0]
    assert pl["label"] == "prefix-list (1)"
    assert pl["children"][0]["id"] == "junos-conf-policy-options:policy-options/prefix-list=AS%201%2F2"


def test_list_nodes_are_not_selectable_but_entries_are():
    cfg = {"junos-conf-interfaces:interfaces": {"interface": [{"name": "et-0/0/1"}]}, "groups": [{}, {}]}
    tree = {n["label"]: n for n in outline(cfg)}
    lst = tree["interfaces"]["children"][0]
    assert lst["label"] == "interface (1)" and lst["selectable"] is False       # bare list path would be "malformed key"
    assert "selectable" not in lst["children"][0]                                # an entry can be fetched
    assert tree["groups (2) – entries not browsable"]["selectable"] is False and not tree["groups (2) – entries not browsable"]["children"]
