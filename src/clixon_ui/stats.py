"""Numbers for the dashboard (pure: plain controller JSON in, plain dicts out)."""

from __future__ import annotations

from collections import Counter
from typing import Any

FAILED = ("FAILED", "ERROR")


def _as_list(x: Any) -> list:
    return x if isinstance(x, list) else ([] if x is None else [x])


def device_stats(devs: list[dict]) -> dict:
    """Counts by connection state, plus the devices that are not open (the ones that need attention)."""
    states = Counter(str(d.get("conn-state", "UNKNOWN")) for d in devs)
    return {
        "total": len(devs),
        "open": states.get("OPEN", 0),
        "closed": states.get("CLOSED", 0),
        "other": len(devs) - states.get("OPEN", 0) - states.get("CLOSED", 0),
        "states": dict(states),
        "attention": sorted((str(d["name"]), str(d.get("conn-state", "UNKNOWN"))) for d in devs if d.get("conn-state") != "OPEN"),
    }


def service_stats(types: list[tuple[str, str, list[dict]]]) -> dict:
    """`types` = (qualified name, label, instances). An instance with a `created` container has been deployed."""
    rows = []
    for qname, label, instances in types:
        n = len(instances)
        deployed = sum(1 for e in instances if e.get("created"))
        rows.append({"qname": qname, "label": label, "total": n, "deployed": deployed, "pending": n - deployed})
    rows.sort(key=lambda r: (-r["total"], r["label"]))
    total, deployed = sum(r["total"] for r in rows), sum(r["deployed"] for r in rows)
    return {"types": rows, "total": total, "deployed": deployed, "pending": total - deployed, "type_count": len(rows)}


def transaction_stats(trs: list[dict], recent: int = 8) -> dict:
    """Newest first; `failed` counts failures among the transactions given (the controller keeps a bounded history)."""
    ordered = sorted(trs, key=lambda t: int(t.get("tid", 0)), reverse=True)
    results = Counter(str(t.get("result", t.get("state", ""))) for t in ordered)
    return {"total": len(ordered), "failed": sum(results.get(r, 0) for r in FAILED), "results": dict(results),
            "recent": ordered[:recent], "failed_recent": [t for t in ordered if t.get("result") in FAILED][:3]}


def inventory_counts(inv: dict) -> dict[str, int]:
    return {k: len(_as_list(inv.get(k))) for k in ("device-group", "device-profile", "template", "rpc-template")}
