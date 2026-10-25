import asyncio

import pytest
from sqlalchemy.pool import StaticPool

from clixon_ui import accounts, actor, db, guide_exec, guides
from clixon_ui import guide_runner as runner
from clixon_ui.access import Access
from clixon_ui.client import RestconfError
from clixon_ui.guide import GuideError


class FakeClient:
    """Records what a run sends. `replies[rpc name]` is a list of per-call replies ({device: data}) used in order."""

    def __init__(self, replies=None):
        self.calls: list[tuple] = []
        self.replies = replies or {}
        self.tids = {}
        self.seen_actor = []
        self.hold: asyncio.Event | None = None  # when set, run_rpc waits for it (a slow device)
        self.tr_result = "SUCCESS"

    async def run_rpc(self, template=None, inline=None, variables=None, device=None, group=None):
        self.seen_actor.append(actor.acting.get())
        if self.hold:
            await self.hold.wait()
        name = next(iter(inline))
        self.calls.append(("rpc", name, inline[name], device or f"group {group}"))
        tid = len(self.tids) + 1
        queue = self.replies.get(name, [{}])
        self.tids[tid] = queue.pop(0) if len(queue) > 1 else queue[0]
        return tid

    async def wait_transaction(self, tid, timeout=120, interval=1.0, on_update=None):
        return {"result": self.tr_result, "reason": "device said no", "state": "DONE"}

    async def rpc_result(self, tid):
        reply = self.tids[tid]
        if isinstance(reply, Exception):
            raise reply
        return reply

    async def connection_change(self, device, operation):
        self.calls.append(("connection", device, operation))
        return 99

    async def config_pull(self, device, merge=False):
        self.calls.append(("pull", device, merge))
        return 98


async def no_sleep(_):
    await asyncio.sleep(0)


def target(*devices):
    return {"devices": list(devices), "groups": []}


def rpc_step(sid, body, devices=("r1",), **kw):
    return {"id": sid, "type": "rpc", "title": sid, "body": body, "target": target(*devices), **kw}


def check_step(sid, rules, body=None, devices=("r1",)):
    return {"id": sid, "type": "check", "title": sid, "body": body or {"get-version": {}}, "target": target(*devices), "rules": rules}


# ---------------------------------------------------------------------------------------------- execute_step
async def test_rpc_step_substitutes_variables_and_clips_output():
    c = FakeClient({"get-version": [{"r1": {"get-version": {"v": "1"}}}]})
    out = await guide_exec.execute_step(c, rpc_step("a", {"get-version": {"x": "{{v}}"}}, ("{{dev}}",)), {"v": "7", "dev": "r1"})
    assert out.ok and c.calls == [("rpc", "get-version", {"x": "7"}, "r1")]
    assert out.detail["results"][0]["output"] == {"get-version": {"v": "1"}}


async def test_rpc_step_lists_every_device_and_fails_if_one_errors():
    c = FakeClient({"get-x": [{"r1": {"a": 1}}, RestconfError("boom")]})
    out = await guide_exec.execute_step(c, rpc_step("a", {"get-x": {}}, ("r1", "r2")), {})
    assert not out.ok and [e["target"] for e in out.detail["results"]] == ["r1", "r2"]
    assert out.detail["results"][0]["ok"] and "error" in out.detail["results"][1]


async def test_missing_variable_is_an_error_not_a_literal():
    with pytest.raises(GuideError, match="{{dev}}"):
        await guide_exec.execute_step(FakeClient(), rpc_step("a", {"get-x": {}}, ("{{dev}}",)), {})


async def test_check_passes_and_fails_per_device():
    rules = [{"path": "get-version/v", "op": "equals", "value": "1"}]
    good = FakeClient({"get-version": [{"r1": {"get-version": {"v": "1"}}}]})
    assert (await guide_exec.execute_step(good, check_step("c", rules), {})).ok
    bad = FakeClient({"get-version": [{"r1": {"get-version": {"v": "2"}}}]})
    out = await guide_exec.execute_step(bad, check_step("c", rules), {})
    assert not out.ok and "Check failed" in out.error and "'2'" in out.error
    assert out.detail["results"][0]["rules"][0]["ok"] is False


async def test_check_needs_every_device_to_pass():
    rules = [{"path": "v", "op": "equals", "value": "1"}]
    c = FakeClient({"get-version": [{"r1": {"v": "1"}}, {"r2": {"v": "2"}}]})
    out = await guide_exec.execute_step(c, check_step("c", rules, devices=("r1", "r2")), {})
    assert not out.ok and [e["ok"] for e in out.detail["results"]] == [True, False]


async def test_wait_seconds():
    ticks = []

    async def sleep(s):
        ticks.append(s)

    out = await guide_exec.execute_step(FakeClient(), {"id": "w", "type": "wait", "title": "w", "seconds": 3}, {}, sleep=sleep)
    assert out.ok and ticks == [1, 1, 1]


