"""State machine of one guide run. Pure: the state is a JSON-able dict (it is stored after every change), and the
functions here only decide what comes next. `guide_exec` talks to the controller; `guides` stores the state.

status: waiting (the step at `index` needs the operator or is ready to run), running (the engine is executing it),
failed (the step failed: retry, skip or abort), done, aborted.
"""

from __future__ import annotations

from typing import Any

from .guide import GuideError, needs_confirmation

FINISHED = ("done", "aborted")


def new_state(values: dict[str, str] | None = None) -> dict:
    return {"status": "waiting", "index": 0, "values": dict(values or {}), "log": [], "error": None}


def current_step(guide: dict, state: dict) -> dict | None:
    steps = guide["steps"]
    return steps[state["index"]] if state["status"] not in FINISHED and state["index"] < len(steps) else None


def phase(guide: dict, state: dict) -> str:
    """What the runner shows now: text | confirm | input | approve (a write, ask before running) | auto (runs by
    itself) | running | failed | done | aborted."""
    if state["status"] in ("running", "failed", *FINISHED):
        return state["status"]
    step = current_step(guide, state)
    if step is None:
        return "done"
    if step["type"] in ("text", "confirm", "input"):
        return step["type"]
    return "approve" if needs_confirmation(step) else "auto"


def _advance(guide: dict, state: dict) -> None:
    state["index"] += 1
    state["error"] = None
    state["status"] = "done" if state["index"] >= len(guide["steps"]) else "waiting"


def _entry(step: dict, outcome: str, detail: dict) -> dict:
    return {"step": step["id"], "type": step["type"], "title": step["title"], "outcome": outcome, **detail}


def begin(state: dict) -> None:
    """The engine starts executing the current step."""
    state["status"] = "running"


def complete(guide: dict, state: dict, step: dict, **detail: Any) -> None:
    """The step succeeded (or the operator acknowledged it): log it and move on."""
    state["log"].append(_entry(step, "ok", detail))
    _advance(guide, state)


def fail(state: dict, step: dict, error: str, **detail: Any) -> None:
    """The step failed. The run stops here until the operator retries, skips or aborts."""
    state["log"].append(_entry(step, "failed", {"error": error, **detail}))
    state["status"], state["error"] = "failed", error


def retry(state: dict) -> None:
    if state["status"] != "failed":
        raise GuideError("Only a failed step can be retried.")
    state["status"], state["error"] = "waiting", None


def skip(guide: dict, state: dict, step: dict) -> None:
    if state["status"] != "failed":
        raise GuideError("Only a failed step can be skipped.")
    state["log"].append(_entry(step, "skipped", {}))
    _advance(guide, state)


def abort(state: dict) -> None:
    if state["status"] not in FINISHED:
        state["status"], state["error"] = "aborted", None


def answer(guide: dict, state: dict, step: dict, value: str) -> None:
    """The operator answered an input step. Raises GuideError when the answer is not acceptable."""
    value = str(value).strip()
    if not value:
        raise GuideError("Enter a value.")
    if step["kind"] == "choice" and value not in step["choices"]:
        raise GuideError(f"Choose one of: {', '.join(step['choices'])}.")
    state["values"][step["var"]] = value
    complete(guide, state, step, var=step["var"], value=value)
