"""Fake data for the demo controller: three OpenConfig-style switches, a few services, LLDP and IS-IS replies.

Everything here is invented. Host names, addresses and customers do not exist."""

from __future__ import annotations

import copy
from datetime import UTC, datetime, timedelta

CTRL = "clixon-controller"
DOMAIN = "demo.example.net"

# name -> (loopback, hardware, device-type, profile). Configuration and replies follow each vendor's public models: OpenConfig
# (Arista, generic) and Juniper's native Junos models. No model files are bundled, see NOTICE.md.
DEVICES = {
    "demo-mx-1": ("10.255.0.1", "MX204", "juniper", "juniper-junos"),
    "demo-mx-2": ("10.255.0.2", "MX480", "juniper", "juniper-junos"),
    "demo-ptx-1": ("10.255.0.3", "PTX10001-36MR", "juniper", "juniper-junos"),
    "demo-eos-1": ("10.255.0.11", "DCS-7280SR3-48YC8", "arista-eos", "arista-eos"),
    "demo-eos-2": ("10.255.0.12", "DCS-7280SR3-48YC8", "arista-eos", "arista-eos"),
    "demo-eos-3": ("10.255.0.13", "DCS-7050CX3-32S", "arista-eos", "arista-eos"),
    "demo-eos-4": ("10.255.0.14", "DCS-7050CX3-32S", "arista-eos", "arista-eos"),
    "demo-openconfig-1": ("10.255.0.21", "Generic OpenConfig switch", "openconfig", "openconfig-generic"),
    "demo-openconfig-2": ("10.255.0.22", "Generic OpenConfig switch", "openconfig", "openconfig-generic"),
    "demo-openconfig-3": ("10.255.0.23", "Generic OpenConfig switch", "openconfig", "openconfig-generic"),
}


def kind(device: str) -> str:
    """Vendor flavour of a device: arista-eos, openconfig or juniper (unknown devices count as openconfig)."""
    return DEVICES[device][2] if device in DEVICES else "openconfig"


# Core: three Junos routers. Aggregation: four Arista switches, dual-homed. Access: three OpenConfig switches.
# (a, b, IS-IS metric seen by a, metric seen by b). Ports and addresses are handed out in this order.
_PAIRS = [
    ("demo-mx-1", "demo-mx-2", 10, 10), ("demo-mx-1", "demo-ptx-1", 10, 10), ("demo-mx-2", "demo-ptx-1", 20, 20),
    ("demo-eos-1", "demo-mx-1", 50, 50), ("demo-eos-1", "demo-mx-2", 50, 50), ("demo-eos-2", "demo-mx-1", 50, 50),
    ("demo-eos-2", "demo-ptx-1", 50, 50), ("demo-eos-3", "demo-mx-2", 50, 50), ("demo-eos-4", "demo-ptx-1", 50, 50),
    ("demo-eos-1", "demo-eos-2", 100, 100), ("demo-eos-3", "demo-eos-4", 100, 100),
    ("demo-openconfig-1", "demo-eos-1", 50, 50), ("demo-openconfig-1", "demo-eos-2", 50, 80),  # the two ends disagree on this metric
    ("demo-openconfig-2", "demo-eos-3", 50, 50), ("demo-openconfig-3", "demo-eos-4", 50, 50), ("demo-openconfig-2", "demo-openconfig-3", 100, 100),
]


def _links() -> list[tuple[str, str, str, str, str, int]]:
    """(device, port, peer, peer port, own address, metric): every link twice, once from each end."""
    used: dict[str, int] = {}

    def port(device: str) -> str:
        used[device] = used.get(device, 0) + 1
        return f"et-0/0/{used[device] - 1}" if kind(device) == "juniper" else f"Ethernet{used[device]}"

    out = []
    for i, (a, b, ma, mb) in enumerate(_PAIRS):
        pa, pb = port(a), port(b)
        out += [(a, pa, b, pb, f"10.1.{i}.0/31", ma), (b, pb, a, pa, f"10.1.{i}.1/31", mb)]
    return out


