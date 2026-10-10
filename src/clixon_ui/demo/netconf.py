"""Pure: NETCONF edit-config XML (as sent by `netconfxml`) -> edits applied to an RFC 7951 JSON tree.

The inverse of `clixon_ui.netconfxml`. The fake controller needs it to keep a candidate datastore. Which element is a
list, a leaf-list or a boolean comes from `Meta` (built from the YANG), because XML does not say."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import Any

from ..netconfxml import NC

_INT = {"int8", "int16", "int32", "uint8", "uint16", "uint32"}
_OP = f"{{{NC}}}operation"


@dataclass
class El:
    name: str
    key: str  # JSON member name: `module:name`, or just `name` when the parent has the same module
    module: str | None
    parent: str = ""  # local name of the parent element: the same name can be a list here and a leaf-list there
    op: str | None = None
    text: str = ""
    children: list["El"] = field(default_factory=list)


class Meta:
    """What the YANG says about a node, found by (parent name, name): kind, list keys, builtin type."""

    def __init__(self, nodes: dict[str, Any] | None = None):
        self.nodes: dict[tuple, Any] = nodes or {}

    @classmethod
    def from_schema(cls, schema) -> "Meta":
        nodes: dict[tuple, Any] = {}

        def walk(n, parent: str) -> None:
            if n.kind in ("choice", "case"):  # the data nodes of a case belong to the case's parent
                for c in n.children:
                    walk(c, parent)
                return
            nodes.setdefault((parent, n.name), n)
            nodes.setdefault((parent, n.name, n.module), n)  # the same name can differ per module (l2c device/interface vs interface-customer's)
            for c in n.children:
                walk(c, n.name)

        walk(schema.top_container(), "")
        for lst in schema.inventory().values():
            walk(lst, "devices")
        for prop in schema.properties():  # augments of services/properties are not always copied into the services container
            walk(prop, "properties")
        if (nacm := schema.nacm()) is not None:
            walk(nacm, "")
        return cls(nodes)

    def node(self, parent: str, name: str, module: str | None = None):
        return self.nodes.get((parent, name, module)) or self.nodes.get((parent, name))

    def kind(self, el: El) -> str:
        n = self.node(el.parent, el.name, el.module)
        if n is not None and n.kind in ("container", "list", "leaf", "leaf-list", "anydata"):
            return n.kind
        return "container" if el.children else "leaf"

    def keys(self, parent: str, name: str, module: str | None = None) -> list[str]:
        n = self.node(parent, name, module)
        return list(n.keys) if n is not None and n.keys else ["name"]

    def scalar(self, parent: str, name: str, text: str, module: str | None = None) -> Any:
        n = self.node(parent, name, module)
        builtin = n.type.builtin if n is not None and n.type is not None else ""
        if builtin == "boolean":
            return text.strip() == "true"
        if builtin in _INT:
            try:
                return int(text)
            except ValueError:
                return text
        if builtin == "empty":
            return [None]
        return text


def parse(xml: str, ns2mod: dict[str, str]) -> list[El]:
    """The elements inside `<config>` of an edit-config input."""
    root = ET.fromstring(xml)
    config = next(c for c in root.iter() if c.tag.rpartition("}")[2] == "config")
    return [_element(c, ns2mod, None, "") for c in config]


def _element(x: ET.Element, ns2mod: dict[str, str], parent: str | None, parent_name: str) -> El:
    uri, _, name = x.tag[1:].partition("}") if x.tag.startswith("{") else ("", "", x.tag)
    module = ns2mod.get(uri) or parent
    key = name if module == parent else f"{module}:{name}"
    return El(name, key, module, parent_name, x.get(_OP), (x.text or "").strip(), [_element(c, ns2mod, module, name) for c in x])


# ------------------------------------------------------------------------------------------------ building values
def value(e: El, meta: Meta) -> Any:
    """The JSON value of an element, operations dropped."""
    kind = meta.kind(e)
    if kind == "leaf":
        return meta.scalar(e.parent, e.name, e.text, e.module) if (e.text or meta.node(e.parent, e.name, e.module)) else [None]
    if kind == "leaf-list":
        return meta.scalar(e.parent, e.name, e.text, e.module)
    out: dict[str, Any] = {}
    for c in e.children:
        _put(out, c, meta)
    return out


def _put(dst: dict, c: El, meta: Meta) -> None:
    kind = meta.kind(c)
    key = _member(dst, c)
    if kind in ("list", "leaf-list"):
        dst.setdefault(key, []).append(value(c, meta))
    else:
        dst[key] = value(c, meta)


def _member(dst: dict, e: El) -> str:
    if e.key in dst:
        return e.key
    return next((k for k in dst if k.rpartition(":")[2] == e.name), e.key)


def _ident(e: El, meta: Meta) -> tuple:
    keys = meta.keys(e.parent, e.name, e.module)
    return tuple(next((c.text for c in e.children if c.name == k), None) for k in keys)


def _entry_ident(entry: dict, keys: list[str]) -> tuple:
    return tuple(None if entry.get(k) is None else str(entry.get(k)) for k in keys)


# ------------------------------------------------------------------------------------------------ applying
def apply(dst: dict, elements: list[El], meta: Meta) -> None:
    """Merge `elements` into `dst` honouring nc:operation (replace, remove, delete, create, merge)."""
    for e in elements:
        kind = meta.kind(e)
        key = _member(dst, e)
        removing = e.op in ("remove", "delete")
        if kind == "list":
            keys = meta.keys(e.parent, e.name, e.module)
            lst = dst.setdefault(key, [])
            ident = _ident(e, meta)
            idx = next((i for i, x in enumerate(lst) if _entry_ident(x, keys) == ident), None)
            if removing:
                if idx is not None:
                    del lst[idx]
            elif idx is None or e.op == "replace":
                (lst.__setitem__(idx, value(e, meta)) if idx is not None else lst.append(value(e, meta)))
            else:
                apply(lst[idx], e.children, meta)
            if not lst:
                dst.pop(key, None)
        elif kind == "leaf-list":
            lst = dst.setdefault(key, [])
            v = meta.scalar(e.parent, e.name, e.text, e.module)
            if removing:
                if v in lst:
                    lst.remove(v)
            elif v not in lst:
                lst.append(v)
            if not lst:
                dst.pop(key, None)
        elif removing:
            dst.pop(key, None)
        elif kind == "container":
            if e.op == "replace" or not isinstance(dst.get(key), dict):
                dst[key] = value(e, meta)
            else:
                apply(dst[key], e.children, meta)
        else:  # leaf, anydata
            dst[key] = value(e, meta)
