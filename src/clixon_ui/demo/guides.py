"""The example guide of the demo: a software upgrade on one switch. Everything it calls is answered by the demo controller
(the reboot does nothing there); it shows each step type once."""

from __future__ import annotations

from sqlalchemy.orm import Session

from .. import guides

NAME = "Upgrade a switch (demo)"


def probe(body: dict) -> dict:
    return {"body": body, "target": {"devices": ["{{device}}"], "groups": []}}


GUIDE = {
    "name": NAME,
    "description": "A software upgrade as one guide: pick a switch, record its version, reboot it, wait until it answers again, "
                   "bring the controller back in sync. In this demo nothing really reboots.",
    "steps": [
        {"id": "s1", "type": "input", "title": "Which switch?", "var": "device", "label": "Switch to upgrade", "kind": "device",
         "default": "demo-eos-1", "choices": []},
        {"id": "s2", "type": "text", "title": "Before you start",
         "text": "You are about to upgrade **{{device}}**.\n\n- Make sure the maintenance window is open.\n- The next steps read the current "
                 "software version, reboot the switch and wait for it to come back."},
        {"id": "s3", "type": "confirm", "title": "Traffic drained",
         "question": "Has traffic been moved away from **{{device}}** (for example by raising the IS-IS metric)?"},
        {"id": "s4", "type": "rpc", "title": "Read the current version", **probe({"get-system-state": {}})},
        {"id": "s5", "type": "check", "title": "The switch answers as itself", **probe({"get-system-state": {}}),
         "rules": [{"path": "system/state/hostname", "op": "equals", "value": "{{device}}"}]},
        {"id": "s6", "type": "rpc", "title": "Reboot", **probe({"request-reboot": {}})},
        {"id": "s7", "type": "wait", "title": "Give it time to go down", "seconds": 5},
        {"id": "s8", "type": "wait", "title": "Wait until it is back",
         "until": {**probe({"get-system-state": {}}), "rules": [{"path": "system/state/hostname", "op": "equals", "value": "{{device}}"}],
                   "interval": 5, "timeout": 300}},
        {"id": "s9", "type": "connection", "title": "Reconnect from the controller", "operation": "RECONNECT", "device": "{{device}}"},
        {"id": "s10", "type": "pull", "title": "Pull the configuration", "device": "{{device}}", "merge": False},
    ],
}


HEALTH_NAME = "Health check before maintenance (demo)"

HEALTH_GUIDE = {
    "name": HEALTH_NAME,
    "description": "Read-only: confirm a switch answers as itself, a port is up, and look at its LLDP neighbours and IS-IS state "
                   "before you start work on it. Nothing is changed, so view-only users may run it. Written for the Arista "
                   "and OpenConfig demo switches.",
    "steps": [
        {"id": "s1", "type": "input", "title": "Which switch?", "var": "device", "label": "Switch to check", "kind": "device",
         "default": "demo-eos-1", "choices": []},
        {"id": "s2", "type": "input", "title": "Which port matters?", "var": "port", "label": "Port that must be up", "kind": "text",
         "default": "Ethernet1", "choices": []},
        {"id": "s3", "type": "check", "title": "The switch answers as itself", **probe({"get-system-state": {}}),
         "rules": [{"path": "system/state/hostname", "op": "equals", "value": "{{device}}"}]},
        {"id": "s4", "type": "check", "title": "The port is up", **probe({"get-interface-state": {"interface-name": "{{port}}"}}),
         "rules": [{"path": "interfaces/interface/state/admin-status", "op": "equals", "value": "UP"},
                   {"path": "interfaces/interface/state/oper-status", "op": "equals", "value": "UP"},
                   {"path": "interfaces/interface/state/counters/in-errors", "op": "equals", "value": "0"}]},
        {"id": "s5", "type": "rpc", "title": "LLDP neighbours", **probe({"get-lldp-neighbors": {}}),
         "show": [{"path": "lldp/interfaces/interface/name", "label": "Local port"},
                  {"path": "lldp/interfaces/interface/neighbors/neighbor/state/system-name", "label": "Neighbour"},
                  {"path": "lldp/interfaces/interface/neighbors/neighbor/state/port-id", "label": "Remote port"}]},
        {"id": "s6", "type": "rpc", "title": "IS-IS state", **probe({"get-isis-state": {}})},
        {"id": "s7", "type": "confirm", "title": "Does it look right?",
         "question": "Do the neighbours and IS-IS adjacencies of **{{device}}** match what you expect before maintenance?"},
    ],
}