LINKS = _links()
EXTERNAL = [  # neighbours that are not managed devices: (device, port, system name, remote port)
    ("demo-mx-1", "ge-0/1/0", "peering-router-3." + DOMAIN, "xe-0/0/2"),
    ("demo-ptx-1", "et-0/0/30", "core-sw-1." + DOMAIN, "Ethernet12"),
    ("demo-eos-4", "Ethernet10", "customer-edge-7." + DOMAIN, "Ethernet1"),
]
CUSTOMER_PORTS = {"demo-eos-1": [("Ethernet10", "Customer Alpha uplink")], "demo-eos-2": [("Ethernet20", "Customer Beta uplink")]}


def now() -> datetime:
    return datetime.now(UTC)


def ts(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%S.%fZ")




def _iface(name: str, description: str, addr: str | None = None, mtu: int = 9214, vlan: dict | None = None) -> dict:
    entry: dict = {"name": name, "config": {"name": name, "type": "iana-if-type:ethernetCsmacd", "mtu": mtu, "enabled": True,
                                            **({"description": description} if description else {})}}
    if addr:
        ip, _, plen = addr.partition("/")
        entry["subinterfaces"] = {"subinterface": [{"index": 0, "config": {"index": 0}, "openconfig-if-ip:ipv4": {
            "addresses": {"address": [{"ip": ip, "config": {"ip": ip, "prefix-length": int(plen)}}]}}}]}
    if vlan:
        entry["openconfig-if-ethernet:ethernet"] = {"openconfig-vlan:switched-vlan": {"config": vlan}}
    return entry


def device_config(name: str) -> dict:
    """The mounted configuration of one device (a few KB), in the models of its vendor."""
    return (_junos_config if kind(name) == "juniper" else _oc_config)(name)


def _junos_config(name: str) -> dict:
    """Junos native model: one root container."""
    loopback = DEVICES[name][0]
    ifaces: dict[str, dict] = {}
    for dev, port, peer, peer_port, addr, _metric in LINKS:
        if dev == name:
            ifaces[port] = {"name": port, "description": f"to {peer} {peer_port}",
                            "unit": [{"name": "0", "family": {"inet": {"address": [{"name": addr}]}, "iso": {}}}]}
    for i, (dev, port, system, _remote) in enumerate(x for x in EXTERNAL if x[0] == name):
        ifaces[port] = {"name": port, "description": system.split(".")[0], "mtu": 9192,
                        "unit": [{"name": "0", "family": {"inet": {"address": [{"name": f"192.0.2.{10 + 4 * i}/30"}]}}}]}
    ifaces["lo0"] = {"name": "lo0", "unit": [{"name": "0", "family": {"inet": {"address": [{"name": f"{loopback}/32"}]}, "iso": {}}}]}
    isis = [{"name": f"{p}.0", "point-to-point": [None], "level": [{"name": "2", "metric": m}]} for d, p, _a, _b, _c, m in LINKS if d == name]
    return {"junos-conf-root:configuration": {
        "junos-conf-system:system": {"host-name": name, "domain-name": DOMAIN, "services": {"ssh": {"root-login": "deny"}, "netconf": {"ssh": {}}},
                                     "ntp": {"server": [{"name": "192.0.2.123", "prefer": [None]}]}},
        "junos-conf-interfaces:interfaces": {"interface": [ifaces[k] for k in sorted(ifaces)]},
        "junos-conf-vlans:vlans": {"vlan": [{"name": "management", "vlan-id": 10}]},
        "junos-conf-protocols:protocols": {"isis": {"level": [{"name": "1", "disable": [None]}], "interface": isis}, "lldp": {"interface": [{"name": "all"}]}},
    }}


def _oc_config(name: str) -> dict:
    """OpenConfig models: several top-level containers, no single root."""
    loopback = DEVICES[name][0]
    ifaces: dict[str, dict] = {}
    for dev, port, peer, peer_port, addr, _metric in LINKS:
        if dev == name:
            ifaces[port] = _iface(port, f"to {peer} {peer_port}", addr)
    for port, descr in CUSTOMER_PORTS.get(name, []):
        ifaces[port] = _iface(port, descr, vlan={"interface-mode": "ACCESS", "access-vlan": 100})
    ifaces["Loopback0"] = _iface("Loopback0", "Router id", f"{loopback}/32", mtu=65535)
    return {
        "openconfig-system:system": {
            "config": {"hostname": name, "domain-name": DOMAIN},
            "ntp": {"config": {"enabled": True}, "servers": {"server": [
                {"address": "192.0.2.123", "config": {"address": "192.0.2.123", "prefer": True}}]}},
        },
        "openconfig-interfaces:interfaces": {"interface": [ifaces[k] for k in sorted(ifaces)]},
        "openconfig-lldp:lldp": {"config": {"enabled": True, "hello-timer": 30}},
        "openconfig-network-instance:network-instances": {"network-instance": [{
            "name": "default", "config": {"name": "default", "type": "openconfig-network-instance-types:DEFAULT_INSTANCE"},
            "openconfig-network-instance:vlans": {"vlan": [
                {"vlan-id": 10, "config": {"vlan-id": 10, "name": "management", "status": "ACTIVE"}},
                {"vlan-id": 100, "config": {"vlan-id": 100, "name": "customer-alpha", "status": "ACTIVE"}}]},
        }]},
    }


def device_entry(name: str) -> dict:
    """The configuration of a device as the controller keeps it (settings only; `config` is held apart)."""
    _loopback, hardware, kind_, profile = DEVICES[name]
    return {"name": name, "description": f"Demo {'router' if kind_ == 'juniper' else 'switch'} ({hardware})", "enabled": "true", "device-profile": profile,
            "addr": f"{name}.{DOMAIN}", "device-type": kind_}


def device_state(name: str, started: datetime) -> dict:
    return {"conn-state": "OPEN", "conn-state-timestamp": ts(started - timedelta(hours=7)),
            "sync-timestamp": ts(started - timedelta(hours=6, minutes=50)), "stable-timestamp": ts(started - timedelta(hours=6, minutes=49)),
            "capabilities": {"capability": ["urn:ietf:params:netconf:base:1.1", "urn:ietf:params:netconf:capability:candidate:1.0",
                                            "urn:ietf:params:netconf:capability:validate:1.1", "http://openconfig.net/yang/interfaces"]}}


def inventory() -> dict:
    """The `clixon-controller:devices` container (without per-device config) of a fresh controller."""
    return {
        "device-timeout": 600,
        "device-group": [
            {"name": "all-switches", "description": "Every demo switch", "device-name": list(DEVICES)},
            {"name": "arista-eos", "description": "Arista EOS aggregation switches", "device-name": [d for d in DEVICES if kind(d) == "arista-eos"]},
            {"name": "core-routers", "description": "Juniper core routers", "device-name": [d for d in DEVICES if kind(d) == "juniper"]},
            {"name": "access-switches", "description": "OpenConfig access switches", "device-name": [d for d in DEVICES if kind(d) == "openconfig"]},
        ],
        "device-profile": [
            {"name": "arista-eos", "description": "Arista EOS over NETCONF/SSH", "user": "demo", "conn-type": "NETCONF_SSH", "port": 830, "yang-config": "BIND"},
            {"name": "openconfig-generic", "description": "Any device that speaks OpenConfig over NETCONF", "user": "demo", "conn-type": "NETCONF_SSH",
             "port": 830, "yang-config": "BIND"},
            {"name": "juniper-junos", "description": "Juniper devices over NETCONF/SSH", "user": "demo", "conn-type": "NETCONF_SSH", "port": 830, "yang-config": "BIND"},
        ],
        "template": [{"name": "ntp-server", "description": "Add an NTP server", "variables": {"variable": [{"name": "SERVER", "description": "NTP server address"}]},
                      "config": {"openconfig-system:system": {"ntp": {"servers": {"server": [{"address": "${SERVER}", "config": {"address": "${SERVER}"}}]}}}}}],
        "rpc-template": [
            {"name": "show-version", "description": "Hardware model and software version", "config": {"get-system-state": {}}},
            {"name": "show-interface", "description": "Status of one interface", "variables": {"variable": [{"name": "IFACE", "description": "Interface name, e.g. Ethernet1"}]},
             "config": {"get-interface-state": {"interface-name": "${IFACE}"}}},
            {"name": "show-lldp-neighbors", "description": "LLDP neighbours", "config": {"get-lldp-neighbors": {}}},
            {"name": "show-isis", "description": "IS-IS interfaces and adjacencies", "config": {"get-isis-state": {}}},
        ],
        "device": [device_entry(n) for n in DEVICES],
    }


def _paths(*devices: str) -> dict:
    return {"path": [f"/{CTRL}:devices/device={d}" for d in devices]}


def services() -> dict:
    return {
        "service-timeout": 60,
        "demo-vlan:vlan": [
            {"service-name": "customer-alpha-vlan", "description": "VLAN of customer Alpha", "vlan-id": 100,
             "member": [{"device": "demo-eos-1", "interface": "Ethernet10", "mode": "access"},
                        {"device": "demo-eos-2", "interface": "Ethernet20", "mode": "trunk"}],
             "created": _paths("demo-eos-1", "demo-eos-2")},
            {"service-name": "management-vlan", "description": "Out-of-band management", "vlan-id": 10,
             "member": [{"device": "demo-eos-1", "interface": "Ethernet30", "mode": "access"},
                        {"device": "demo-mx-1", "interface": "ge-0/1/1", "mode": "access"}],
             "created": _paths("demo-eos-1", "demo-mx-1")},
        ],
        "demo-bgp-peering:bgp-peering": [
            {"service-name": "transit-a", "description": "Transit provider A (documentation AS)", "device": "demo-mx-1",
             "neighbor-address": "192.0.2.1", "peer-as": 64496, "max-prefixes": 950000, "shutdown": False, "created": _paths("demo-mx-1")},
        ],
        "demo-interface-config:interface-config": [
            {"service-name": "alpha-uplink", "description": "Customer Alpha uplink", "device": "demo-eos-1", "interface": "Ethernet10", "mtu": 9214, "enabled": True,
             "created": _paths("demo-eos-1")},
            {"service-name": "peering-port", "description": "Peering router 3", "device": "demo-mx-1", "interface": "ge-0/1/0", "mtu": 9192, "enabled": True,
             "created": _paths("demo-mx-1")},
            {"service-name": "spare-port", "description": "Spare, shut down", "device": "demo-openconfig-2", "interface": "Ethernet20", "mtu": 1500, "enabled": False,
             "created": _paths("demo-openconfig-2")},
        ],
        "demo-static-route:static-route": [
            {"service-name": "edge-defaults", "description": "Default and documentation routes on the edge", "device": "demo-mx-2",
             "route": [{"prefix": "0.0.0.0/0", "next-hop": "192.0.2.1", "distance": 5}, {"prefix": "198.51.100.0/24", "next-hop": "192.0.2.5", "distance": 10}],
             "created": _paths("demo-mx-2")},
            {"service-name": "access-default", "description": "Default route of an access switch", "device": "demo-openconfig-1",
             "route": [{"prefix": "0.0.0.0/0", "next-hop": "10.1.11.1", "distance": 5}], "created": _paths("demo-openconfig-1")},
        ],
        "demo-syslog:syslog": [
            {"service-name": "site-syslog", "description": "Central log servers", "device": list(DEVICES),
             "server": [{"host": "192.0.2.50", "port": 514, "severity": "warning"}, {"host": "loghost.example.net", "port": 514, "severity": "info"}],
             "created": _paths(*DEVICES)},
        ],
        "demo-lag:lag": [
            {"service-name": "mx1-core-lag", "description": "Bundle towards the core", "device": "demo-mx-1", "lag-id": 0,
             "member": ["et-0/0/10", "et-0/0/11"], "min-links": 1, "lacp": "active", "created": _paths("demo-mx-1")},
            {"service-name": "eos1-uplink-lag", "description": "Uplink bundle", "device": "demo-eos-1", "lag-id": 1,
             "member": ["Ethernet5", "Ethernet6"], "min-links": 2, "lacp": "active", "created": _paths("demo-eos-1")},
        ],
        "properties": {"demo-defaults:demo-defaults": {"domain-name": "demo.example.net", "default-mtu": 9214, "contact": "noc@example.net"}},
        "demo-ntp:ntp": [
            {"service-name": "site-ntp", "description": "NTP servers for the whole site", "server": ["192.0.2.123", "198.51.100.7"],
             "device": list(DEVICES), "prefer-first": True, "created": _paths(*DEVICES)},
        ],
    }


def nacm() -> dict:
    return {"enable-nacm": True, "read-default": "permit", "write-default": "deny", "exec-default": "permit",
            "groups": {"group": [{"name": "admin", "user-name": ["demo"]}]},
            "rule-list": [{"name": "admin-rules", "group": ["admin"],
                           "rule": [{"name": "permit-all", "module-name": "*", "access-operations": "*", "action": "permit"}]}]}


def initial_transactions() -> list[dict]:
    t0 = now() - timedelta(hours=6, minutes=45)
    out = []
    for tid, descr, who in [(1, "Pull config of demo-eos-1", "demo-eos-1"), (2, "Pull config of demo-eos-2", "demo-eos-2"),
                            (3, "Pull config of demo-openconfig-1", "demo-openconfig-1"), (4, "Commit: vlan 'customer-alpha-vlan'", "demo-eos-1")]:
        start = t0 + timedelta(minutes=tid * 3)
        out.append({"tid": str(tid), "description": descr, "state": "DONE", "result": "SUCCESS", "username": "demo", "timestamp0": ts(start),
                    "timestamp": ts(start + timedelta(seconds=2)), "devices": {"device": [{"name": who, "result": "SUCCESS"}]}})
    return out


# ---------------------------------------------------------------------------------------------- RPC replies (device-rpc-result)
def _lldp_rows(device: str) -> list[tuple[str, str, str, str]]:
    """(local port, neighbour system name, neighbour port, neighbour chassis id) as the device sees it."""
    rows = [(port, f"{peer}.{DOMAIN}", peer_port, f"aa:bb:cc:00:{list(DEVICES).index(peer):02x}:01") for dev, port, peer, peer_port, *_r in LINKS if dev == device]
    return rows + [(port, system, remote_port, "de:ad:be:ef:00:01") for dev, port, system, remote_port in EXTERNAL if dev == device]


def lldp_reply(device: str) -> dict:
    """Junos: lldp-neighbors-information. Everything else: the openconfig-lldp state tree."""
    rows = _lldp_rows(device)
    if kind(device) == "juniper":
        return {"lldp-neighbors-information": {"lldp-neighbor-information": [
            {"lldp-local-port-id": port, "lldp-local-parent-interface-name": "-", "lldp-remote-chassis-id": chassis, "lldp-remote-port-id": remote_port,
             "lldp-remote-port-description": remote_port, "lldp-remote-system-name": system} for port, system, remote_port, chassis in rows]}}
    interfaces: dict[str, list] = {}
    for port, system, remote_port, chassis in rows:
        interfaces.setdefault(port, []).append({"id": remote_port, "state": {
            "id": remote_port, "system-name": system, "chassis-id": chassis, "chassis-id-type": "MAC_ADDRESS",
            "port-id": remote_port, "port-id-type": "INTERFACE_NAME", "port-description": remote_port}})
    return {"lldp": {"interfaces": {"interface": [{"name": port, "neighbors": {"neighbor": n}} for port, n in interfaces.items()]}}}


def isis_reply(device: str) -> dict:
    """Junos: isis-interface-information + isis-adjacency-information. Everything else: the openconfig-isis state tree."""
    mine = [(port, peer, metric) for dev, port, peer, _pp, _addr, metric in LINKS if dev == device]
    if kind(device) == "juniper":
        interfaces = [{"interface-name": f"{p}.0", "circuit-type": "2", "metric-one": "10", "metric-two": str(m),
                       "isis-interface-state-two": "Point to Point", "isis-interface-state-one": "Point to Point"} for p, _peer, m in mine]
        interfaces.append({"interface-name": "lo0.0", "circuit-type": "2", "metric-one": "0", "metric-two": "0", "isis-interface-state-two": "Passive"})
        adjacencies = [{"interface-name": f"{p}.0", "system-name": peer, "level": "2", "adjacency-state": "Up", "holdtime": "26"} for p, peer, _m in mine]
        return {"isis-interface-information": {"isis-interface": interfaces}, "isis-adjacency-information": {"isis-adjacency": adjacencies}}
    return {"isis": {"interfaces": {"interface": [{"interface-id": p, "state": {"circuit-type": "POINT_TO_POINT"}, "levels": {"level": [{
        "level-number": 2,
        "afi-safi": {"af": [{"afi-name": "openconfig-isis-types:IPV4", "safi-name": "openconfig-isis-types:UNICAST", "state": {"metric": m}}]},
        "adjacencies": {"adjacency": [{"system-id": peer, "state": {"system-id": peer, "adjacency-state": "UP", "adjacency-type": "LEVEL_2"}}]}}]}}
        for p, peer, m in mine]}}}


def _version(device: str) -> str:
    return {"arista-eos": "4.32.1F", "juniper": "24.2R1.17"}.get(kind(device), "1.0.0")


def system_reply(device: str) -> dict:
    hardware = DEVICES[device][1]
    serial = "DEMO" + str(sum(map(ord, device)) * 7).zfill(6)
    if kind(device) == "juniper":
        return {"system-information": {"hardware-model": hardware.lower(), "os-name": "junos", "os-version": _version(device), "host-name": device, "serial-number": serial}}
    return {"system": {"state": {"hostname": device, "domain-name": DOMAIN, "boot-time": "1760000000000000000", "software-version": _version(device),
                                 "hardware": hardware, "serial-no": serial}}}


def interface_reply(device: str, name: str) -> dict:
    if kind(device) == "juniper":
        return {"interface-information": {"physical-interface": [{"name": name, "admin-status": "up", "oper-status": "up", "speed": "100Gbps", "mtu": "9192",
                                                                   "input-bytes": "81920473216", "output-bytes": "77711206400", "input-errors": "0", "output-errors": "0"}]}}
    return {"interfaces": {"interface": [{"name": name, "state": {"name": name, "admin-status": "UP", "oper-status": "UP", "mtu": 9214, "counters": {
        "in-octets": "81920473216", "out-octets": "77711206400", "in-errors": "0", "out-errors": "0"}}}]}}


CLI = {
    "show version": lambda d: f"Hardware: {DEVICES[d][1]}\nHostname: {d}\nSoftware version: {_version(d)}\n",
    "show lldp neighbors": lambda d: "Port        Neighbor Device ID            Neighbor Port ID\n" + "".join(
        f"{p:<11} {n + '.' + DOMAIN:<29} {pp}\n" for dev, p, n, pp, *_r in LINKS if dev == d),
}


def reply(device: str, rpc: dict) -> dict:
    """Canned answer for one inline device RPC; the fake controller does not know most RPCs."""
    name = next(iter(rpc), "")
    local = name.rpartition(":")[2]
    body = rpc.get(name) or {}
    if local in ("get-lldp-neighbors", "get-lldp-interface-neighbors"):  # the first is the demo's vendor-neutral RPC
        return lldp_reply(device)
    if local in ("get-isis-state", "get-isis-interface-information", "get-isis-adjacency-information"):
        return isis_reply(device)
    if local in ("get-system-state", "get-system-uptime", "get-system-information", "get-system-uptime-information"):
        return system_reply(device)
    if local in ("get-interface-state", "get-interface-information"):
        return interface_reply(device, (body or {}).get("interface-name") or "Ethernet1")
    if local == "command":
        cmd = str(body).strip()
        make = CLI.get(cmd.lower())
        return {"output": make(device) if make else f"{device}# {cmd}\n(demo output)\n"}
    return {"output": f"The demo controller accepted {local} on {device} and did nothing."}


def fresh() -> dict:
    """Everything a freshly started demo controller holds."""
    started = now()
    return {
        "inventory": inventory(), "services": services(), "nacm": nacm(),
        "configs": {n: device_config(n) for n in DEVICES},
        "state": {n: device_state(n, started) for n in DEVICES},
        "transactions": initial_transactions(),
    }


def clone(x):
    return copy.deepcopy(x)