async def test_wait_until_polls_until_the_rule_holds():
    rules = [{"path": "up", "op": "equals", "value": "true"}]
    c = FakeClient({"get-x": [RestconfError("rebooting"), {"r1": {"up": False}}, {"r1": {"up": True}}]})
    step = {"id": "w", "type": "wait", "title": "w", "until": {**rpc_step("p", {"get-x": {}}), "rules": rules, "interval": 0.001, "timeout": 60}}
    out = await guide_exec.execute_step(c, step, {}, sleep=no_sleep)
    assert out.ok and out.detail["polls"] == 3


async def test_wait_until_times_out():
    rules = [{"path": "up", "op": "equals", "value": "true"}]
    c = FakeClient({"get-x": [{"r1": {"up": False}}]})
    step = {"id": "w", "type": "wait", "title": "w", "until": {**rpc_step("p", {"get-x": {}}), "rules": rules, "interval": 0.01, "timeout": 0.05}}
    out = await guide_exec.execute_step(c, step, {}, sleep=asyncio.sleep)
    assert not out.ok and "Not satisfied" in out.error and "false" in out.error


async def test_connection_and_pull_and_failed_transaction():
    c = FakeClient()
    out = await guide_exec.execute_step(c, {"id": "g", "type": "connection", "title": "t", "operation": "RECONNECT", "device": "{{d}}"}, {"d": "r1"})
    assert out.ok and c.calls == [("connection", "r1", "RECONNECT")]
    assert (await guide_exec.execute_step(c, {"id": "h", "type": "pull", "title": "t", "device": "*", "merge": True}, {})).ok
    assert c.calls[-1] == ("pull", "*", True)
    c.tr_result = "FAILED"
    out = await guide_exec.execute_step(c, {"id": "h", "type": "pull", "title": "t", "device": "r1"}, {})
    assert not out.ok and out.error == "device said no"


# ---------------------------------------------------------------------------------------------- run controller
@pytest.fixture
def env():
    engine = db.create_engine("sqlite://", poolclass=StaticPool)
    db.migrate(engine)
    factory = lambda: db.Session(engine, expire_on_commit=False)  # noqa: E731
    with factory() as s:
        accounts.seed_admin(s)
        user, _ = accounts.create(s, "ola")
        access = accounts.access_for(user)
    guide_exec._active.clear()
    yield factory, access
    guide_exec._active.clear()


def guide_body():
    return {"name": "Upgrade", "description": "", "steps": [
        {"id": "i", "type": "input", "title": "Device", "var": "dev", "label": "Device", "kind": "device"},
        {"id": "t", "type": "text", "title": "Read me", "text": "go {{dev}}"},
        check_step("c", [{"path": "get-version/v", "op": "equals", "value": "1"}], devices=("{{dev}}",)),
        rpc_step("w", {"request-package-add": {"package-name": "x"}}, ("{{dev}}",)),
        {"id": "g", "type": "connection", "title": "Reconnect", "operation": "RECONNECT", "device": "{{dev}}"},
    ]}


def make_run(factory, access, client, guide=None, sleep=no_sleep):
    with factory() as s:
        row = guides.save(s, guide or guide_body(), "admin")
        run = guides.start_run(s, row, access)
    ctl = guide_exec.RunController(run.id, run.guide, run.username, run.state, client, factory, sleep)
    guide_exec._active[run.id] = ctl
    return ctl


async def settle(ctl):
    while ctl.busy:
        await asyncio.sleep(0)
        if ctl.task and ctl.task.done():
            break
    if ctl.task:
        await ctl.task


def stored(factory, ctl):
    with factory() as s:
        return guides.get_run(s, ctl.run_id)


async def test_run_end_to_end_confirms_before_writes(env):
    factory, access = env
    c = FakeClient({"get-version": [{"r1": {"get-version": {"v": "1"}}}]})
    ctl = make_run(factory, access, c)
    assert ctl.phase == "input"
    ctl.answer("r1")
    assert ctl.phase == "text"
    ctl.acknowledge()  # the check is read-only: it runs by itself
    await settle(ctl)
    assert ctl.phase == "approve" and ctl.step["id"] == "w"
    assert [x[0] for x in c.calls] == ["rpc"] and c.calls[0][1] == "get-version"  # the write has NOT been sent
    ctl.approve()
    await settle(ctl)
    assert ctl.phase == "approve" and ctl.step["id"] == "g"
    assert [x[1] for x in c.calls] == ["get-version", "request-package-add"]
    ctl.approve()
    await settle(ctl)
    assert ctl.phase == "done" and c.calls[-1] == ("connection", "r1", "RECONNECT")
    run = stored(factory, ctl)
    assert run.status == "done" and run.finished_at and [e["outcome"] for e in run.state["log"]] == ["ok"] * 5
    assert run.state["values"] == {"dev": "r1"}


async def test_a_wrong_action_is_refused_and_sends_nothing(env):
    factory, access = env
    c = FakeClient()
    ctl = make_run(factory, access, c)
    with pytest.raises(GuideError):
        ctl.approve()  # the step is an input
    with pytest.raises(GuideError):
        ctl.acknowledge()
    ctl.answer("r1")
    ctl.acknowledge()
    await settle(ctl)
    with pytest.raises(GuideError):
        ctl.answer("x")
    assert c.calls == [("rpc", "get-version", {}, "r1")]  # only the read-only check went out