def fields(base: str, *pairs: tuple[str, str]) -> list[dict]:
    """`show` fields: (path below `base`, label) pairs."""
    return [{"path": base + path, "label": label} for path, label in pairs]


SYSTEM = fields("system/state/", ("hostname", "Hostname"), ("hardware", "Hardware"), ("software-version", "Software"), ("serial-no", "Serial number"))
ISIS = fields("isis/interfaces/interface/", ("interface-id", "Port"), ("levels/level/afi-safi/af/state/metric", "Metric"),
              ("levels/level/adjacencies/adjacency/state/system-id", "Neighbour"),
              ("levels/level/adjacencies/adjacency/state/adjacency-state", "State"))

REPORT_GUIDE = {
    "name": "Device report (demo)",
    "description": "Read-only: gather what you usually look at on one switch (identity, one port, LLDP and IS-IS neighbours) as small "
                   "tables instead of raw replies. Every step uses the fields chosen in the editor; the full reply is one click away. "
                   "The port step also says when it counts as failed: the port must be up and must have no input errors.",
    "steps": [
        {"id": "s1", "type": "input", "title": "Which switch?", "var": "device", "label": "Switch", "kind": "device", "default": "demo-eos-1", "choices": []},
        {"id": "s2", "type": "input", "title": "Which port?", "var": "port", "label": "Port", "kind": "text", "default": "Ethernet1", "choices": []},
        {"id": "s3", "type": "rpc", "title": "Identity", **probe({"get-system-state": {}}), "show": SYSTEM},
        {"id": "s4", "type": "rpc", "title": "The port", **probe({"get-interface-state": {"interface-name": "{{port}}"}}),
         "success": [{"path": "interfaces/interface/state/oper-status", "op": "equals", "value": "UP"}],
         "failure": [{"path": "interfaces/interface/state/counters/in-errors", "op": "not-equals", "value": "0"}],
         "show": fields("interfaces/interface/state/", ("name", "Port"), ("admin-status", "Admin"), ("oper-status", "Oper"), ("mtu", "MTU"),
                        ("counters/in-octets", "In octets"), ("counters/out-octets", "Out octets"),
                        ("counters/in-errors", "In errors"), ("counters/out-errors", "Out errors"))},
        {"id": "s5", "type": "rpc", "title": "LLDP neighbours", **probe({"get-lldp-neighbors": {}}),
         "show": fields("lldp/interfaces/interface/", ("name", "Port"), ("neighbors/neighbor/state/system-name", "Neighbour"),
                        ("neighbors/neighbor/state/port-id", "Remote port"))},
        {"id": "s6", "type": "rpc", "title": "IS-IS adjacencies", **probe({"get-isis-state": {}}), "show": ISIS},
        {"id": "s7", "type": "text", "title": "Done", "text": "That is the report for **{{device}}**. Open any step above for the full reply."},
    ],
}

COMPARE_GUIDE = {
    "name": "Compare two switches (demo)",
    "description": "Read-only: one RPC step sent to two switches gives one summary per switch, so differences in software or "
                   "IS-IS adjacencies are easy to spot side by side.",
    "steps": [
        {"id": "s1", "type": "input", "title": "First switch", "var": "first", "label": "First switch", "kind": "device", "default": "demo-eos-1", "choices": []},
        {"id": "s2", "type": "input", "title": "Second switch", "var": "second", "label": "Second switch", "kind": "device", "default": "demo-eos-2", "choices": []},
        {"id": "s3", "type": "rpc", "title": "Identity of both",
         "body": {"get-system-state": {}}, "target": {"devices": ["{{first}}", "{{second}}"], "groups": []}, "show": SYSTEM},
        {"id": "s4", "type": "rpc", "title": "IS-IS adjacencies of both",
         "body": {"get-isis-state": {}}, "target": {"devices": ["{{first}}", "{{second}}"], "groups": []}, "show": ISIS},
        {"id": "s5", "type": "confirm", "title": "Do they match?",
         "question": "Are **{{first}}** and **{{second}}** on the software version and with the neighbours you expect?"},
    ],
}

VERSIONS_GUIDE = {
    "name": "Software versions, access and aggregation (demo)",
    "description": "Read-only and without questions: the identity of the seven Arista and OpenConfig demo switches in one step, "
                   "one summary per switch.",
    "steps": [
        {"id": "s1", "type": "rpc", "title": "Identity of every switch", "body": {"get-system-state": {}},
         "target": {"devices": [f"demo-eos-{n}" for n in range(1, 5)] + [f"demo-openconfig-{n}" for n in range(1, 4)], "groups": []},
         "show": SYSTEM[:1] + SYSTEM[2:3]},
        {"id": "s2", "type": "confirm", "title": "Reviewed", "question": "Have you looked at the software versions above?"},
    ],
}

