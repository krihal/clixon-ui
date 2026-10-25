import json

import pytest

from clixon_ui import guide as g
from clixon_ui import guide_runner as r
from clixon_ui.guide import GuideError


def probe(body=None, **kw):
    return {"body": body or {"get-system-information": {}}, "target": {"devices": ["{{dev}}"], "groups": []}, **kw}


def sample() -> dict:
    return {"name": "Upgrade", "description": "", "steps": [
        {"id": "a", "type": "input", "title": "Device", "var": "dev", "label": "Device", "kind": "device"},
        {"id": "b", "type": "text", "title": "Read", "text": "Upgrading {{dev}}"},
        {"id": "c", "type": "confirm", "title": "Drained?", "question": "Is {{dev}} drained?"},
        {"id": "d", "type": "check", "title": "Version", **probe(rules=[{"path": "version", "op": "equals", "value": "1"}])},
        {"id": "e", "type": "rpc", "title": "Install", **probe({"request-package-add": {"package-name": "x"}})},
        {"id": "f", "type": "wait", "title": "Reboot", "seconds": 5},
        {"id": "g", "type": "connection", "title": "Reconnect", "operation": "RECONNECT", "device": "{{dev}}"},
        {"id": "h", "type": "pull", "title": "Pull", "device": "{{dev}}", "merge": False},
    ]}


# ---------------------------------------------------------------------------------------------- variables
def test_variables_in_and_fill():
    assert g.variables_in({"a": "{{x}} and {{ y }}", "b": ["{{x}}"]}) == ["x", "y"]
    assert g.fill({"a": ["to {{x}}"]}, {"x": "r1"}) == {"a": ["to r1"]}
    with pytest.raises(GuideError, match="{{y}}"):
        g.fill("{{y}}", {"x": "1"})


# ---------------------------------------------------------------------------------------------- rules
REPLY = {"software-information": {"host-name": "r1", "package-information": [{"name": "os", "comment": "JUNOS 24.2R1"}, {"name": "x"}],
                                  "up": True}}


@pytest.mark.parametrize("rule, ok", [
    ({"path": "software-information/host-name", "op": "equals", "value": "r1"}, True),
    ({"path": "software-information/host-name", "op": "equals", "value": "r2"}, False),
    ({"path": "software-information/host-name", "op": "not-equals", "value": "r2"}, True),
    ({"path": "software-information/host-name", "op": "not-equals", "value": "r1"}, False),
    ({"path": "software-information/package-information/comment", "op": "contains", "value": "24.2"}, True),
    ({"path": "software-information/package-information/comment", "op": "regex", "value": r"JUNOS 2\d\."}, True),
    ({"path": "software-information/package-information/0/name", "op": "equals", "value": "os"}, True),
    ({"path": "software-information/package-information/1/name", "op": "equals", "value": "os"}, False),
    ({"path": "software-information/up", "op": "equals", "value": "true"}, True),
    ({"path": "software-information/missing", "op": "exists"}, False),
    ({"path": "software-information/missing", "op": "not-exists"}, True),
    ({"path": "software-information/missing", "op": "not-equals", "value": "a"}, False),  # nothing found is not a pass
])
def test_evaluate(rule, ok):
    assert g.evaluate([rule], REPLY)[0].ok is ok


def test_lookup_ignores_module_prefix_and_uses_variables():
    data = {"junos-rpc:get-x": {"a:ver": "7"}}
    assert g.lookup(data, "get-x/ver") == ["7"]
    assert g.evaluate([{"path": "get-x/ver", "op": "equals", "value": "{{v}}"}], data, {"v": "7"})[0].ok


def test_rule_describe_mentions_what_was_found():
    res = g.evaluate([{"path": "software-information/host-name", "op": "equals", "value": "r2"}], REPLY)[0]
    assert "'r1'" in res.describe() and "'r2'" in res.describe()


# ---------------------------------------------------------------------------------------------- validation
def test_sample_is_valid():
    assert g.validate(sample()) == []


