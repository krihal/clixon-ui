"""Which service instances differ between the candidate and the running datastore (pure)."""

from __future__ import annotations

from typing import Any

SKIP = ("service-timeout",)  # settings that are not service instances


def _as_list(x: Any) -> list:
    return x if isinstance(x, list) else ([] if x is None else [x])


def _strip(e: Any) -> Any:
    """Drop the controller-managed `created` bookkeeping: it changes by itself and is not a user edit."""
    if isinstance(e, dict):
        return {k: _strip(v) for k, v in e.items() if k != "created"}
    if isinstance(e, list):
        return [_strip(v) for v in e]
    return e


def _index(services: dict) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in (services or {}).items():
        if key in SKIP:
            continue
        local = key.rpartition(":")[2]
        if key == "properties":
            out["properties"] = _strip(value)
            continue
        for e in _as_list(value):
            out[f"{local} '{e.get('service-name', '?')}'"] = _strip(e)
    return out


def changed_instances(candidate: dict, running: dict) -> list[str]:
    """Names like "l2c 'MONSTER-U'" that were added, removed or edited in candidate compared to running."""
    c, r = _index(candidate), _index(running)
    return sorted(k for k in c.keys() | r.keys() if c.get(k) != r.get(k))
