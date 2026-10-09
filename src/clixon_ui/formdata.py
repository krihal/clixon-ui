"""Pure data layer for service forms: RESTCONF JSON <-> form dict, validation, leafref lookup.

Form dicts use plain local names (no module prefixes):
  container -> dict, list -> list[dict], leaf-list -> list, empty leaf -> True, others -> scalars.
"""

from __future__ import annotations

import re
from typing import Any

from .schema import Node, YType, data_children

_STR_NUM = {"int64", "uint64"}


def _local(name: str) -> str:
    return name.rpartition(":")[2]


# ------------------------------------------------------------------ JSON <-> dict
def _scalar_from_json(t: YType | None, v: Any) -> Any:
    if t is None:
        return v
    if t.base == "empty":
        return True
    if t.base == "int" and isinstance(v, str):
        try:
            return int(v)
        except ValueError:
            return v
    if t.base == "decimal" and isinstance(v, (str, int)):
        try:
            return float(v)
        except ValueError:
            return v
    if t.base == "boolean" and isinstance(v, str):
        return v == "true"
    return v


def entry_from_json(node: Node, d: dict) -> dict:
    """Convert one RESTCONF object (container body or list entry) to a form dict."""
    by_name = {c.name: c for c in data_children(node)}
    out: dict = {}
    for k, v in d.items():
        c = by_name.get(_local(k))
        if c is None:
            continue  # e.g. config-false or unknown leaves
        out[c.name] = _value_from_json(c, v)
    return out


def _value_from_json(c: Node, v: Any) -> Any:
    if c.kind == "container":
        return entry_from_json(c, v)
    if c.kind == "list":
        return [entry_from_json(c, e) for e in v]
    if c.kind == "leaf-list":
        return [_scalar_from_json(c.type, x) for x in v]
    return _scalar_from_json(c.type, v)


def _scalar_to_json(t: YType | None, v: Any) -> Any:
    if t is None:
        return v
    if t.base == "empty":
        return [None]
    if t.base == "int":
        v = int(v)
        return str(v) if t.builtin in _STR_NUM else v
    if t.base == "decimal":
        return f"{float(v):.{t.fraction_digits}f}"
    return v


def _empty(v: Any) -> bool:
    return v is None or v == "" or v == [] or v == {}


def entry_to_json(node: Node, data: dict) -> dict:
    """Form dict -> RESTCONF object with RFC 7951 module-qualified names where the module changes."""
    out: dict = {}
    for c in data_children(node):
        if c.name not in data:
            continue
        v = data[c.name]
        if c.kind == "container":
            body = entry_to_json(c, v)
            if not body and not (c.presence and v is not None):
                continue
            jv: Any = body
        elif c.kind == "list":
            jv = [entry_to_json(c, e) for e in v if e]
            if not jv:
                continue
        elif c.kind == "leaf-list":
            if not v:
                continue
            jv = [_scalar_to_json(c.type, x) for x in v]
        else:
            if _empty(v) or (c.type and c.type.base == "empty" and not v):
                continue
            jv = _scalar_to_json(c.type, v)
        out[f"{c.module}:{c.name}" if c.module != node.module else c.name] = jv
    return out


def service_to_json(node: Node, data: dict, preserved: dict | None = None) -> dict:
    """Body for PUT .../services/<module>:<list>=<key>.

    PUT replaces the whole instance, so `preserved` (hidden controller-managed nodes such as
    `created`) must be passed through or the controller loses track of what the service made."""
    return {f"{node.module}:{node.name}": [{**entry_to_json(node, data), **(preserved or {})}]}


def property_to_json(node: Node, data: dict) -> dict:
    """Body for PUT .../services/properties/<module>:<container>. For a list property `data` is
    {name: [entries]} and the result shows the whole list (it is written entry by entry, see `property_entries`)."""
    if node.kind == "list":
        return {f"{node.module}:{node.name}": [entry_to_json(node, e) for e in data.get(node.name, []) if e]}
    return {f"{node.module}:{node.name}": entry_to_json(node, data)}


def property_key(node: Node, entry: dict) -> str:
    """Key of a list entry as used in the RESTCONF path (composite keys are comma separated)."""
    return ",".join(str(entry.get(k, "")) for k in node.keys)


def property_entries(node: Node, data: dict) -> dict[str, dict]:
    """List property: {key: PUT body} for every entry of the form."""
    return {property_key(node, e): {f"{node.module}:{node.name}": [entry_to_json(node, e)]}
            for e in data.get(node.name, []) if e}


