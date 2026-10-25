"""Guides: step-by-step procedures built from RPC calls, instructions, questions and checks. Pure (no NiceGUI, no I/O).

A guide is JSON: {"schema_version": 1, "name", "description", "steps": [step, ...]}. Every step has an `id`
(unique), a `type` and a `title`; the rest depends on the type (see STEP_TYPES). Text fields may use `{{var}}`,
filled from `input` steps that come earlier in the guide.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from .confview import to_lines
from .rpcutil import inline_is_read_only

SCHEMA_VERSION = 1
MAX_WAIT_SECONDS = 1800  # a wait or a poll never blocks a run for longer than this
OUTPUT_LIMIT = 64 * 1024  # bytes of RPC output kept per target in the run log
OPERATORS = {
    "equals": "equals", "not-equals": "does not equal", "contains": "contains",
    "regex": "matches regex", "exists": "exists", "not-exists": "does not exist",
}
INPUT_KINDS = ("text", "device", "group", "choice")
CONNECTION_OPERATIONS = ("OPEN", "CLOSE", "RECONNECT")

# type -> (label, icon)
STEP_TYPES = {
    "text": ("Instruction", "notes"),
    "confirm": ("Confirmation", "task_alt"),
    "input": ("Input", "edit_note"),
    "rpc": ("RPC call", "terminal"),
    "check": ("Check", "rule"),
    "wait": ("Wait", "hourglass_empty"),
    "connection": ("Connection", "link"),
    "pull": ("Config pull", "download"),
}

_VAR = re.compile(r"\{\{\s*(\w+)\s*\}\}")


class GuideError(Exception):
    """The guide (or an import file) is not valid; the message says why."""


# ------------------------------------------------------------------------------------------------ variables
def variables_in(obj: Any) -> list[str]:
    """Names used as {{var}} anywhere in `obj` (strings, nested), in order of first use."""
    found: list[str] = []

    def walk(o: Any) -> None:
        if isinstance(o, str):
            found.extend(n for n in _VAR.findall(o) if n not in found)
        elif isinstance(o, dict):
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)

    walk(obj)
    return found


def fill(obj: Any, values: dict[str, str]) -> Any:
    """Copy of `obj` with every {{var}} replaced. A variable without a value is an error: sending the
    literal text "{{x}}" to a device would be worse."""
    if isinstance(obj, str):
        def sub(m: re.Match) -> str:
            name = m.group(1)
            if name not in values:
                raise GuideError(f"Variable {{{{{name}}}}} has no value.")
            return str(values[name])
        return _VAR.sub(sub, obj)
    if isinstance(obj, dict):
        return {k: fill(v, values) for k, v in obj.items()}
    if isinstance(obj, list):
        return [fill(v, values) for v in obj]
    return obj


# ------------------------------------------------------------------------------------------------ rules
@dataclass
class RuleResult:
    path: str
    op: str
    expected: str
    found: list[str]
    ok: bool

    def describe(self) -> str:
        what = f"{self.path} {OPERATORS[self.op]}" + ("" if self.op in ("exists", "not-exists") else f" {self.expected!r}")
        return f"{what} (found: {', '.join(map(repr, self.found)) or 'nothing'})"


def _local(key: str) -> str:
    return key.rpartition(":")[2]


def _text(v: Any) -> str:
    if v is True:
        return "true"
    if v is False:
        return "false"
    return v if isinstance(v, str) else json.dumps(v, sort_keys=True)


def lookup(data: Any, path: str) -> list[Any]:
    """Values at a `/`-separated path. A key matches with or without its module prefix; a number indexes a list;
    any other key on a list looks in every element (one `lookup` can return several values)."""
    nodes = [data]
    for part in (p for p in path.split("/") if p):
        nxt: list[Any] = []
        for n in nodes:
            if isinstance(n, dict):
                nxt.extend(v for k, v in n.items() if k == part or _local(k) == part)
            elif isinstance(n, list) and part.isdigit():
                nxt.extend(n[int(part):int(part) + 1])
            elif isinstance(n, list):
                for item in n:
                    if isinstance(item, dict):
                        nxt.extend(v for k, v in item.items() if k == part or _local(k) == part)
        nodes = nxt
    out: list[Any] = []
    for n in nodes:  # a leaf-list or repeated element counts as several values
        out.extend(n if isinstance(n, list) else [n])
    return out


def evaluate(rules: list[dict], data: Any, values: dict[str, str] | None = None) -> list[RuleResult]:
    """Apply every rule to one reply. Positive operators pass when any found value matches; the negative ones
    (`not-equals`, `not-exists`) pass when none does."""
    results = []
    for rule in rules:
        path, op = rule["path"], rule["op"]
        expected = fill(str(rule.get("value", "")), values or {})
        found = [_text(v) for v in lookup(data, path)]
        if op == "equals":
            ok = expected in found
        elif op == "not-equals":
            ok = bool(found) and expected not in found
        elif op == "contains":
            ok = any(expected in f for f in found)
        elif op == "regex":
            ok = any(re.search(expected, f) for f in found)
        elif op == "exists":
            ok = bool(found)
        else:  # not-exists
            ok = not found
        results.append(RuleResult(path, op, expected, found, ok))
    return results


# ------------------------------------------------------------------------------------------------ steps
def new_step(type_: str, step_id: str) -> dict:
    """An empty step of `type_` with sensible defaults, for the editor."""
    base = {"id": step_id, "type": type_, "title": STEP_TYPES[type_][0]}
    target = {"devices": [], "groups": []}
    extra: dict[str, Any] = {
        "text": {"text": ""},
        "confirm": {"question": ""},
        "input": {"var": "", "label": "", "kind": "text", "default": "", "choices": []},
        "rpc": {"body": {}, "target": target, "show": [], "success": [], "failure": []},
        "check": {"body": {}, "target": target, "rules": []},
        "wait": {"seconds": 60},
        "connection": {"operation": "RECONNECT", "device": ""},
        "pull": {"device": "", "merge": False},
    }[type_]
    return {**base, **json.loads(json.dumps(extra))}


def step_is_read_only(step: dict) -> bool:
    """True when running the step cannot change anything on a device or the controller."""
    t = step["type"]
    if t in ("rpc", "check"):
        return inline_is_read_only(step.get("body"))
    if t == "wait":
        return "until" not in step or inline_is_read_only(step["until"].get("body"))
    return t not in ("connection", "pull")


def guide_is_read_only(guide: dict) -> bool:
    return all(step_is_read_only(s) for s in guide.get("steps", []))


def needs_confirmation(step: dict) -> bool:
    """Runner asks before executing. Not up to the author: a write is always confirmed."""
    return step["type"] in ("rpc", "connection", "pull") and not step_is_read_only(step)


def targets_of(step_or_probe: dict) -> tuple[list[str], list[str]]:
    t = step_or_probe.get("target") or {}
    return list(t.get("devices") or []), list(t.get("groups") or [])


def validate(guide: dict) -> list[str]:
    """Everything wrong with `guide`, as sentences for the author. Empty list = fine."""
    errors: list[str] = []
    if not str(guide.get("name", "")).strip():
        errors.append("The guide needs a name.")
    steps = guide.get("steps")
    if not isinstance(steps, list) or not steps:
        return errors + ["The guide has no steps."]
    seen_ids: set[str] = set()
    defined: set[str] = set()  # variables set by input steps so far
    for n, s in enumerate(steps, 1):
        where = f"Step {n}" + (f" ({s.get('title')})" if s.get("title") else "")
        t = s.get("type")
        if t not in STEP_TYPES:
            errors.append(f"{where}: unknown type {t!r}.")
            continue
        sid = s.get("id")
        if not sid or sid in seen_ids:
            errors.append(f"{where}: needs a unique id.")
        seen_ids.add(sid)
        errors += [f"{where}: {e}" for e in _validate_step(s, defined)]
        if t == "input" and s.get("var"):
            defined.add(s["var"])
    return errors


def _probe_errors(probe: dict, defined: set[str], *, read_only: bool, rules: bool) -> list[str]:
    errs: list[str] = []
    body = probe.get("body")
    if not isinstance(body, dict) or len(body) != 1:
        errs.append("pick the RPC to run.")
    elif read_only and not inline_is_read_only(body):
        errs.append("only read-only RPCs (get-*, show ...) can be used here.")
    devices, groups = targets_of(probe)
    if not devices and not groups:
        errs.append("choose at least one device or group.")
    if rules:
        errs += _rule_errors(probe.get("rules"))
    errs += [f"uses {{{{{v}}}}} before an input step sets it." for v in variables_in(probe) if v not in defined]
    return errs


MAX_SHOW_FIELDS = 30


def _show_errors(show: Any) -> list[str]:
    if not show:
        return []
    if not isinstance(show, list) or len(show) > MAX_SHOW_FIELDS:
        return [f"show at most {MAX_SHOW_FIELDS} fields."]
    return ["a field to show has no path."] if any(not str(f.get("path", "")).strip("/ ") for f in show) else []


def _rule_errors(rules: Any) -> list[str]:
    if not rules:
        return ["add at least one rule."]
    errs = []
    for r in rules:
        if not str(r.get("path", "")).strip("/ "):
            errs.append("a rule has no path.")
        if r.get("op") not in OPERATORS:
            errs.append(f"unknown operator {r.get('op')!r}.")
        elif r["op"] == "regex":
            try:
                re.compile(str(r.get("value", "")))
            except re.error as e:
                errs.append(f"bad regex {r.get('value')!r}: {e}.")
    return errs


def _validate_step(s: dict, defined: set[str]) -> list[str]:
    t = s["type"]
    errs: list[str] = []
    if not str(s.get("title", "")).strip():
        errs.append("needs a title.")
    if t == "text" and not str(s.get("text", "")).strip():
        errs.append("write the instruction text.")
    elif t == "confirm" and not str(s.get("question", "")).strip():
        errs.append("write the question.")
    elif t == "input":
        if not re.fullmatch(r"\w+", str(s.get("var", ""))):
            errs.append("the variable name must be letters, digits and underscore.")
        if s.get("kind") not in INPUT_KINDS:
            errs.append(f"unknown input kind {s.get('kind')!r}.")
        if s.get("kind") == "choice" and not s.get("choices"):
            errs.append("list the choices.")
    elif t == "rpc":
        errs += _probe_errors(s, defined, read_only=False, rules=False)
        errs += _show_errors(s.get("show"))
        errs += [f"succeeded when: {e}" for e in _rule_errors(s["success"])] if s.get("success") else []
        errs += [f"failed when: {e}" for e in _rule_errors(s["failure"])] if s.get("failure") else []
    elif t == "check":
        errs += _probe_errors(s, defined, read_only=True, rules=True)
    elif t == "wait":
        until = s.get("until")
        if until:
            errs += _probe_errors(until, defined, read_only=True, rules=True)
            if not 1 <= int(until.get("interval", 10)) <= MAX_WAIT_SECONDS:
                errs.append("the poll interval is out of range.")
            if not 1 <= int(until.get("timeout", 0)) <= MAX_WAIT_SECONDS:
                errs.append(f"set a timeout of at most {MAX_WAIT_SECONDS} seconds.")
        elif not 1 <= int(s.get("seconds", 0)) <= MAX_WAIT_SECONDS:
            errs.append(f"wait between 1 and {MAX_WAIT_SECONDS} seconds.")
    elif t == "connection":
        if s.get("operation") not in CONNECTION_OPERATIONS:
            errs.append("choose OPEN, CLOSE or RECONNECT.")
        errs += _device_errors(s, defined)
    elif t == "pull":
        errs += _device_errors(s, defined)
    if t in ("text", "confirm"):
        errs += [f"uses {{{{{v}}}}} before an input step sets it." for v in variables_in(s) if v not in defined]
    return errs


def _device_errors(s: dict, defined: set[str]) -> list[str]:
    errs = [] if str(s.get("device", "")).strip() else ["choose a device (or * for all)."]
    return errs + [f"uses {{{{{v}}}}} before an input step sets it." for v in variables_in(s) if v not in defined]


# ------------------------------------------------------------------------------------------------ files
def export_guide(guide: dict) -> str:
    """The guide as a JSON file's text."""
    body = {"schema_version": SCHEMA_VERSION, "name": guide["name"], "description": guide.get("description", ""),
            "steps": guide["steps"]}
    return json.dumps(body, indent=2, ensure_ascii=False) + "\n"