def inp(sid: str, title: str, var: str, kind: str = "text", default: str = "", choices: list[str] | None = None) -> dict:
    return {"id": sid, "type": "input", "title": title, "var": var, "label": title, "kind": kind, "default": default, "choices": choices or []}


PORT = fields("interfaces/interface/state/", ("name", "Port"), ("admin-status", "Admin"), ("oper-status", "Oper"),
              ("counters/in-errors", "In errors"), ("counters/out-errors", "Out errors"),
              ("counters/in-octets", "In octets"), ("counters/out-octets", "Out octets"))

AUDIT_GUIDE = {
    "name": "Interface error audit (demo)",
    "description": "Read-only: pick a switch and one of its ports from a list. The step fails if the port is down or has input or "
                   "output errors, so the result needs no reading: it is green or red.",
    "steps": [
        inp("s1", "Which switch?", "device", "device", "demo-eos-1"),
        inp("s2", "Which port?", "port", "choice", "Ethernet1", ["Ethernet1", "Ethernet2", "Ethernet3", "Ethernet4"]),
        {"id": "s3", "type": "rpc", "title": "Port state and counters", **probe({"get-interface-state": {"interface-name": "{{port}}"}}),
         "show": PORT,
         "success": [{"path": "interfaces/interface/state/admin-status", "op": "equals", "value": "UP"},
                     {"path": "interfaces/interface/state/oper-status", "op": "equals", "value": "UP"}],
         "failure": [{"path": "interfaces/interface/state/counters/in-errors", "op": "not-equals", "value": "0"},
                     {"path": "interfaces/interface/state/counters/out-errors", "op": "not-equals", "value": "0"}]},
        {"id": "s4", "type": "confirm", "title": "Recorded", "question": "Have you noted the result for **{{port}}** on **{{device}}** in the ticket?"},
    ],
}

RECONNECT_GUIDE = {
    "name": "Reconnect a switch (demo)",
    "description": "Close the controller's connection to a switch, open it again, wait until the switch answers, and pull its "
                   "configuration. Uses a connection step, a wait that polls until a check passes, and a config pull. Each step "
                   "that changes something asks first.",
    "steps": [
        inp("s1", "Which switch?", "device", "device", "demo-openconfig-3"),
        {"id": "s2", "type": "text", "title": "What happens",
         "text": "The controller drops its connection to **{{device}}**, connects again and reads the configuration. "
                 "The switch itself is not touched."},
        {"id": "s3", "type": "connection", "title": "Close the connection", "operation": "CLOSE", "device": "{{device}}"},
        {"id": "s4", "type": "wait", "title": "Let it settle", "seconds": 3},
        {"id": "s5", "type": "connection", "title": "Open the connection", "operation": "OPEN", "device": "{{device}}"},
        {"id": "s6", "type": "wait", "title": "Wait until it answers",
         "until": {**probe({"get-system-state": {}}), "rules": [{"path": "system/state/hostname", "op": "equals", "value": "{{device}}"}],
                   "interval": 2, "timeout": 60}},
        {"id": "s7", "type": "pull", "title": "Pull the configuration", "device": "{{device}}", "merge": False},
        {"id": "s8", "type": "text", "title": "Done", "text": "**{{device}}** is connected and its configuration is in sync with the controller."},
    ],
}

COUNTERS_GUIDE = {
    "name": "Clear interface counters (demo)",
    "description": "Read the counters of a port, clear them (a step that changes something, so it asks first), and read them "
                   "again. The same fields are shown before and after so the two tables line up.",
    "steps": [
        inp("s1", "Which switch?", "device", "device", "demo-eos-1"),
        inp("s2", "Which port?", "port", "choice", "Ethernet1", ["Ethernet1", "Ethernet2", "Ethernet3", "Ethernet4"]),
        {"id": "s3", "type": "rpc", "title": "Counters before", **probe({"get-interface-state": {"interface-name": "{{port}}"}}), "show": PORT},
        {"id": "s4", "type": "rpc", "title": "Clear the counters", **probe({"request-clear-counters": {"interface-name": "{{port}}"}})},
        {"id": "s5", "type": "rpc", "title": "Counters after", **probe({"get-interface-state": {"interface-name": "{{port}}"}}), "show": PORT},
        {"id": "s6", "type": "confirm", "title": "Look right?", "question": "Do the counters of **{{port}}** on **{{device}}** look as you expect after clearing?"},
    ],
}

