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


def seed(db: Session) -> None:
    """Add the example guide while the demo has no guide at all."""
    if not guides.list_guides(db):
        guides.save(db, GUIDE, "demo")