def import_guide(text: str) -> dict:
    """Parse and check a guide file. Raises GuideError with all problems found."""
    try:
        body = json.loads(text)
    except ValueError as e:
        raise GuideError(f"Not a JSON file: {e}") from e
    if not isinstance(body, dict):
        raise GuideError("A guide file is a JSON object.")
    if body.get("schema_version") != SCHEMA_VERSION:
        raise GuideError(f"Unsupported schema_version {body.get('schema_version')!r} (this UI reads {SCHEMA_VERSION}).")
    guide = {"name": str(body.get("name", "")), "description": str(body.get("description", "")),
             "steps": body.get("steps")}
    if errors := validate(guide):
        raise GuideError("\n".join(errors))
    return guide


# ------------------------------------------------------------------------------------------------ output
def clip_output(data: Any, limit: int = OUTPUT_LIMIT) -> tuple[Any, bool]:
    """`data` as it goes into the run log: unchanged when small, else its first `limit` characters of JSON as text."""
    text = json.dumps(data, ensure_ascii=False)
    if len(text) <= limit:
        return data, False
    return text[:limit], True


# ------------------------------------------------------------------------------------------------ display
def target_text(probe: dict) -> str:
    devices, groups = targets_of(probe)
    return ", ".join(devices + [f"group {x}" for x in groups]) or "no target"


