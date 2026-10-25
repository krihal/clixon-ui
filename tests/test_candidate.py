from clixon_ui import candidate
from clixon_ui.candidate import ACCESS, INVENTORY, SERVICES

RUN_S = {"service-timeout": 1800, "properties": {"x": 1},
         "l2c:l2c": [{"service-name": "A", "description": "d", "created": {"path": ["/p1"]}}, {"service-name": "B"}]}


def test_services_added_modified_deleted_and_bookkeeping_ignored():
    cand = {"service-timeout": 900, "properties": {"x": 1},
            "l2c:l2c": [{"service-name": "A", "description": "changed", "created": {"path": ["/other"]}}, {"service-name": "C"}]}
    got = {(c.key, c.status): c for c in candidate.service_changes(cand, RUN_S)}
    assert set(got) == {("A", "modified"), ("B", "deleted"), ("C", "added")}
    assert got["A", "modified"].old["created"] == {"path": ["/p1"]}  # kept: putting the entry back must send it
    assert got["B", "deleted"].new is None and got["C", "added"].old is None
    assert all(c.revertible for c in got.values())
    unchanged = {**RUN_S, "l2c:l2c": [{"service-name": "A", "description": "d", "created": {"path": ["/x"]}}, {"service-name": "B"}]}
    assert candidate.service_changes(unchanged, RUN_S) == []


def test_property_changes_cannot_be_reverted_one_by_one():
    (ch,) = candidate.service_changes({"properties": {"x": 2}}, {"properties": {"x": 1}})
    assert (ch.kind, ch.key, ch.revertible) == ("properties", "x", False) and ch.why_not


def test_inventory_changes_and_deleted_device():
    run = {"device": [{"name": "r1", "user": "a"}, {"name": "r2"}], "device-group": [{"name": "g"}]}
    cand = {"device": [{"name": "r1", "user": "b"}], "device-group": [{"name": "g"}, {"name": "h"}]}
    got = {(c.kind, c.key): c for c in candidate.inventory_changes(cand, run)}
    assert set(got) == {("device", "r1"), ("device", "r2"), ("device-group", "h")}
    assert got["device", "r1"].status == "modified" and got["device", "r1"].revertible
    assert got["device", "r2"].status == "deleted" and not got["device", "r2"].revertible
    assert got["device-group", "h"].status == "added" and got["device-group", "h"].revertible


def test_nacm_and_grouping():
    assert candidate.nacm_changes(None, None) == [] and candidate.nacm_changes({}, None) == []
    (ch,) = candidate.nacm_changes({"enable-nacm": True}, None)
    assert (ch.area, ch.status) == (ACCESS, "added")
    changes = candidate.all_changes({"services": {"l2c": [{"service-name": "A"}]}, "devices": {"device-group": [{"name": "g"}]}, "nacm": None},
                                    {"services": {}, "devices": {}, "nacm": {"enable-nacm": "true"}})
    assert [(a, len(i)) for a, i in candidate.grouped(changes)] == [(SERVICES, 1), (INVENTORY, 1), (ACCESS, 1)]


def test_text_diff_shows_running_to_candidate():
    text = candidate.text_diff({"service-name": "A", "mtu": 1500, "created": {"path": ["/x"]}}, {"service-name": "A", "mtu": 9000})
    assert '-  "mtu": 1500' in text and '+  "mtu": 9000' in text and "created" not in text
    assert candidate.text_diff(None, {"a": 1}).startswith("+{")