# ------------------------------------------------------------------ leafref lookup
class Lookup:
    """Resolves absolute leafref paths against a synthetic tree {'devices':…, 'services':…}."""

    def __init__(self, tree: dict | None = None):
        self.tree = tree or {}

    def values(self, path: str | None) -> list[str] | None:
        """Possible values for an absolute leafref path; None if path unsupported/unknown."""
        if path and path.startswith("../"):
            # relative reference between siblings of a list under /devices: resolve against /devices
            path = "/devices/" + "/".join(p for p in path.split("/") if p not in ("..", ""))
        if not path or not path.startswith("/"):
            return None
        steps = [_local(re.sub(r"\[.*?\]", "", s)) for s in path.strip("/").split("/")]
        nodes: list[Any] = [self.tree]
        for step in steps:
            nxt: list[Any] = []
            for n in nodes:
                if not isinstance(n, dict):
                    continue
                for k, v in n.items():
                    if _local(k) == step:
                        nxt.extend(v if isinstance(v, list) else [v])
            nodes = nxt
        vals = sorted({str(n) for n in nodes if not isinstance(n, (dict, list))})
        return vals if steps[0] in {_local(k) for k in self.tree} else None


# ------------------------------------------------------------------ validation
def _num_error(t: YType, v: Any) -> str | None:
    if t.range:
        lo, hi = t.range
        if lo is not None and v < lo:
            return f"must be ≥ {lo:g}"
        if hi is not None and v > hi:
            return f"must be ≤ {hi:g}"
    return None


def type_error(t: YType | None, v: Any, lookup: Lookup | None = None, path: str | None = None) -> str | None:
    """Return an error message if v is not valid for type t."""
    if t is None or v is None or v == "":
        return None
    if t.base == "int":
        if isinstance(v, bool) or not isinstance(v, (int, float)) or int(v) != v:
            return "must be an integer"
        return _num_error(t, v)
    if t.base == "decimal":
        return _num_error(t, v) if isinstance(v, (int, float)) else "must be a number"
    if t.base == "enum":
        return None if v in t.enums else f"must be one of: {', '.join(t.enums)}"
    if t.base == "identityref":
        return None if not t.identities or v in t.identities or _local(v) in {_local(i) for i in t.identities} else "unknown identity"
    if t.base == "leafref":
        opts = lookup.values(t.path) if lookup else None
        return None if not opts or v in opts else f"'{v}' does not exist"
    if t.base == "union":
        errs = [type_error(m, v, lookup) for m in t.members]
        return None if (not t.members or None in errs) else "invalid value (" + "; ".join(sorted({e for e in errs if e})) + ")"
    if t.base == "string":
        s = str(v)
        if t.length:
            lo, hi = t.length
            if lo is not None and len(s) < lo:
                return f"min length {lo:g}"
            if hi is not None and len(s) > hi:
                return f"max length {hi:g}"
        for p in t.patterns:
            try:
                if not re.fullmatch(p, s):
                    return f"must match pattern {p}"
            except re.error:
                pass  # XSD regex we can't translate: let the controller decide
    return None


def _active_children(node: Node, data: dict):
    """Like data_children, but of a choice only the cases the data selects (a mandatory leaf in a case that
    is not used must not be reported as missing)."""
    for c in node.children:
        if c.kind == "choice":
            for case in c.children:
                kids = [case] if case.kind != "case" else case.children
                holder = Node(kind="case", name=case.name, module=case.module, children=kids)
                if any(not _empty(data.get(k.name)) for k in data_children(holder)):
                    yield from _active_children(holder, data)
        elif c.kind == "case":
            yield from _active_children(c, data)
        else:
            yield c


def validate(node: Node, data: dict, lookup: Lookup | None = None, path: str = "") -> list[str]:
    """Validate a form dict against the schema; returns human-readable errors."""
    errs: list[str] = []
    errs += [f"{path + '/' if path else ''}{node.name}/{k}: {m}" for k, m in (data.get("__bad__") or {}).items()]  # invalid JSON editors
    here = f"{path}/{node.name}" if path else node.name
    for c in _active_children(node, data):
        v = data.get(c.name)
        loc = f"{here}/{c.name}"
        if c.kind == "container":
            sub = v or {}
            if sub or not c.presence:
                errs += validate(c, sub, lookup, here)
        elif c.kind == "list":
            ents = [e for e in (v or []) if e]
            if len(ents) < c.min_elements:
                errs.append(f"{loc}: at least {c.min_elements} entries required")
            seen = set()
            for e in ents:
                kid = tuple(e.get(k) for k in c.keys)
                label = "/".join(str(x) for x in kid)
                if kid in seen:
                    errs.append(f"{loc}[{label}]: duplicate key")
                seen.add(kid)
                errs += validate(c, e, lookup, f"{here}/{c.name}[{label}]")
        elif c.kind == "leaf-list":
            items = v or []
            if len(items) < c.min_elements:
                errs.append(f"{loc}: at least {c.min_elements} values required")
            errs += [f"{loc}: {e}" for x in items if (e := type_error(c.type, x, lookup))]
        else:
            required = c.mandatory or c.name in node.keys
            if _empty(v) and c.type and c.type.base != "empty":
                if required:
                    errs.append(f"{loc}: required")
            elif (e := type_error(c.type, v, lookup)):
                errs.append(f"{loc}: {e}")
    return errs