def summarize(step: dict) -> str:
    """One line for step lists: what the step does, without its text."""
    t = step["type"]
    if t in ("text", "confirm"):  # the first words, without markdown marks
        return " ".join(re.sub(r"[*`#>]", "", str(step.get("text" if t == "text" else "question", ""))).split())[:80]
    if t == "input":
        return f"asks for {{{{{step.get('var', '')}}}}} ({step.get('kind', 'text')})"
    if t in ("rpc", "check"):
        what = next(iter(step.get("body") or {}), "no RPC chosen")
        if what == "command":
            what = step["body"]["command"]
        return f"{what} on {target_text(step)}" + (f", {len(step.get('rules') or [])} rule(s)" if t == "check" else "")
    if t == "wait":
        u = step.get("until")
        return f"until {next(iter(u.get('body') or {}), '?')} on {target_text(u)} passes (max {u.get('timeout')} s)" if u \
            else f"{step.get('seconds')} seconds"
    if t == "connection":
        return f"{step.get('operation')} {step.get('device')}"
    return f"config pull {step.get('device')}" + (" (merge)" if step.get("merge") else "")


def reply_lines(data: Any) -> tuple[list[str], bool]:
    """A reply as text lines for display, and whether it was cut. CLI replies (`{"output": "..."}`, possibly under one
    wrapper key) are shown as the device printed them; a reply that was cut for the log is already text; anything else
    is rendered like device configuration."""
    if data is None:
        return [], False
    if isinstance(data, str):
        return data.splitlines(), False
    inner = next(iter(data.values())) if isinstance(data, dict) and len(data) == 1 else None
    for d in (data, inner):
        if isinstance(d, dict) and len(d) == 1 and isinstance(d.get("output"), str):
            return d["output"].splitlines(), False
    return to_lines(data)