def test_validate_finds_problems():
    guide = sample()
    guide["steps"][1]["text"] = "{{nope}}"
    guide["steps"][3]["body"] = {"request-reboot": {}}  # a check must be read-only
    guide["steps"][3]["rules"][0]["op"] = "bogus"
    guide["steps"][5]["seconds"] = g.MAX_WAIT_SECONDS + 1
    guide["steps"][4]["id"] = "a"
    text = "\n".join(g.validate(guide))
    assert "{{nope}}" in text and "read-only" in text and "bogus" in text and "wait between 1 and" in text and "unique id" in text


def test_variable_must_be_set_by_an_earlier_input():
    guide = sample()
    guide["steps"].insert(0, guide["steps"].pop(1))  # the text step now comes before the input step
    assert any("{{dev}}" in e for e in g.validate(guide))


def test_empty_and_bad_regex():
    assert "no steps" in g.validate({"name": "x", "steps": []})[0]
    guide = sample()
    guide["steps"][3]["rules"][0] = {"path": "a", "op": "regex", "value": "("}
    assert any("bad regex" in e for e in g.validate(guide))


def test_wait_until_needs_timeout_and_read_only_probe():
    step = {"id": "w", "type": "wait", "title": "Back up", "until": {**probe(rules=[{"path": "a", "op": "exists"}]), "interval": 10, "timeout": 600}}
    assert g.validate({"name": "x", "steps": [{"id": "a", "type": "input", "title": "D", "var": "dev", "kind": "text"}, step]}) == []
    step["until"]["timeout"] = 0
    step["until"]["body"] = {"request-reboot": {}}
    errs = "\n".join(g.validate({"name": "x", "steps": [{"id": "a", "type": "input", "title": "D", "var": "dev", "kind": "text"}, step]}))
    assert "timeout" in errs and "read-only" in errs


def test_new_step_of_every_type_has_a_title_and_no_shared_state():
    for t in g.STEP_TYPES:
        s = g.new_step(t, "x")
        assert s["type"] == t and s["title"]
    a, b = g.new_step("rpc", "1"), g.new_step("rpc", "2")
    a["target"]["devices"].append("r1")
    assert b["target"]["devices"] == []


# ---------------------------------------------------------------------------------------------- safety
def test_what_needs_confirmation():
    by_id = {s["id"]: s for s in sample()["steps"]}
    assert not g.needs_confirmation(by_id["d"])  # check (read-only)
    assert g.needs_confirmation(by_id["e"])  # write RPC
    assert g.needs_confirmation(by_id["g"]) and g.needs_confirmation(by_id["h"])
    assert not g.needs_confirmation(by_id["f"]) and not g.needs_confirmation(by_id["b"])
    assert not g.step_is_read_only(by_id["e"]) and not g.guide_is_read_only(sample())
    cli = {"id": "x", "type": "rpc", "title": "show", **probe({"command": "show version"})}
    assert g.step_is_read_only(cli) and not g.needs_confirmation(cli)
    assert g.step_is_read_only({"id": "x", "type": "rpc", "title": "t", **probe({"get-interface-information": {}})})


# ---------------------------------------------------------------------------------------------- files
def test_export_import_roundtrip():
    guide = sample()
    assert g.import_guide(g.export_guide(guide)) == guide
    assert json.loads(g.export_guide(guide))["schema_version"] == g.SCHEMA_VERSION


@pytest.mark.parametrize("text, msg", [
    ("nope", "Not a JSON"), ("[]", "JSON object"), ('{"schema_version": 99}', "schema_version"),
    ('{"schema_version": 1, "name": "x", "steps": []}', "no steps"),
])
def test_import_rejects(text, msg):
    with pytest.raises(GuideError, match=msg):
        g.import_guide(text)


def test_clip_output():
    small = {"a": 1}
    assert g.clip_output(small) == (small, False)
    clipped, was = g.clip_output({"a": "x" * 100}, limit=20)
    assert was and isinstance(clipped, str) and len(clipped) == 20