CHECKLIST_GUIDE = {
    "name": "Maintenance window checklist (demo)",
    "description": "A guide made of questions and confirmations with one live check: who and why, what was agreed, and a last "
                   "look at the switch before work starts. Shows the input kinds (text, choice, device) and confirmations.",
    "steps": [
        inp("s1", "Ticket", "ticket", "text", "CHG-1234"),
        inp("s2", "Kind of work", "kind", "choice", "planned", ["planned", "emergency"]),
        inp("s3", "Switch", "device", "device", "demo-eos-2"),
        {"id": "s4", "type": "text", "title": "Before you start",
         "text": "Maintenance on **{{device}}** under **{{ticket}}** ({{kind}} work).\n\nWork through the confirmations; "
                 "the last step looks at the switch itself."},
        {"id": "s5", "type": "confirm", "title": "Customers informed", "question": "Are the affected customers informed about work on **{{device}}** ({{ticket}})?"},
        {"id": "s6", "type": "confirm", "title": "Rollback plan", "question": "Is there a rollback plan and is the previous configuration saved?"},
        {"id": "s7", "type": "check", "title": "The switch answers", **probe({"get-system-state": {}}),
         "rules": [{"path": "system/state/hostname", "op": "equals", "value": "{{device}}"}]},
        {"id": "s8", "type": "text", "title": "Go ahead", "text": "All set for **{{ticket}}**. Start the work, and note the time in the ticket."},
    ],
}

GROUP_GUIDE = {
    "name": "Check a device group (demo)",
    "description": "Read-only: choose a device group instead of single switches. One step reaches every member and shows one summary "
                   "per switch; a check then makes sure every member reports a software version. Written for the arista-eos and "
                   "access-switches groups (the core routers answer in another shape).",
    "steps": [
        inp("s1", "Which group?", "group", "group", "arista-eos"),
        {"id": "s2", "type": "rpc", "title": "Identity of every member",
         "body": {"get-system-state": {}}, "target": {"devices": [], "groups": ["{{group}}"]}, "show": SYSTEM[:1] + SYSTEM[2:3]},
        {"id": "s3", "type": "check", "title": "Every member reports a software version",
         "body": {"get-system-state": {}}, "target": {"devices": [], "groups": ["{{group}}"]},
         "rules": [{"path": "system/state/software-version", "op": "exists"}]},
        {"id": "s4", "type": "confirm", "title": "Versions as planned",
         "question": "Do all members of **{{group}}** run the software version you planned?"},
    ],
}

SERIAL_GUIDE = {
    "name": "Verify you are at the right box (demo)",
    "description": "The operator reads the serial number off the hardware label and types it in; the guide checks it against what the "
                   "switch itself reports. A typo or the wrong box makes the check fail; abort and start the guide again with the right value.",
    "steps": [
        inp("s1", "Which switch?", "device", "device", "demo-eos-1"),
        {"id": "s2", "type": "rpc", "title": "What the switch reports", **probe({"get-system-state": {}}),
         "show": fields("system/state/", ("hostname", "Hostname"), ("hardware", "Hardware"))},
        inp("s3", "Serial number on the label", "serial", "text", ""),
        {"id": "s4", "type": "check", "title": "The serial number matches", **probe({"get-system-state": {}}),
         "rules": [{"path": "system/state/serial-no", "op": "equals", "value": "{{serial}}"}]},
        {"id": "s5", "type": "text", "title": "Right box", "text": "The serial number **{{serial}}** belongs to **{{device}}**. Go ahead."},
    ],
}

GUIDES = [GUIDE, HEALTH_GUIDE, REPORT_GUIDE, COMPARE_GUIDE, VERSIONS_GUIDE, AUDIT_GUIDE, RECONNECT_GUIDE, COUNTERS_GUIDE, CHECKLIST_GUIDE,
          GROUP_GUIDE, SERIAL_GUIDE]


def seed(db: Session) -> None:
    """Add the example guides that are missing, by name. Runs at every demo start, so a database from an older version
    gets new examples; an example the visitor deleted comes back at the next start."""
    have = {g.name for g in guides.list_guides(db)}
    for g in GUIDES:
        if g["name"] not in have:
            guides.save(db, g, "demo")
