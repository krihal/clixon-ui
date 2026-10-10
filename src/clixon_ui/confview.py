"""Turn RESTCONF JSON device config into Junos-style hierarchical text, and build an outline tree."""

from __future__ import annotations

from typing import Any
from urllib.parse import quote

MAX_LINES = 20_000


def _local(k: str) -> str:
    return k.rpartition(":")[2]


def _scalar(v: Any) -> str:
    if v is True:
        return "true"
    if v is False:
        return "false"
    s = str(v)
    return f'"{s}"' if (not s or any(c in s for c in ' \t;{}"[]')) else s


def to_lines(obj: Any, limit: int = MAX_LINES) -> tuple[list[str], bool]:
    """Render config JSON as indented text lines. Returns (lines, truncated)."""
    out: list[str] = []

    def emit(ind: int, text: str) -> bool:
        if len(out) >= limit:
            return False
        out.append("    " * ind + text)
        return True

    def block(ind: int, head: str, body: dict) -> bool:
        if not body:
            return emit(ind, f"{head};")
        if not emit(ind, f"{head} {{"):
            return False
        for k, v in body.items():
            if not node(ind + 1, k, v):
                return False
        return emit(ind, "}")

    def node(ind: int, key: str, val: Any) -> bool:
        name = _local(key)
        if isinstance(val, dict):
            return block(ind, name, val)
        if isinstance(val, list):
            if val and all(isinstance(x, dict) for x in val):
                for e in val:
                    if "name" in e:
                        rest = {k: v for k, v in e.items() if k != "name"}
                        if not block(ind, f"{name} {_scalar(e['name'])}", rest):
                            return False
                    elif not block(ind, name, e):
                        return False
                return True
            if val == [None]:
                return emit(ind, f"{name};")
            return emit(ind, f"{name} [ {' '.join(_scalar(x) for x in val)} ];")
        if val is None:
            return emit(ind, f"{name};")
        return emit(ind, f"{name} {_scalar(val)};")

    if isinstance(obj, dict):
        for k, v in obj.items():
            if not node(0, k, v):
                break
    return out, len(out) >= limit


def entry_key(entry: dict) -> str | None:
    """The key leaf of a list entry: `name` (Junos), else the first scalar leaf (other models, e.g. OpenConfig `vlan-id`)."""
    if "name" in entry:
        return "name"
    return next((k for k, v in entry.items() if not isinstance(v, (dict, list))), None)


def outline(cfg: dict) -> list[dict]:
    """Tree nodes {id,label,children} from a depth-limited config object.

    `id` is the RESTCONF path of the node relative to the config root (segments joined by '/')."""

    def build(obj: dict, prefix: str) -> list[dict]:
        nodes = []
        for k, v in obj.items():
            path = f"{prefix}{k}"
            label = _local(k)
            if isinstance(v, dict):
                nodes.append({"id": path, "label": label, "children": build(v, path + "/")})
            elif isinstance(v, list) and v and all(isinstance(x, dict) for x in v):
                kids = []
                for e in v:
                    key = entry_key(e)
                    if key:
                        eid = f"{path}={quote(str(e[key]), safe='')}"
                        kids.append({"id": eid, "label": str(e[key]), "children": build({a: b for a, b in e.items() if a != key}, eid + "/")})
                # A list cannot be read as a whole over RESTCONF (the controller answers "malformed key"):
                # only its entries can. So the list node itself is not selectable, it just expands.
                note = "" if kids else " – entries not browsable"
                nodes.append({"id": path, "label": f"{label} ({len(v)}){note}", "children": kids, "selectable": False})
        return nodes

    return build(cfg, "")
