from clixon_ui.servicechanges import changed_instances

RUNNING = {"service-timeout": 1800, "properties": {"x": 1},
           "l2c:l2c": [{"service-name": "A", "description": "d", "created": {"path": ["/p1"]}}, {"service-name": "B"}],
           "dwdm-link:dwdm-link": {"service-name": "L", "speed": "100g"}}


def test_no_changes_ignores_created_and_timeout():
    cand = {"service-timeout": 900, "properties": {"x": 1},
            "l2c:l2c": [{"service-name": "A", "description": "d", "created": {"path": ["/other"]}}, {"service-name": "B"}],
            "dwdm-link:dwdm-link": [{"service-name": "L", "speed": "100g"}]}
    assert changed_instances(cand, RUNNING) == []


def test_edit_add_remove_and_properties():
    cand = {"properties": {"x": 2},
            "l2c:l2c": [{"service-name": "A", "description": "changed"}, {"service-name": "C"}],   # A edited, B removed, C added
            "dwdm-link:dwdm-link": {"service-name": "L", "speed": "100g"}}
    assert changed_instances(cand, RUNNING) == ["l2c 'A'", "l2c 'B'", "l2c 'C'", "properties"]