# ---------------------------------------------------------------------------------------------- runner
def test_runner_walks_through_the_guide():
    guide, st = sample(), r.new_state()
    seen = []
    while r.phase(guide, st) not in ("done", "aborted"):
        ph, step = r.phase(guide, st), r.current_step(guide, st)
        seen.append(ph)
        if ph == "input":
            r.answer(guide, st, step, "r1")
        else:
            r.complete(guide, st, step)
    assert seen == ["input", "text", "confirm", "auto", "approve", "auto", "approve", "approve"]
    assert st["values"] == {"dev": "r1"} and st["status"] == "done" and len(st["log"]) == 8
    assert r.current_step(guide, st) is None


def test_runner_failure_retry_skip_abort():
    guide, st = sample(), r.new_state({"dev": "r1"})
    st["index"] = 3
    step = r.current_step(guide, st)
    r.begin(st)
    assert r.phase(guide, st) == "running"
    r.fail(st, step, "version differs")
    assert r.phase(guide, st) == "failed" and st["error"] == "version differs"
    r.retry(st)
    assert st["status"] == "waiting" and st["index"] == 3 and st["log"][-1]["outcome"] == "failed"
    r.fail(st, step, "again")
    r.skip(guide, st, step)
    assert st["index"] == 4 and st["log"][-1]["outcome"] == "skipped" and st["error"] is None
    with pytest.raises(GuideError):
        r.retry(st)
    with pytest.raises(GuideError):
        r.skip(guide, st, step)
    r.abort(st)
    assert st["status"] == "aborted" and r.phase(guide, st) == "aborted"
    r.abort(st)  # harmless twice


def test_runner_answer_validation():
    guide = {"name": "x", "steps": [{"id": "a", "type": "input", "title": "t", "var": "v", "kind": "choice", "choices": ["x", "y"]}]}
    st = r.new_state()
    step = r.current_step(guide, st)
    for bad in ("", "  ", "z"):
        with pytest.raises(GuideError):
            r.answer(guide, st, step, bad)
    r.answer(guide, st, step, " y ")
    assert st["values"] == {"v": "y"} and st["status"] == "done"


def test_summarize_every_step_of_the_sample():
    lines = [g.summarize(s) for s in sample()["steps"]]
    assert lines[0] == "asks for {{dev}} (device)" and lines[1] == "Upgrading {{dev}}"
    assert "get-system-information on {{dev}}, 1 rule(s)" == lines[3] and lines[4].startswith("request-package-add on")
    assert lines[5] == "5 seconds" and lines[6] == "RECONNECT {{dev}}" and lines[7] == "config pull {{dev}}"
    cli = {"type": "rpc", "body": {"command": "show version"}, "target": {"devices": ["r1"], "groups": ["core"]}}
    assert g.summarize(cli) == "show version on r1, group core"
    until = {"type": "wait", "until": {**probe(), "timeout": 600}}
    assert "until get-system-information on {{dev}} passes (max 600 s)" == g.summarize(until)


def test_example_guides_in_the_repo_import():
    from pathlib import Path
    files = sorted(Path(__file__).parent.parent.joinpath("examples").glob("*.guide.json"))
    assert files
    for f in files:
        assert g.import_guide(f.read_text())["steps"], f


def test_reply_lines():
    assert g.reply_lines(None) == ([], False)
    assert g.reply_lines({"output": "a\nb\n"}) == (["a", "b"], False)  # CLI text as the device printed it
    assert g.reply_lines({"rpc-reply": {"output": "x"}}) == (["x"], False)
    assert g.reply_lines("cut text\nsecond") == (["cut text", "second"], False)
    lines, cut = g.reply_lines({"software-information": {"host-name": "r1", "up": True}})
    assert lines == ["software-information {", "    host-name r1;", "    up true;", "}"] and not cut
    assert g.reply_lines({"output": {"a": 1}})[0][0].startswith("output")  # not a string: rendered like config
