"""Fake data for the demo controller: three Juniper-like devices, a few services, LLDP and IS-IS replies.

Everything here is invented. Host names, addresses and customers do not exist."""

from __future__ import annotations

import copy
from datetime import UTC, datetime, timedelta

CTRL = "clixon-controller"
DOMAIN = "demo.example.net"

# name -> (loopback, [(port, peer, peer port, ip with /31, isis metric)]) ; a link is listed at both ends
DEVICES = {
    "demo-mx-1": ("10.255.0.1", "mx204"),
    "demo-mx-2": ("10.255.0.2", "mx204"),
    "demo-ptx-1": ("10.255.0.3", "ptx10001-36mr"),
}
# (device, port, peer, peer port, own address, metric, lag)
LINKS = [
    ("demo-mx-1", "et-0/0/0", "demo-mx-2", "et-0/0/0", "10.1.0.0/31", 100, ""),
    ("demo-mx-2", "et-0/0/0", "demo-mx-1", "et-0/0/0", "10.1.0.1/31", 100, ""),
    ("demo-mx-1", "et-0/0/1", "demo-ptx-1", "et-0/0/1", "10.1.1.0/31", 50, "ae0"),
    ("demo-ptx-1", "et-0/0/1", "demo-mx-1", "et-0/0/1", "10.1.1.1/31", 50, "ae0"),
    ("demo-mx-2", "et-0/0/1", "demo-ptx-1", "et-0/0/2", "10.1.2.0/31", 50, ""),
    ("demo-ptx-1", "et-0/0/2", "demo-mx-2", "et-0/0/1", "10.1.2.1/31", 80, ""),  # the two ends disagree on the metric
]
EXTERNAL = [  # neighbours that are not managed devices: (device, port, system name, remote port)
    ("demo-ptx-1", "et-0/0/10", "core-sw-1." + DOMAIN, "xe-0/0/4"),
    ("demo-mx-2", "ge-0/1/0", "customer-edge-7." + DOMAIN, "ge-0/0/0"),
]
CUSTOMER_PORTS = {"demo-mx-1": [("ge-0/1/0", "Customer Alpha, uplink")], "demo-mx-2": [("ge-0/1/0", "Customer Beta, uplink")]}


def now() -> datetime:
    return datetime.now(UTC)


