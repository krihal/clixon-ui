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
        {"id": "s5", "type": "rpc", "title": "LLDP neighbours", **probe({"get-lldp-neighbors": {}})},
        {"id": "s6", "type": "rpc", "title": "IS-IS state", **probe({"get-isis-state": {}})},
        {"id": "s7", "type": "confirm", "title": "Does it look right?",
         "question": "Do the neighbours and IS-IS adjacencies of **{{device}}** match what you expect before maintenance?"},
    ],
}

GUIDES = [GUIDE, HEALTH_GUIDE]


def seed(db: Session) -> None:
    """Add the example guides that are missing, by name. Runs at every demo start, so a database from an older version
    gets new examples; an example the visitor deleted comes back at the next start."""
    have = {g.name for g in guides.list_guides(db)}
    for g in GUIDES:
        if g["name"] not in have:
            guides.save(db, g, "demo")
