"""Network map for the demo: one LLDP source and one IS-IS overlay that work for every demo vendor.

The demo devices all answer the demo RPCs `get-lldp-neighbors` and `get-isis-state`, each in its own vendor's shape:
the Junos device with its native `lldp-neighbors-information` / `isis-*-information` trees (parsed by the real Juniper
parsers in `network`), the others with the OpenConfig `lldp` / `isis` state trees. These sources replace the Juniper
ones in `network.SOURCES` / `network.OVERLAYS` in demo mode only: which RPC returns this data on a real device depends
on the controller and the device, and has not been verified."""

from __future__ import annotations

from typing import Any

from .. import network, network_views
from ..network import Adjacency, DiscoverySource, Overlay, PortMetric, _as_list, _clean

_LEVEL = {"LEVEL_1": 1, "LEVEL_2": 2}
_STATE = {"POINT_TO_POINT": "Point to Point", "BROADCAST": "Broadcast"}


def parse_lldp(device: str, data: Any) -> list[Adjacency]:
    """Junos-shaped reply -> the real Juniper parser, anything else -> OpenConfig."""
    if "lldp-neighbors-information" in (data or {}):
        return network.parse_juniper_lldp(device, data)
    return parse_openconfig_lldp(device, data)


def parse_isis(device: str, replies: dict[str, Any]) -> list[PortMetric]:
    reply = replies.get("get-isis-state") or {}
    if "isis-interface-information" in reply:
        return network.parse_juniper_isis(device, {"get-isis-interface-information": {"isis-interface-information": reply["isis-interface-information"]},
                                                   "get-isis-adjacency-information": {"isis-adjacency-information": reply.get("isis-adjacency-information", {})}})
    return parse_openconfig_isis(device, replies)


def parse_openconfig_lldp(device: str, data: Any) -> list[Adjacency]:
    out: list[Adjacency] = []
    for i in _as_list(((data or {}).get("lldp") or {}).get("interfaces", {}).get("interface")):
        local = _clean(i.get("name"))
        for n in _as_list((i.get("neighbors") or {}).get("neighbor")):
            st = n.get("state") or {}
            chassis = _clean(st.get("chassis-id")) or ""
            if local:
                out.append(Adjacency(device=device, local_port=local, remote_name=_clean(st.get("system-name")) or chassis or "unknown",
                                     remote_port=_clean(st.get("port-id")), remote_descr=_clean(st.get("port-description")) or "",
                                     remote_chassis=chassis, source="openconfig-lldp"))
    return out


def parse_openconfig_isis(device: str, replies: dict[str, Any]) -> list[PortMetric]:
    reply = replies.get("get-isis-state")
    out: list[PortMetric] = []
    for i in _as_list(((reply or {}).get("isis") or {}).get("interfaces", {}).get("interface")):
        pm = PortMetric(device, str(i.get("interface-id")), iface_state=_STATE.get((i.get("state") or {}).get("circuit-type"), ""), adj_known=reply is not None)
        for lv in _as_list((i.get("levels") or {}).get("level")):
            level = int(lv.get("level-number", 0))
            pm.circuit_levels += (level,)
            for af in _as_list((lv.get("afi-safi") or {}).get("af")):
                if (metric := (af.get("state") or {}).get("metric")) is not None:
                    pm.metrics[level] = int(metric)
            for a in _as_list((lv.get("adjacencies") or {}).get("adjacency")):
                st = a.get("state") or {}
                pm.adj_state = str(st.get("adjacency-state", "")).title()
                pm.adj_level = _LEVEL.get(st.get("adjacency-type", ""), level)
                pm.neighbour = str(st.get("system-id", ""))
        out.append(pm)
    return out


LLDP = DiscoverySource(key="demo-lldp", label="LLDP neighbours (all vendors)", rpc={"get-lldp-neighbors": {}},
                       parse=parse_lldp, description="get-lldp-neighbors on every selected device")
ISIS = Overlay(key="demo-isis", label="IS-IS metrics (all vendors)", rpcs={"get-isis-state": {"get-isis-state": {}}},
               parse=parse_isis, description="IS-IS interface metrics and adjacency state")


def install() -> None:
    """Make the vendor-neutral demo sources the only ones."""
    network.SOURCES.clear()
    network.SOURCES[LLDP.key] = LLDP
    network.OVERLAYS.clear()
    network.OVERLAYS[ISIS.key] = ISIS
    network_views.DEFAULT_SOURCE, network_views.DEFAULT_OVERLAY = LLDP.key, ISIS.key
