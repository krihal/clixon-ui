"""What differs between the candidate and the running datastore, as a list of items (pure: no NiceGUI, no client).

Three areas are compared: service instances, inventory lists (devices, groups, profiles, templates) and NACM.
Every `Change` carries the running and the candidate version of one entry, and whether it can be reverted on its own
(an edit that puts the running entry back into the candidate). Anything else is only undone by "Discard all"."""

from __future__ import annotations

import difflib
import json
from dataclasses import dataclass
from typing import Any

from .servicechanges import SKIP, _as_list, _strip

SERVICES, INVENTORY, ACCESS = "Services", "Inventory", "Access control"
AREAS = (SERVICES, INVENTORY, ACCESS)
INVENTORY_KINDS = ("device", "device-group", "device-profile", "template", "rpc-template")
SERVICE_KEY = "service-name"  # key leaf of every service instance (see servicechanges)


@dataclass
class Change:
    area: str
    kind: str  # service type (l2c), inventory list (device) or "nacm"
    key: str
    status: str  # added | modified | deleted
    old: Any  # running version, None when added
    new: Any  # candidate version, None when deleted
    revertible: bool = True
    why_not: str = ""  # shown when `revertible` is False

    @property
    def label(self) -> str:
        return f"{self.kind.rpartition(':')[2]} {self.key}" if self.key else self.kind.rpartition(':')[2]


def _status(old: Any, new: Any) -> str:
    return "added" if old is None else "deleted" if new is None else "modified"


def _compare(area: str, kind: str, cand: dict[str, Any], run: dict[str, Any], same=lambda a, b: a == b) -> list[Change]:
    """One Change per key present on one side only or different on both."""
    return [Change(area, kind, key, _status(run.get(key), cand.get(key)), run.get(key), cand.get(key))
            for key in sorted(cand.keys() | run.keys())
            if run.get(key) is None or cand.get(key) is None or not same(run[key], cand[key])]


def service_changes(candidate: dict, running: dict) -> list[Change]:
    out: list[Change] = []
    for top in sorted(set(candidate or {}) | set(running or {})):
        if top in SKIP:
            continue
        if top == "properties":  # one entry per property container/list; their keys are not known here
            c, r = (candidate or {}).get(top) or {}, (running or {}).get(top) or {}
            for member in sorted(set(c) | set(r)):
                if _strip(c.get(member)) != _strip(r.get(member)):
                    out.append(Change(SERVICES, "properties", member, _status(r.get(member), c.get(member)),
                                      r.get(member), c.get(member), False, "Property entries are reverted with Discard all."))
            continue
        cand = {str(e.get(SERVICE_KEY, "?")): e for e in _as_list((candidate or {}).get(top))}
        run = {str(e.get(SERVICE_KEY, "?")): e for e in _as_list((running or {}).get(top))}
        # `created` is controller bookkeeping that changes by itself: not an edit, but kept when putting the entry back
        out += _compare(SERVICES, top, cand, run, lambda a, b: _strip(a) == _strip(b))
    return out


def inventory_changes(candidate: dict, running: dict) -> list[Change]:
    out: list[Change] = []
    for kind in INVENTORY_KINDS:
        cand = {str(e["name"]): e for e in _as_list((candidate or {}).get(kind)) if isinstance(e, dict)}
        run = {str(e["name"]): e for e in _as_list((running or {}).get(kind)) if isinstance(e, dict)}
        found = _compare(INVENTORY, kind, cand, run)
        for ch in found:
            if kind == "device" and ch.status == "deleted":  # the device's mounted config is not part of what we read
                ch.revertible, ch.why_not = False, "A deleted device's configuration cannot be restored here. Use Discard all."
        out += found
    return out


def nacm_changes(candidate: dict | None, running: dict | None) -> list[Change]:
    if (candidate or None) == (running or None):
        return []
    return [Change(ACCESS, "nacm", "", _status(running, candidate), running, candidate)]


def all_changes(candidate_snapshot: dict, running_snapshot: dict) -> list[Change]:
    """Both arguments are `ClixonClient.config_snapshot` results: {"services": .., "devices": .., "nacm": ..}."""
    c, r = candidate_snapshot, running_snapshot
    return service_changes(c.get("services") or {}, r.get("services") or {}) \
        + inventory_changes(c.get("devices") or {}, r.get("devices") or {}) + nacm_changes(c.get("nacm"), r.get("nacm"))


def grouped(changes: list[Change]) -> list[tuple[str, list[Change]]]:
    """Changes per area, in a fixed order, without empty areas."""
    return [(a, [c for c in changes if c.area == a]) for a in AREAS if any(c.area == a for c in changes)]


def text_diff(old: Any, new: Any) -> str:
    """Unified diff of two entries rendered as indented JSON (running -> candidate), without the file header lines."""
    def lines(v: Any) -> list[str]:
        return [] if v is None else json.dumps(_strip(v), indent=2, sort_keys=True).splitlines()

    return "\n".join(ln for ln in difflib.unified_diff(lines(old), lines(new), lineterm="", n=2) if not ln.startswith(("---", "+++", "@@")))