def ts(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def device_config(name: str) -> dict:
    """The mounted Junos configuration of one device (a few KB instead of 30 MB)."""
    loopback, model = DEVICES[name]
    ifaces: dict[str, dict] = {}
    for dev, port, peer, peer_port, addr, _metric, lag in LINKS:
        if dev != name:
            continue
        ifaces[port] = {"name": port, "description": f"to {peer} {peer_port}",
                        **({"gigether-options": {"ieee-802.3ad": {"bundle": lag}}} if lag else
                           {"unit": [{"name": "0", "family": {"inet": {"address": [{"name": addr}]}, "iso": {}}}]})}
    for port, descr in CUSTOMER_PORTS.get(name, []):
        ifaces[port] = {"name": port, "description": descr, "mtu": 9192,
                        "unit": [{"name": "0", "family": {"inet": {"address": [{"name": f"192.0.2.{10 + len(ifaces)}/30"}]}}}]}
    if any(link[6] for link in LINKS if link[0] == name):
        ifaces["ae0"] = {"name": "ae0", "description": "LAG to the core",
                         "aggregated-ether-options": {"lacp": {"active": [None]}},
                         "unit": [{"name": "0", "family": {"inet": {"address": [{"name": "10.1.1." + ("0/31" if name == "demo-mx-1" else "1/31")}]}, "iso": {}}}]}
    ifaces["lo0"] = {"name": "lo0", "unit": [{"name": "0", "family": {
        "inet": {"address": [{"name": f"{loopback}/32"}]},
        "iso": {"address": [{"name": f"49.0001.0100.{loopback.split('.')[-1].zfill(4)}.00"}]}}}]}
    isis_ifaces = [{"name": f"{i}.0", "point-to-point": [None], "level": [{"name": "2", "metric": next(
        (m for d, p, _a, _b, _c, m, _l in LINKS if d == name and (p == i or i == "ae0")), 10)}]}
        for i in ifaces if i != "lo0" and i not in CUSTOMER_PORTS.get(name, [])]
    return {"junos-conf-root:configuration": {
        "junos-conf-system:system": {
            "host-name": name, "domain-name": DOMAIN,
            "services": {"ssh": {"root-login": "deny"}, "netconf": {"ssh": {}}},
            "login": {"user": [{"name": "demo", "class": "super-user", "authentication": {"ssh-ed25519": [{"name": "ssh-ed25519 AAAADEMO demo@example.net"}]}}]},
            "ntp": {"server": [{"name": "192.0.2.123"}]},
        },
        "junos-conf-chassis:chassis": {"aggregated-devices": {"ethernet": {"device-count": 2}}},
        "junos-conf-interfaces:interfaces": {"interface": [ifaces[k] for k in sorted(ifaces)]},
        "junos-conf-routing-options:routing-options": {"router-id": loopback, "autonomous-system": {"as-number": "64512"}},
        "junos-conf-protocols:protocols": {
            "isis": {"level": [{"name": "1", "disable": [None]}], "interface": isis_ifaces},
            "lldp": {"interface": [{"name": "all"}]},
        },
        "junos-conf-policy-options:policy-options": {"policy-statement": [
            {"name": "export-loopback", "term": [{"name": "lo", "from": {"route-filter": [{"name": f"{loopback}/32", "exact": [None]}]}, "then": {"accept": [None]}}]}]},
    }}


def device_entry(name: str) -> dict:
    """The configuration of a device as the controller keeps it (settings only; `config` is held apart)."""
    return {"name": name, "description": f"Demo router ({DEVICES[name][1]})", "enabled": "true", "device-profile": "junos-default",
            "addr": f"{name}.{DOMAIN}", "device-type": "juniper"}


def device_state(name: str, started: datetime) -> dict:
    return {"conn-state": "OPEN", "conn-state-timestamp": ts(started - timedelta(hours=7)),
            "sync-timestamp": ts(started - timedelta(hours=6, minutes=50)), "stable-timestamp": ts(started - timedelta(hours=6, minutes=49)),
            "capabilities": {"capability": ["urn:ietf:params:netconf:base:1.1", "urn:ietf:params:netconf:capability:candidate:1.0",
                                            "urn:ietf:params:netconf:capability:validate:1.1", "http://xml.juniper.net/netconf/junos/1.0"]}}


def inventory() -> dict:
    """The `clixon-controller:devices` container (without per-device config) of a fresh controller."""
    return {
        "device-timeout": 600,
        "device-group": [
            {"name": "all-routers", "description": "Every demo router", "device-name": list(DEVICES)},
            {"name": "mx-routers", "description": "MX series edge routers", "device-name": ["demo-mx-1", "demo-mx-2"]},
        ],
        "device-profile": [{"name": "junos-default", "description": "Juniper devices reachable over NETCONF/SSH", "user": "demo",
                            "conn-type": "NETCONF_SSH", "port": 830, "yang-config": "BIND"}],
        "template": [{"name": "ntp-server", "description": "Set the NTP server", "variables": {"variable": [{"name": "SERVER", "description": "NTP server address"}]},
                      "config": {"junos-conf-root:configuration": {"junos-conf-system:system": {"ntp": {"server": [{"name": "${SERVER}"}]}}}}}],
        "rpc-template": [
            {"name": "show-version", "description": "Junos version and hardware model", "config": {"get-system-information": {}}},
            {"name": "show-interface", "description": "Status of one interface", "variables": {"variable": [{"name": "IFACE", "description": "Interface name, e.g. et-0/0/0"}]},
             "config": {"get-interface-information": {"interface-name": "${IFACE}"}}},
            {"name": "show-isis-adjacencies", "description": "IS-IS adjacencies", "config": {"get-isis-adjacency-information": {}}},
        ],
        "device": [device_entry(n) for n in DEVICES],
    }


def services() -> dict:
    return {
        "service-timeout": 60,
        "interface-customer:interface-customer": [{
            "service-name": "customer-alpha", "description": "Customer Alpha, 10G uplink", "testing": False,
            "serviceID": [{"service-id": "ALPHA-0001", "zinotag": "ZT-1001", "description": "Alpha primary uplink",
                           "device": {"name": "demo-mx-1", "interface": [{"interface-name": "ge-0/1/0", "description": "Customer Alpha", "speed": "10g", "mtu": 9192}]}}],
            "created": {"path": ["/clixon-controller:devices/device=demo-mx-1"]},
        }, {
            "service-name": "customer-beta", "description": "Customer Beta, 10G uplink", "testing": False,
            "serviceID": [{"service-id": "BETA-0001", "zinotag": "ZT-2001", "description": "Beta primary uplink",
                           "device": {"name": "demo-mx-2", "interface": [{"interface-name": "ge-0/1/0", "description": "Customer Beta", "speed": "10g", "mtu": 9192}]}}],
            "created": {"path": ["/clixon-controller:devices/device=demo-mx-2"]},
        }],
        "l2c:l2c": [{
            "service-name": "alpha-beta-l2", "description": "Layer 2 circuit between Alpha and Beta",
            "connection": [{"service-id": "L2-0001", "description": "Alpha to Beta",
                            "endpoint-a": {"device": {"name": "demo-mx-1", "interface": "ge-0/1/0"}, "interface-customer-ref": {"service-name": "customer-alpha"}},
                            "endpoint-b": {"device": {"name": "demo-mx-2", "interface": "ge-0/1/0"}, "interface-customer-ref": {"service-name": "customer-beta"}}}],
            "created": {"path": ["/clixon-controller:devices/device=demo-mx-1", "/clixon-controller:devices/device=demo-mx-2"]},
        }],
    }


def nacm() -> dict:
    return {"enable-nacm": True, "read-default": "permit", "write-default": "deny", "exec-default": "permit",
            "groups": {"group": [{"name": "admin", "user-name": ["demo"]}]},
            "rule-list": [{"name": "admin-rules", "group": ["admin"],
                           "rule": [{"name": "permit-all", "module-name": "*", "access-operations": "*", "action": "permit"}]}]}


def initial_transactions() -> list[dict]:
    t0 = now() - timedelta(hours=6, minutes=45)
    out = []
    for tid, descr, user, who in [(1, "Pull config of demo-mx-1", "demo", "demo-mx-1"), (2, "Pull config of demo-mx-2", "demo", "demo-mx-2"),
                                  (3, "Pull config of demo-ptx-1", "demo", "demo-ptx-1"), (4, "Commit: interface-customer 'customer-alpha'", "demo", "demo-mx-1")]:
        start = t0 + timedelta(minutes=tid * 3)
        out.append({"tid": str(tid), "description": descr, "state": "DONE", "result": "SUCCESS", "username": user, "timestamp0": ts(start),
                    "timestamp": ts(start + timedelta(seconds=2)), "devices": {"device": [{"name": who, "result": "SUCCESS"}]}})
    return out


# ---------------------------------------------------------------------------------------------- RPC replies (device-rpc-result)
def lldp_reply(device: str) -> dict:
    n = []
    for dev, port, peer, peer_port, *_rest in LINKS:
        if dev == device:
            lag = next((x[6] for x in LINKS if x[0] == dev and x[1] == port), "")
            n.append({"lldp-local-port-id": port, "lldp-local-parent-interface-name": lag or "-", "lldp-remote-chassis-id": "aa:bb:cc:00:00:" + peer[-1].zfill(2),
                      "lldp-remote-port-id": peer_port, "lldp-remote-port-description": peer_port, "lldp-remote-system-name": f"{peer}.{DOMAIN}"})
    for dev, port, system, remote_port in EXTERNAL:
        if dev == device:
            n.append({"lldp-local-port-id": port, "lldp-local-parent-interface-name": "-", "lldp-remote-chassis-id": "de:ad:be:ef:00:01",
                      "lldp-remote-port-id": remote_port, "lldp-remote-port-description": remote_port, "lldp-remote-system-name": system})
    return {"lldp-neighbors-information": {"lldp-neighbor-information": n}}


def isis_interfaces_reply(device: str) -> dict:
    ports = []
    seen = set()
    for dev, port, _peer, _pp, _addr, metric, lag in LINKS:
        name = f"{lag or port}.0"
        if dev == device and name not in seen:
            seen.add(name)
            ports.append({"interface-name": name, "circuit-type": "2", "metric-one": "10", "metric-two": str(metric),
                          "isis-interface-state-two": "Point to Point", "isis-interface-state-one": "Point to Point"})
    ports.append({"interface-name": "lo0.0", "circuit-type": "2", "metric-one": "0", "metric-two": "0", "isis-interface-state-two": "Passive"})
    return {"isis-interface-information": {"isis-interface": ports}}


def isis_adjacencies_reply(device: str) -> dict:
    adj, seen = [], set()
    for dev, port, peer, _pp, _addr, _metric, lag in LINKS:
        name = f"{lag or port}.0"
        if dev == device and name not in seen:
            seen.add(name)
            adj.append({"interface-name": name, "system-name": peer, "level": "2", "adjacency-state": "Up", "holdtime": "26"})
    return {"isis-adjacency-information": {"isis-adjacency": adj}}


def version_reply(device: str) -> dict:
    return {"system-information": {"hardware-model": DEVICES[device][1], "os-name": "junos", "os-version": "24.2R1.17",
                                   "host-name": device, "serial-number": "DEMO" + str(abs(hash(device)) % 100000).zfill(5)}}


def interface_reply(device: str, name: str) -> dict:
    return {"interface-information": {"physical-interface": [{
        "name": name, "admin-status": "up", "oper-status": "up", "speed": "100Gbps", "mtu": "9192", "link-level-type": "Ethernet",
        "input-bytes": "81920473216", "output-bytes": "77711206400", "input-errors": "0", "output-errors": "0"}]}}


def chassis_reply(device: str) -> dict:
    return {"chassis-inventory": {"chassis": {"name": "Chassis", "description": DEVICES[device][1].upper(), "serial-number": "DEMO12345"}}}


def reply(device: str, rpc: dict) -> dict:
    """Canned answer for one inline device RPC; the fake controller does not know most RPCs."""
    name = next(iter(rpc), "")
    local = name.rpartition(":")[2]
    body = rpc.get(name) or {}
    if local == "get-lldp-interface-neighbors":
        return lldp_reply(device)
    if local == "get-isis-interface-information":
        return isis_interfaces_reply(device)
    if local == "get-isis-adjacency-information":
        return isis_adjacencies_reply(device)
    if local in ("get-system-information", "get-system-uptime-information"):
        return version_reply(device)
    if local == "get-interface-information":
        return interface_reply(device, (body or {}).get("interface-name") or "et-0/0/0")
    if local == "get-chassis-inventory":
        return chassis_reply(device)
    if local == "command":
        cmd = str(body)
        return {"output": f"{device}> {cmd}\n" + ("Hostname: " + device + "\nModel: " + DEVICES[device][1] + "\nJunos: 24.2R1.17\n" if "version" in cmd else "(demo output)\n")}
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
