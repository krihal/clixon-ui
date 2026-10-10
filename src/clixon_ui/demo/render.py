"""What each demo service would configure on a device (the fake controller has no service scripts).

Pure: a service instance in, `{device: config fragment}` out, in the models of each device's vendor:
OpenConfig (Arista, generic) and Junos native. The controller turns fragments into diff lines."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

OC_NI = "openconfig-network-instance:network-instances"
JUNOS = "junos-conf-root:configuration"


# ---------------------------------------------------------------------------------------------- what the services mean
def _vlan_facts(entry: dict) -> dict[str, dict]:
    """device -> {id, name, ports: [(interface, access|trunk)]}"""
    out: dict[str, dict] = {}
    for m in entry.get("member", []):
        d = out.setdefault(m["device"], {"id": int(entry["vlan-id"]), "name": entry["service-name"], "description": entry.get("description") or entry["service-name"], "ports": []})
        d["ports"].append((m["interface"], m.get("mode", "access")))
    return out


def _bgp_facts(entry: dict) -> dict[str, dict]:
    return {entry["device"]: {"address": entry["neighbor-address"], "asn": entry["peer-as"], "description": entry.get("description", ""),
                              "max": int(entry.get("max-prefixes", 1000)), "shutdown": bool(entry.get("shutdown", False))}}


def _ntp_facts(entry: dict) -> dict[str, dict]:
    return {d: {"servers": list(entry.get("server", [])), "prefer_first": entry.get("prefer-first", True)} for d in entry.get("device", [])}


def _interface_facts(entry: dict) -> dict[str, dict]:
    return {entry["device"]: {"name": entry["interface"], "description": entry.get("description", ""), "mtu": int(entry.get("mtu", 1500)), "enabled": entry.get("enabled", True)}}


def _route_facts(entry: dict) -> dict[str, dict]:
    return {entry["device"]: {"routes": [(r["prefix"], r["next-hop"], int(r.get("distance", 5))) for r in entry.get("route", [])]}}


def _syslog_facts(entry: dict) -> dict[str, dict]:
    return {d: {"servers": [(x["host"], int(x.get("port", 514)), x.get("severity", "warning")) for x in entry.get("server", [])]} for d in entry.get("device", [])}


def _lag_facts(entry: dict) -> dict[str, dict]:
    return {entry["device"]: {"id": int(entry["lag-id"]), "description": entry.get("description", ""), "members": list(entry.get("member", [])),
                              "min_links": int(entry.get("min-links", 1)), "lacp": entry.get("lacp", "active")}}


# ---------------------------------------------------------------------------------------------- OpenConfig
def _oc_vlan(f: dict) -> dict:
    ifaces = [{"name": i, "openconfig-if-ethernet:ethernet": {"openconfig-vlan:switched-vlan": {"config": (
        {"interface-mode": "ACCESS", "access-vlan": f["id"]} if mode == "access" else {"interface-mode": "TRUNK", "trunk-vlans": [f["id"]]})}}} for i, mode in f["ports"]]
    return {OC_NI: {"network-instance": [{"name": "default", "openconfig-network-instance:vlans": {"vlan": [
        {"vlan-id": f["id"], "config": {"vlan-id": f["id"], "name": f["description"], "status": "ACTIVE"}}]}}]},
        "openconfig-interfaces:interfaces": {"interface": ifaces}}


def _oc_bgp(f: dict) -> dict:
    cfg = {"neighbor-address": f["address"], "peer-as": f["asn"], "enabled": not f["shutdown"], **({"description": f["description"]} if f["description"] else {})}
    return {OC_NI: {"network-instance": [{"name": "default", "protocols": {"protocol": [{"name": "bgp", "identifier": "openconfig-policy-types:BGP", "bgp": {
        "neighbors": {"neighbor": [{"neighbor-address": f["address"], "config": cfg, "afi-safis": {"afi-safi": [{
            "afi-safi-name": "openconfig-bgp-types:IPV4_UNICAST", "ipv4-unicast": {"prefix-limit": {"config": {"max-prefixes": f["max"]}}}}]}}]}}}]}}]}}


def _oc_ntp(f: dict) -> dict:
    return {"openconfig-system:system": {"ntp": {"servers": {"server": [
        {"address": s, "config": {"address": s, **({"prefer": True} if i == 0 and f["prefer_first"] else {})}} for i, s in enumerate(f["servers"])]}}}}


def _oc_interface(f: dict) -> dict:
    return {"openconfig-interfaces:interfaces": {"interface": [{"name": f["name"], "config": {
        "name": f["name"], "mtu": f["mtu"], "enabled": f["enabled"], **({"description": f["description"]} if f["description"] else {})}}]}}


def _oc_route(f: dict) -> dict:
    statics = [{"prefix": p, "config": {"prefix": p}, "next-hops": {"next-hop": [{"index": "1", "config": {"index": "1", "next-hop": nh, "metric": d}}]}} for p, nh, d in f["routes"]]
    return {OC_NI: {"network-instance": [{"name": "default", "protocols": {"protocol": [{
        "name": "static", "identifier": "openconfig-policy-types:STATIC", "static-routes": {"static": statics}}]}}]}}


def _oc_syslog(f: dict) -> dict:
    return {"openconfig-system:system": {"logging": {"remote-servers": {"remote-server": [
        {"host": h, "config": {"host": h, "remote-port": port}, "selectors": {"selector": [{"facility": "openconfig-system-logging:ALL", "severity": sev.upper(),
                                                                                           "config": {"facility": "openconfig-system-logging:ALL", "severity": sev.upper()}}]}}
        for h, port, sev in f["servers"]]}}}}


def _oc_lag(f: dict) -> dict:
    name = f"Port-Channel{f['id']}"
    ifaces = [{"name": name, "config": {"name": name, "type": "iana-if-type:ieee8023adLag", **({"description": f["description"]} if f["description"] else {})},
               "openconfig-if-aggregate:aggregation": {"config": {"lag-type": "LACP", "min-links": f["min_links"]}}}]
    ifaces += [{"name": m, "openconfig-if-ethernet:ethernet": {"config": {"openconfig-if-aggregate:aggregate-id": name}}} for m in f["members"]]
    return {"openconfig-interfaces:interfaces": {"interface": ifaces}}


# ---------------------------------------------------------------------------------------------- Junos
def _junos_vlan(f: dict) -> dict:
    return {JUNOS: {"vlans": {"vlan": [{"name": f["name"], "vlan-id": f["id"]}]}, "interfaces": {"interface": [
        {"name": i, "unit": [{"name": "0", "family": {"ethernet-switching": {"interface-mode": mode, "vlan": {"members": [f["name"]]}}}}]} for i, mode in f["ports"]]}}}


def _junos_bgp(f: dict) -> dict:
    return {JUNOS: {"protocols": {"bgp": {"group": [{"name": "ebgp-peers", "neighbor": [{
        "name": f["address"], **({"description": f["description"]} if f["description"] else {}), "peer-as": f["asn"],
        "family": {"inet": {"unicast": {"prefix-limit": {"maximum": f["max"]}}}}, **({"disable": [None]} if f["shutdown"] else {})}]}]}}}}


def _junos_ntp(f: dict) -> dict:
    return {JUNOS: {"system": {"ntp": {"server": [{"name": s, **({"prefer": [None]} if i == 0 and f["prefer_first"] else {})} for i, s in enumerate(f["servers"])]}}}}


def _junos_interface(f: dict) -> dict:
    return {JUNOS: {"interfaces": {"interface": [{"name": f["name"], **({"description": f["description"]} if f["description"] else {}), "mtu": f["mtu"],
                                                  **({} if f["enabled"] else {"disable": [None]})}]}}}


def _junos_route(f: dict) -> dict:
    return {JUNOS: {"routing-options": {"static": {"route": [{"name": p, "next-hop": [nh], "preference": {"metric-value": d}} for p, nh, d in f["routes"]]}}}}


def _junos_syslog(f: dict) -> dict:
    return {JUNOS: {"system": {"syslog": {"host": [{"name": h, "contents": [{"name": "any", sev: [None]}], "port": port} for h, port, sev in f["servers"]]}}}}


def _junos_lag(f: dict) -> dict:
    name = f"ae{f['id']}"
    ifaces = [{"name": name, **({"description": f["description"]} if f["description"] else {}), "aggregated-ether-options": {
        "minimum-links": f["min_links"], "lacp": {f["lacp"]: [None]}}}]
    ifaces += [{"name": m, "gigether-options": {"ieee-802.3ad": {"bundle": name}}} for m in f["members"]]
    return {JUNOS: {"interfaces": {"interface": ifaces}}}


FACTS: dict[str, Callable[[dict], dict[str, dict]]] = {
    "vlan": _vlan_facts, "bgp-peering": _bgp_facts, "ntp": _ntp_facts,
    "interface-config": _interface_facts, "static-route": _route_facts, "syslog": _syslog_facts, "lag": _lag_facts}
FORMATS: dict[str, dict[str, Callable[[dict], dict]]] = {
    "openconfig": {"vlan": _oc_vlan, "bgp-peering": _oc_bgp, "ntp": _oc_ntp, "interface-config": _oc_interface, "static-route": _oc_route,
                   "syslog": _oc_syslog, "lag": _oc_lag},
    "juniper": {"vlan": _junos_vlan, "bgp-peering": _junos_bgp, "ntp": _junos_ntp, "interface-config": _junos_interface, "static-route": _junos_route,
                "syslog": _junos_syslog, "lag": _junos_lag},
}
FORMATS["arista-eos"] = FORMATS["openconfig"]


def fragments(kind: str, entry: dict[str, Any], vendor_of: Callable[[str], str]) -> dict[str, dict]:
    """Device -> config fragment for one service instance of type `kind` (the local name of its YANG list)."""
    if not entry or kind not in FACTS:
        return {}
    return {dev: FORMATS.get(vendor_of(dev), FORMATS["openconfig"])[kind](facts) for dev, facts in FACTS[kind](entry).items()}
