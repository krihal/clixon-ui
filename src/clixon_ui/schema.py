"""YANG schema of the controller's /services tree, loaded over RESTCONF (get-schema) and parsed with pyang."""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pyang import context, repository

from .client import ClixonClient, RestconfError

log = logging.getLogger("clixon_ui.schema")
CTRL = "clixon-controller"


@dataclass
class YType:
    builtin: str = ""  # original builtin name, e.g. uint64
    base: str = ""  # string int decimal boolean enum leafref union identityref empty binary bits
    enums: list[str] = field(default_factory=list)
    range: tuple[float | None, float | None] | None = None
    length: tuple[int | None, int | None] | None = None
    patterns: list[str] = field(default_factory=list)
    path: str | None = None  # leafref
    members: list["YType"] = field(default_factory=list)  # union
    identities: list[str] = field(default_factory=list)  # identityref values
    fraction_digits: int = 2  # decimal64


@dataclass
class Node:
    kind: str  # container list leaf leaf-list choice case anydata
    name: str
    module: str
    description: str = ""
    mandatory: bool = False
    default: str | None = None
    type: YType | None = None
    keys: list[str] = field(default_factory=list)
    presence: bool = False
    min_elements: int = 0
    children: list["Node"] = field(default_factory=list)
    stmt: Any = None  # underlying pyang statement (for leafref path resolution)

    def child(self, name: str) -> "Node | None":
        return next((c for c in self.children if c.name == name), None)


def _arg(stmt, kw: str) -> str | None:
    s = stmt.search_one(kw)
    return s.arg if s is not None else None


_INT = {"int8", "int16", "int32", "int64", "uint8", "uint16", "uint32", "uint64"}


def _num(x):
    try:
        return float(x) if x not in ("min", "max") else None
    except (TypeError, ValueError):
        return None


def _parse_range(arg: str | None):
    """'1..65535 | 7' -> (lo, hi) overall bounds (good enough for form validation)."""
    if not arg:
        return None
    lo, hi = [], []
    for part in arg.split("|"):
        a, _, b = part.strip().partition("..")
        lo.append(_num(a)); hi.append(_num(b or a))
    los = [x for x in lo if x is not None]
    his = [x for x in hi if x is not None]
    return (min(los) if los and None not in lo else None, max(his) if his and None not in hi else None)


def _base_type(t) -> YType:
    """Resolve a pyang `type` statement (following typedefs) to a YType."""
    name = t.arg.split(":")[-1]
    td = getattr(t, "i_typedef", None)
    if td is not None:
        inner = _base_type(td.search_one("type"))
        inner = YType(**{**inner.__dict__})  # don't mutate shared typedef result
        # facets on the use-site restrict the typedef
        for kw, attr in (("range", "range"), ("length", "length")):
            r = _parse_range(_arg(t, kw))
            if r:
                setattr(inner, attr, r)
        inner.patterns = inner.patterns + [p.arg for p in t.search("pattern")]
        return inner
    y = YType(builtin=name, base=name)
    if name in _INT:
        y.base = "int"
        y.range = _parse_range(_arg(t, "range"))
        if y.range is None:
            bits = int(name.lstrip("uint")) if name.startswith(("int", "uint")) else 32
            y.range = (0, 2 ** bits - 1) if name.startswith("u") else (-(2 ** (bits - 1)), 2 ** (bits - 1) - 1)
    elif name == "decimal64":
        y.base = "decimal"
        y.fraction_digits = int(_arg(t, "fraction-digits") or 2)
        y.range = _parse_range(_arg(t, "range"))
    elif name == "string":
        y.length = _parse_range(_arg(t, "length"))
        y.patterns = [p.arg for p in t.search("pattern")]
    elif name == "enumeration":
        y.base = "enum"
        y.enums = [e.arg for e in t.search("enum")]
    elif name == "leafref":
        y.path = _arg(t, "path")
        rt = t.search_one("type")
    elif name == "union":
        y.members = [_base_type(m) for m in t.search("type")]
    elif name == "identityref":
        ids = []
        for b in t.search("base"):
            ident = getattr(b, "i_identity", None)
            if ident is not None:
                ids += _derived(ident)
        y.identities = sorted(set(ids))
    return y


def _derived(ident) -> list[str]:
    out = []
    for d in getattr(ident, "i_derived", []):
        mod = _modname(d)
        out.append(f"{mod}:{d.arg}")
        out += _derived(d)
    return out


def _modname(s) -> str:
    """Owning module name (submodules map to their belongs-to module): the JSON namespace."""
    m = s.i_module
    return getattr(m, "i_modulename", None) or m.arg


