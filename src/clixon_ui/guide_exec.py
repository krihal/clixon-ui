"""Executing guide steps against the controller, and the server-side controller of one run. No NiceGUI.

`execute_step` does the work of one step and reports what happened. `RunController` owns a run: it applies the
operator's actions (acknowledge, answer, approve, retry, skip, abort) to the `guide_runner` state, runs steps in a
background task that keeps going when the browser page is closed, and stores the state after every change.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.orm import Session

from . import accounts, actor
from . import guide as g
from . import guide_runner as runner
from . import guides
from .client import PermissionDenied, RestconfError
from .guide import GuideError
from .rpcrun import run_on_targets

CONTROLLER_TIMEOUT = 300  # seconds to wait for one controller transaction (connection change, config pull)


@dataclass
class Outcome:
    ok: bool
    error: str | None = None
    detail: dict = field(default_factory=dict)


# ------------------------------------------------------------------------------------------------ one step
async def _probe(client, probe: dict, values: dict[str, str], rules: list[dict] | None, show: list[dict] | None = None,
                 failure: list[dict] | None = None, fold: bool = False) -> tuple[bool, list[dict], str | None]:
    """Run the probe's RPC on its targets and check every reply: `rules` must all hold, none of the `failure` rules may hold.
    `fold`: the page shows the reply folded away (a check: the rules are the point). Returns (all ok, one entry per target,
    first problem as text)."""
    body = g.fill(probe["body"], values)
    devices, groups = (g.fill(x, values) for x in g.targets_of(probe))
    done = await run_on_targets(client, {"inline": body, "label": next(iter(body))}, devices, groups)
    entries: list[dict] = []
    for label, res in done:
        if "error" in res:
            entries.append({"target": label, "ok": False, "error": res["error"]})
            continue
        for name, data in (res["replies"] or {}).items() or [(label, None)]:
            output, truncated = g.clip_output(data)
            entry: dict[str, Any] = {"target": name, "ok": True, "output": output, "truncated": truncated, "fold": fold}
            if show and data is not None:
                entry["summary"] = g.summary(data, show)  # from the full reply: the log keeps only the first 64 KB of it
            if rules is not None:
                results = g.evaluate(rules, data, values)
                entry["rules"] = [{"text": r.describe(), "ok": r.ok} for r in results]
                entry["ok"] = all(r.ok for r in results)
            if failure:
                bad = g.evaluate(failure, data, values)
                entry["rules"] = entry.get("rules", []) + [{"text": f"Failed if: {r.describe()}", "ok": not r.ok} for r in bad]
                entry["ok"] = entry["ok"] and not any(r.ok for r in bad)
            entries.append(entry)
    problem = next((f"{e['target']}: {e['error']}" for e in entries if e.get("error")), None) or \
        next((f"{e['target']}: {r['text']}" for e in entries for r in e.get("rules", []) if not r["ok"]), None)
    return all(e["ok"] for e in entries) and bool(entries), entries, problem


async def _transaction(client, tid: int | None) -> str | None:
    """None when the controller transaction succeeded, else the reason."""
    if tid is None:
        return None  # the controller had nothing to do (no matching device)
    tr = await client.wait_transaction(tid, timeout=CONTROLLER_TIMEOUT, interval=1.0)
    if not tr or tr.get("result") != "SUCCESS":
        return ((tr or {}).get("reason") or "failed").strip() or "failed"
    return None


async def execute_step(client, step: dict, values: dict[str, str], progress: Callable[[str], None] = lambda _: None,
                       sleep=asyncio.sleep) -> Outcome:
    """Do the work of an rpc, check, wait, connection or pull step. Raises GuideError for a missing variable."""
    t = step["type"]
    if t == "rpc":
        ok, entries, problem = await _probe(client, step, values, step.get("success") or None, step.get("show"), step.get("failure") or None)
        return Outcome(ok, None if ok else (problem or "No reply."), {"results": entries})
    if t == "check":
        ok, entries, problem = await _probe(client, step, values, step["rules"], fold=True)
        return Outcome(ok, None if ok else f"Check failed. {problem or 'No reply.'}", {"results": entries})
    if t == "wait":
        return await _wait(client, step, values, progress, sleep)
    if t == "connection":
        device = g.fill(step["device"], values)
        why = await _transaction(client, await client.connection_change(device, step["operation"]))
        return Outcome(why is None, why, {"device": device, "operation": step["operation"]})
    if t == "pull":
        device = g.fill(step["device"], values)
        why = await _transaction(client, await client.config_pull(device, bool(step.get("merge"))))
        return Outcome(why is None, why, {"device": device})
    raise GuideError(f"{t} steps are not executed.")


async def _wait(client, step: dict, values: dict[str, str], progress, sleep) -> Outcome:
    until = step.get("until")
    if not until:
        seconds = min(int(step["seconds"]), g.MAX_WAIT_SECONDS)
        for left in range(seconds, 0, -1):
            progress(f"Waiting, {left} s left")
            await sleep(1)
        return Outcome(True, None, {"waited": seconds})
    interval, timeout = float(until.get("interval", 10)), min(float(until["timeout"]), g.MAX_WAIT_SECONDS)
    start, tries, problem = time.monotonic(), 0, None
    while True:
        tries += 1
        try:  # a device that is still rebooting answers with errors: that is "not yet", not a failure
            ok, entries, problem = await _probe(client, until, values, until["rules"], fold=True)
        except (RestconfError, TimeoutError) as e:
            ok, entries, problem = False, [], str(e)
        elapsed = time.monotonic() - start
        if ok:
            return Outcome(True, None, {"polls": tries, "seconds": round(elapsed), "results": entries})
        if elapsed + interval > timeout:
            return Outcome(False, f"Not satisfied after {round(elapsed)} s ({tries} tries). Last: {problem or 'no reply'}",
                           {"polls": tries, "results": entries})
        progress(f"Polling, try {tries}, {round(timeout - elapsed)} s left. Last: {problem or 'no reply'}")
        await sleep(interval)


# ------------------------------------------------------------------------------------------------ one run
_active: dict[int, RunController] = {}


def controller_for(run_id: int, client, factory: Callable[[], Session]) -> RunController | None:
    """The live controller of a run (made on first use, also after a restart of the UI)."""
    if run_id not in _active:
        with factory() as db:
            run = guides.get_run(db, run_id)
            if run is None:
                return None
            _active[run_id] = RunController(run_id, run.guide, run.username, run.state, client, factory)
    return _active[run_id]


class RunController:
    def __init__(self, run_id: int, guide: dict, username: str, state: dict, client, factory: Callable[[], Session],
                 sleep=asyncio.sleep) -> None:
        self.run_id, self.guide, self.username, self.state = run_id, guide, username, state
        self.client, self.factory, self.sleep = client, factory, sleep
        self.rev = 0  # grows with every change: pages poll this to know when to redraw
        self.progress = ""
        self.task: asyncio.Task | None = None
        step = runner.current_step(guide, state)
        if state["status"] == "running" and step:  # the UI went away mid-step: never repeat a write on our own
            runner.fail(state, step, "The UI was restarted while this step was running. Check the result on the device, "
                                     "then retry or skip it.")
            self._save()

    # -- read
    @property
    def phase(self) -> str:
        return runner.phase(self.guide, self.state)

    @property
    def step(self) -> dict | None:
        return runner.current_step(self.guide, self.state)

    @property
    def busy(self) -> bool:
        return self.task is not None and not self.task.done()

    # -- operator actions
    def acknowledge(self) -> None:
        """Next on an instruction or a confirmation."""
        self._expect("text", "confirm")
        runner.complete(self.guide, self.state, self.step)
        self._changed()

    def answer(self, value: str) -> None:
        self._expect("input")
        runner.answer(self.guide, self.state, self.step, value)
        self._changed()

    def approve(self) -> None:
        """The operator confirmed a write: run it now."""
        self._expect("approve")
        self._start(approved=True)

    def kick(self) -> None:
        """Start whatever can run by itself (a page that was just opened, or a run just resumed after a restart)."""
        self._start(approved=False)

    def retry(self) -> None:
        runner.retry(self.state)
        self._changed()

    def skip(self) -> None:
        runner.skip(self.guide, self.state, self.step)
        self._changed()

    def abort(self) -> None:
        """Stop the run. A controller transaction that is already under way cannot be recalled; we stop waiting for it."""
        was_running = self.busy
        if was_running:
            self.task.cancel()
        step = self.step
        runner.abort(self.state)
        if was_running and step:
            self.state["log"].append({"step": step["id"], "type": step["type"], "title": step["title"], "outcome": "aborted",
                                      "note": "Aborted while the step was running; the controller may still finish it."})
        self._save()
        self.rev += 1

    # -- internals
    def _expect(self, *phases: str) -> None:
        if self.phase not in phases:
            raise GuideError("That is not possible at this point of the guide.")

    def _changed(self) -> None:
        self._save()
        self.rev += 1
        self._start(approved=False)

    def _save(self) -> None:
        self.state["progress"] = self.progress
        with self.factory() as db:
            guides.save_state(db, guides.get_run(db, self.run_id), self.state)

    def _start(self, *, approved: bool) -> None:
        if self.busy or not (self.phase == "auto" or (approved and self.phase == "approve")):
            return
        self.task = asyncio.get_running_loop().create_task(self._drive(approved))

    async def _drive(self, approved: bool) -> None:
        """Run steps until one needs the operator again (or fails)."""
        while self.phase == "auto" or (approved and self.phase == "approve"):
            approved = False
            await self._execute(self.step)
            if self.state["status"] != "waiting":
                return

    def _set_progress(self, text: str) -> None:
        self.progress = text  # not `rev`: pages show it in a label of its own instead of redrawing

    async def _execute(self, step: dict) -> None:
        runner.begin(self.state)
        self.progress = ""
        self._save()
        self.rev += 1
        try:
            with self.factory() as db:
                user = accounts.get(db, self.username)
            if user is None or user.disabled:
                raise GuideError(f"The user {self.username} cannot act any more (removed or disabled).")
            with actor.acting_as(user.id, user.username):
                out = await execute_step(self.client, step, self.state["values"], self._set_progress, self.sleep)
        except asyncio.CancelledError:
            raise  # abort() has set the state
        except (GuideError, PermissionDenied, RestconfError, TimeoutError) as e:
            out = Outcome(False, str(e) or type(e).__name__)
        except Exception as e:  # a bug must stop the run visibly, not leave it "running" forever
            out = Outcome(False, f"Unexpected error: {type(e).__name__}: {e}")
        self.progress = ""
        if out.ok:
            runner.complete(self.guide, self.state, step, **out.detail)
        else:
            runner.fail(self.state, step, out.error or "failed", **out.detail)
        self._save()
        self.rev += 1
