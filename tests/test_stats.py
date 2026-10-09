from clixon_ui import stats


def test_device_stats():
    s = stats.device_stats([{"name": "a", "conn-state": "OPEN"}, {"name": "b", "conn-state": "CLOSED"},
                            {"name": "c", "conn-state": "CONNECTING"}, {"name": "d"}])
    assert (s["total"], s["open"], s["closed"], s["other"]) == (4, 1, 1, 2)
    assert [n for n, _ in s["attention"]] == ["b", "c", "d"]


def test_service_stats_sorted_and_deployed():
    s = stats.service_stats([("m:a", "a", [{"service-name": "1"}]),
                             ("m:b", "b", [{"service-name": "1", "created": {"y": 1}}, {"service-name": "2", "created": {"x": 1}}, {}])])
    assert [r["label"] for r in s["types"]] == ["b", "a"]
    assert (s["total"], s["deployed"], s["type_count"]) == (4, 2, 2)
    assert s["types"][0]["pending"] == 1


def test_transaction_stats_newest_first():
    trs = [{"tid": "2", "result": "SUCCESS"}, {"tid": "10", "result": "FAILED"}, {"tid": "3", "result": "ERROR"}]
    s = stats.transaction_stats(trs, recent=2)
    assert [t["tid"] for t in s["recent"]] == ["10", "3"]
    assert s["failed"] == 2 and s["total"] == 3


def test_inventory_counts_single_entry_is_dict():
    assert stats.inventory_counts({"device-group": {"name": "x"}, "template": [{}, {}]})["device-group"] == 1