def _convert(s) -> Node | None:
    kw = s.keyword
    if kw not in ("container", "list", "leaf", "leaf-list", "choice", "case", "anydata", "anyxml"):
        return None
    if getattr(s, "i_config", True) is False:
        return None
    n = Node(
        kind="anydata" if kw == "anyxml" else kw,
        name=s.arg,
        module=_modname(s),
        description=" ".join((_arg(s, "description") or "").split()),
        mandatory=(_arg(s, "mandatory") == "true"),
        default=_arg(s, "default"),
        stmt=s,
    )
    if kw in ("leaf", "leaf-list"):
        n.type = _base_type(s.search_one("type"))
        if n.default is None and kw == "leaf":
            td = getattr(s.search_one("type"), "i_typedef", None)
            n.default = _arg(td, "default") if td is not None else None
    if kw == "list":
        n.keys = [k.arg if hasattr(k, "arg") else k for k in getattr(s, "i_key", [])]
        n.min_elements = int(_arg(s, "min-elements") or 0)
    if kw == "leaf-list":
        n.min_elements = int(_arg(s, "min-elements") or 0)
    if kw == "container":
        n.presence = s.search_one("presence") is not None
    for c in getattr(s, "i_children", []):
        cn = _convert(c)
        if cn:
            n.children.append(cn)
    return n


class Schema:
    """All service types known to the controller (`/services` augments)."""

    def __init__(self, ctx: context.Context):
        self.ctx = ctx
        ctrl = next(m for (n, _), m in ((k, v) for k, v in ctx.modules.items()) if n == CTRL)
        services = ctrl.search_one("container", "services")
        self.services: list[Node] = []
        for c in services.i_children:
            n = _convert(c)
            if n and n.kind == "list":
                # controller-managed bookkeeping, not user input
                n.children = [c for c in n.children if not (c.name == "created" and [g.name for g in c.children] == ["path"])]
                self.services.append(n)
        self.services.sort(key=lambda n: n.name)

    def inventory(self) -> dict[str, Node]:
        """The editable lists under /devices (device, device-group, device-profile, template, rpc-template).

        The device's `config` container is the mounted configuration of the device itself: it is not a setting
        and must never be edited (or replaced) through a form, so it is removed here."""
        ctrl = next(m for (n, _), m in ((k, v) for k, v in self.ctx.modules.items()) if n == CTRL)
        devices = _convert(ctrl.search_one("container", "devices"))
        out = {c.name: c for c in devices.children if c.kind == "list"}
        if "device" in out:
            out["device"].children = [c for c in out["device"].children if c.name != "config"]
        return out

    def service(self, qname: str) -> Node | None:
        """qname = 'module:name' or just name."""
        mod, _, name = qname.rpartition(":")
        return next((s for s in self.services if s.name == name and (not mod or s.module == mod)), None)

    def top_container(self) -> Node:
        ctrl = next(m for (n, _), m in ((k, v) for k, v in self.ctx.modules.items()) if n == CTRL)
        return _convert(ctrl.search_one("container", "services"))


async def load_schema(client: ClixonClient, cache: Path | None = None) -> Schema:
    """Fetch every module from yang-library via get-schema, cache on disk, parse."""
    key = hashlib.sha1(client.url.encode()).hexdigest()[:10]
    d = (cache or Path.home() / ".cache" / "clixon-ui") / key
    d.mkdir(parents=True, exist_ok=True)
    lib = await client.get("ietf-yang-library:yang-library/module-set=top")
    mods = lib["ietf-yang-library:module-set"][0]["module"]
    wanted = []
    for m in mods:
        wanted.append((m["name"], m.get("revision", "")))
        wanted += [(s["name"], s.get("revision", "")) for s in m.get("submodule", [])]
    for name, rev in wanted:
        f = d / f"{name}@{rev}.yang"
        if f.exists():
            continue
        try:
            out = await client._request("POST", "/operations/ietf-netconf-monitoring:get-schema", json={
                "ietf-netconf-monitoring:input": {"identifier": name, "version": rev, "format": "yang"}})
            text = list(out.values())[0]
            f.write_text(text["data"] if isinstance(text, dict) else text)
        except RestconfError as e:
            log.warning("get-schema %s@%s failed: %s", name, rev, e)
    repo = repository.FileRepository(str(d), use_env=False)
    ctx = context.Context(repo)
    for name, rev in wanted:
        f = d / f"{name}@{rev}.yang"
        if f.exists():
            ctx.add_module(str(f), f.read_text(), in_format="yang")
    ctx.validate()
    return Schema(ctx)


def data_children(node: Node):
    """Data-bearing child nodes, looking through choice/case wrappers."""
    for c in node.children:
        if c.kind in ("choice", "case"):
            yield from data_children(c)
        else:
            yield c