# ------------------------------------------------------------------------------------------------ picking and summarising fields
def label_for(path: str) -> str:
    """A readable default label for a path: its last part, dashes as spaces ('host-name' -> 'Host name')."""
    last = [p for p in path.split("/") if p and not p.isdigit()][-1:] or [""]
    return last[0].replace("-", " ").replace("_", " ").capitalize()


def leaf_paths(data: Any, limit: int = 400) -> list[dict]:
    """Every value in a reply as {"path", "sample", "count"}, for the author to pick from. Elements of a list share one path
    (`count` says how many values there are, `sample` is the first); keys are written without their module prefix."""
    found: dict[str, dict] = {}

    def walk(node: Any, path: tuple[str, ...]) -> None:
        if len(found) >= limit and "/".join(path) not in found:
            return
        if isinstance(node, dict):
            for k, v in node.items():
                walk(v, (*path, _local(k)))
        elif isinstance(node, list):
            for item in node:
                walk(item, path)
        elif node is not None and path:
            key = "/".join(path)
            entry = found.setdefault(key, {"path": key, "sample": _text(node), "count": 0})
            entry["count"] += 1

    walk(data, ())
    return list(found.values())


def column(data: Any, path: str) -> tuple[list[str | None], bool]:
    """The values at `path` for a table column, and whether the path went through a list. Unlike `lookup`, an element of a
    list that lacks the key leaves a None gap, so the values of several columns stay on the right row."""
    nodes: list[Any] = [data]
    listy = False
    for part in (p for p in path.split("/") if p):
        nxt: list[Any] = []
        for n in nodes:
            if isinstance(n, list):
                listy = True
                if part.isdigit():
                    nxt.extend(n[int(part):int(part) + 1] or [None])
                    continue
                for item in n:
                    got = [v for k, v in item.items() if k == part or _local(k) == part] if isinstance(item, dict) else []
                    nxt.extend(got or [None])
            elif isinstance(n, dict):
                nxt.extend([v for k, v in n.items() if k == part or _local(k) == part] or [None])
            else:
                nxt.append(None)
        nodes = nxt
    out: list[str | None] = []
    for n in nodes:
        if isinstance(n, list):  # a leaf-list at the end counts as several values
            listy = True
            out.extend([_text(x) for x in n] or [None])
        else:
            out.append(None if n is None else _text(n))
    return out, listy


def summary(data: Any, fields: list[dict]) -> dict | None:
    """The chosen fields of one reply, ready for a table. One value per field gives a vertical list (Field, Value); several
    give a grid with a column per field. A field that is a single value outside any list is repeated on every row; a missing
    value shows as an em dash. None when no fields were chosen."""
    if not fields:
        return None
    cols = [(f.get("label") or label_for(f["path"]), *column(data, f["path"])) for f in fields]
    dash = lambda v: "—" if v is None else v  # noqa: E731
    n = max(len(v) for _, v, _ in cols)
    if n <= 1:
        return {"vertical": True, "columns": ["Field", "Value"], "rows": [[label, dash(v[0] if v else None)] for label, v, _ in cols]}
    rows = [[dash(v[i] if i < len(v) else (v[0] if len(v) == 1 and not listy else None)) for _, v, listy in cols] for i in range(n)]
    return {"vertical": False, "columns": [label for label, _, _ in cols], "rows": rows}
