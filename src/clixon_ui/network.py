"""Network map: discovery sources (which RPC to ask a device) and the graph built from their answers.

A *discovery source* knows which RPC to send and how to turn the reply into vendor-neutral
`Adjacency` records ("device D sees neighbour N on port P"). Adding another vendor or another RPC means
adding a source to `SOURCES`; nothing else changes. `build_graph` merges the two directions of a link
(A sees B and B sees A) and separates managed devices from external neighbours.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class Adjacency:
    """One neighbour as reported by one device."""
    device: str  # the managed device that reported it
    local_port: str
    remote_name: str  # as announced by the neighbour (often an FQDN)
    remote_port: str | None = None
    remote_descr: str = ""
    remote_chassis: str = ""
    parent: str = ""  # aggregate (LAG) the local port belongs to, if any
    source: str = ""


@dataclass
class DiscoverySource:
    key: str
    label: str
    rpc: dict  # inline RPC body sent to every device
    parse: Callable[[str, Any], list[Adjacency]]  # (device, reply data) -> adjacencies
    description: str = ""


def _as_list(x: Any) -> list:
    return x if isinstance(x, list) else ([] if x is None else [x])


def _clean(v: Any) -> str | None:
    v = None if v is None else str(v).strip()
    return None if v in (None, "", "-") else v


# --------------------------------------------------------------------------- Juniper
def parse_juniper_lldp(device: str, data: Any) -> list[Adjacency]:
    """Reply of `get-lldp-interface-neighbors` (Junos): lldp-neighbors-information/lldp-neighbor-information[]."""
    info = (data or {}).get("lldp-neighbors-information") or {}
    out: list[Adjacency] = []
    for n in _as_list(info.get("lldp-neighbor-information")):
        name, local = _clean(n.get("lldp-remote-system-name")), _clean(n.get("lldp-local-port-id"))
        if not local:
            continue
        chassis = _clean(n.get("lldp-remote-chassis-id")) or ""
        out.append(Adjacency(
            device=device, local_port=local,
            remote_name=name or chassis or "unknown",  # a neighbour without a system name is identified by its chassis id
            remote_port=_clean(n.get("lldp-remote-port-id")),
            remote_descr=_clean(n.get("lldp-remote-port-description")) or "",
            remote_chassis=chassis,
            parent=_clean(n.get("lldp-local-parent-interface-name")) or "",
            source="juniper-lldp"))
    return out


SOURCES: dict[str, DiscoverySource] = {
    "juniper-lldp": DiscoverySource(
        key="juniper-lldp", label="Juniper LLDP neighbours",
        rpc={"get-lldp-interface-neighbors": {}},
        parse=parse_juniper_lldp,
        description="get-lldp-interface-neighbors on every selected device"),
}
DEFAULT_SOURCE = "juniper-lldp"


# --------------------------------------------------------------------------- graph
@dataclass
class Link:
    a: str  # managed device
    a_port: str
    b: str  # managed device or external system name
    b_port: str | None
    b_managed: bool
    descr: str = ""
    parent: str = ""
    both_sides: bool = False  # the neighbour reported us too

    @property
    def status(self) -> str:
        if not self.b_managed:
            return "external"
        return "confirmed" if self.both_sides else "one side"


@dataclass
class Node:
    name: str
    managed: bool
    chassis: set[str] = field(default_factory=set)


@dataclass
class Graph:
    nodes: dict[str, Node] = field(default_factory=dict)
    links: list[Link] = field(default_factory=list)


def short_name(name: str) -> str:
    """'ptx-ac-2.sunet.se' -> 'ptx-ac-2'"""
    return name.split(".")[0].strip().lower()


def build_graph(adjs: list[Adjacency], managed: list[str]) -> Graph:
    """Merge adjacencies reported by managed devices into nodes and links."""
    by_short = {short_name(m): m for m in managed}
    g = Graph(nodes={m: Node(m, True) for m in managed})
    used: set[int] = set()

    def resolve(name: str) -> str | None:
        return by_short.get(short_name(name))

    for i, a in enumerate(adjs):
        if i in used:
            continue
        rdev = resolve(a.remote_name)
        if rdev is None:  # not one of our devices: an external neighbour (switch, customer, ...)
            node = g.nodes.setdefault(a.remote_name, Node(a.remote_name, False))
            if a.remote_chassis:
                node.chassis.add(a.remote_chassis)
            g.links.append(Link(a.device, a.local_port, a.remote_name, a.remote_port, False, a.remote_descr, a.parent))
            continue
        # a managed neighbour: look for the same link as seen from the other end
        rev = next((j for j, b in enumerate(adjs)
                    if j != i and j not in used and b.device == rdev and resolve(b.remote_name) == a.device
                    and a.remote_port in (None, b.local_port) and b.remote_port in (None, a.local_port)), None)
        used.add(i)
        if rev is not None:
            used.add(rev)
            b = adjs[rev]
            g.links.append(Link(a.device, a.local_port, rdev, b.local_port, True, a.remote_descr or b.remote_descr,
                                a.parent or b.parent, both_sides=True))
        else:
            g.links.append(Link(a.device, a.local_port, rdev, a.remote_port, True, a.remote_descr, a.parent))
    return g


# --------------------------------------------------------------------------- readability helpers
_MGMT = ("fxp", "em0", "em1", "me0", "vme", "mgmt", "oob", "eth0")


def is_management_port(port: str | None) -> bool:
    """Out-of-band management interfaces (Junos fxp0/em0/me0/vme, 're0:mgmt-0', ...)."""
    p = (port or "").lower()
    return ":mgmt" in p or p.startswith(_MGMT)


def filter_graph(g: Graph, external: bool = True, management: bool = False, loops: bool = False) -> Graph:
    """A copy of g without the clutter the user chose to hide. Managed devices are always kept."""
    links = []
    for l in g.links:
        if not external and not l.b_managed:
            continue
        if not management and (is_management_port(l.a_port) or is_management_port(l.b_port)):
            continue
        if not loops and l.a == l.b:
            continue
        links.append(l)
    used = {l.a for l in links} | {l.b for l in links}
    nodes = {n: node for n, node in g.nodes.items() if node.managed or n in used}
    return Graph(nodes=nodes, links=links)


@dataclass
class LinkGroup:
    """All links between the same two nodes, drawn as one line."""
    a: str
    b: str
    links: list[Link]

    @property
    def status(self) -> str:
        sts = {l.status for l in self.links}
        return "one side" if "one side" in sts else ("confirmed" if "confirmed" in sts else "external")


def group_links(g: Graph) -> list[LinkGroup]:
    groups: dict[tuple[str, str], LinkGroup] = {}
    for l in g.links:
        key = tuple(sorted((l.a, l.b)))
        groups.setdefault(key, LinkGroup(key[0], key[1], [])).links.append(l)
    return list(groups.values())


def radial_positions(g: Graph) -> dict[str, tuple[float, float]]:
    """Deterministic layout (unit scale): managed devices on a ring, an external neighbour outside the device it
    hangs off, one shared by two devices outside their midpoint, one shared by three or more in the centre."""
    import math

    managed = sorted(n for n, node in g.nodes.items() if node.managed)
    pos: dict[str, tuple[float, float]] = {}
    ang: dict[str, float] = {}
    for i, m in enumerate(managed):
        ang[m] = 2 * math.pi * i / max(len(managed), 1) - math.pi / 2
        pos[m] = (math.cos(ang[m]), math.sin(ang[m])) if len(managed) > 1 else (0.0, 0.0)
    attached: dict[str, list[str]] = {}
    for l in g.links:
        for ext, dev in ((l.b, l.a), ) if not l.b_managed else ():
            if dev not in attached.setdefault(ext, []):
                attached[ext].append(dev)
    per_anchor: dict[tuple, int] = {}
    for ext in sorted(n for n, node in g.nodes.items() if not node.managed):
        devs = sorted(attached.get(ext, []))
        if not devs:
            pos[ext] = (0.0, 0.0)
        elif len(devs) >= 3:
            pos[ext] = (0.0, 0.0)
        else:
            vx = sum(math.cos(ang[d]) for d in devs)
            vy = sum(math.sin(ang[d]) for d in devs)
            a = math.atan2(vy, vx) if (abs(vx) + abs(vy)) > 1e-9 else ang[devs[0]]
            idx = per_anchor.get(tuple(devs), 0)
            per_anchor[tuple(devs)] = idx + 1
            a += 0.34 * (idx - 0) * (1 if idx % 2 else -1) * ((idx + 1) // 2)  # fan siblings out around the anchor
            r = 1.75 if len(devs) == 1 else 1.55
            pos[ext] = (r * math.cos(a), r * math.sin(a))
    return pos