async def test_failed_check_stops_the_run_and_retry_asks_again(env):
    factory, access = env
    c = FakeClient({"get-version": [{"r1": {"get-version": {"v": "2"}}}, {"r1": {"get-version": {"v": "1"}}}]})
    ctl = make_run(factory, access, c)
    ctl.answer("r1")
    ctl.acknowledge()
    await settle(ctl)
    assert ctl.phase == "failed" and "Check failed" in ctl.state["error"]
    assert stored(factory, ctl).status == "failed"
    ctl.retry()  # a check runs again by itself; the second answer passes
    await settle(ctl)
    assert ctl.phase == "approve" and ctl.step["id"] == "w"


async def test_skip_a_failed_step(env):
    factory, access = env
    c = FakeClient({"get-version": [{"r1": {"get-version": {"v": "2"}}}]})
    ctl = make_run(factory, access, c)
    ctl.answer("r1")
    ctl.acknowledge()
    await settle(ctl)
    ctl.skip()
    assert ctl.step["id"] == "w" and ctl.state["log"][-1]["outcome"] == "skipped"


async def test_run_keeps_going_without_a_page_and_acts_as_its_user(env):
    factory, access = env
    c = FakeClient({"get-version": [{"r1": {"get-version": {"v": "1"}}}]})
    ctl = make_run(factory, access, c)
    ctl.answer("r1")
    ctl.acknowledge()
    await settle(ctl)
    assert c.seen_actor and all(a == (access.user_id, "ola") for a in c.seen_actor)
    assert actor.acting.get() is None  # nothing leaks out of the task


async def test_abort_during_a_step(env):
    factory, access = env
    c = FakeClient()
    c.hold = asyncio.Event()  # a device that never answers
    ctl = make_run(factory, access, c)
    ctl.answer("r1")
    ctl.acknowledge()
    await asyncio.sleep(0.01)
    assert ctl.busy and ctl.phase == "running"
    ctl.abort()
    await asyncio.sleep(0.01)
    assert not ctl.busy and ctl.phase == "aborted"
    run = stored(factory, ctl)
    assert run.status == "aborted" and run.state["log"][-1]["outcome"] == "aborted" and run.finished_at


async def test_restart_while_running_never_repeats_a_step(env):
    factory, access = env
    ctl = make_run(factory, access, FakeClient())
    ctl.answer("r1")
    ctl.acknowledge()
    await settle(ctl)
    state = dict(stored(factory, ctl).state)
    state["status"] = "running"  # as if the process died while the step ran
    with factory() as s:
        guides.save_state(s, guides.get_run(s, ctl.run_id), state)
    guide_exec._active.clear()
    again = guide_exec.controller_for(ctl.run_id, FakeClient(), factory)
    assert again.phase == "failed" and "restarted" in again.state["error"]
    assert stored(factory, ctl).status == "failed"


async def test_disabled_user_cannot_continue_a_run(env):
    factory, access = env
    c = FakeClient({"get-version": [{"r1": {"get-version": {"v": "1"}}}]})
    ctl = make_run(factory, access, c)
    with factory() as s:
        u = accounts.get(s, "ola")
        u.disabled = True
        s.commit()
    ctl.answer("r1")
    ctl.acknowledge()
    await settle(ctl)
    assert ctl.phase == "failed" and "cannot act" in ctl.state["error"] and c.calls == []


# ---------------------------------------------------------------------------------------------- guides.py
def test_guides_crud_versions_and_permissions(env):
    factory, _ = env
    with factory() as s:
        row = guides.save(s, guide_body(), "admin")
        assert row.version == 1 and not row.read_only
        guide = guides.body(row)
        guide["description"] = "only text changed"
        assert guides.save(s, guide, "admin", row.id).version == 1
        guide["steps"] = guide["steps"][:3]
        again = guides.save(s, guide, "admin", row.id)
        assert again.version == 2 and again.read_only is True  # the first three steps only read
        assert [x.name for x in guides.list_guides(s)] == ["Upgrade"]
        with pytest.raises(GuideError, match="already exists"):
            guides.save(s, guide_body(), "admin")
        with pytest.raises(GuideError, match="name"):
            guides.save(s, {**guide_body(), "name": " "}, "admin")
        view = Access(2, "v", view_only=True)
        assert guides.can_run(view, row) is None  # now read-only
        full = guides.save(s, {**guide_body(), "name": "Full"}, "admin")
        assert "view-only" in guides.can_run(view, full)
        with pytest.raises(GuideError):
            guides.start_run(s, full, view)


def test_deleting_a_guide_keeps_its_runs(env):
    factory, access = env
    with factory() as s:
        row = guides.save(s, guide_body(), "admin")
        run = guides.start_run(s, row, access)
        assert [r.id for r in guides.list_runs(s, guide_id=row.id)] == [run.id]
        guides.delete(s, row)
        kept = guides.get_run(s, run.id)
        assert kept.guide_id is None and kept.guide["name"] == "Upgrade" and guides.list_guides(s) == []
        assert guides.unfinished_runs(s)[0].id == run.id and guides.list_runs(s, username="ola")[0].id == run.id
