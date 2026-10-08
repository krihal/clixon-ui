"""Helpers for device RPC templates."""

from __future__ import annotations

import json
import re

_VAR = re.compile(r"\$\{(\w+)\}")


def template_vars(t: dict) -> list[tuple[str, str]]:
    """(name, description) for every variable of an RPC template: declared ones first,
    then any ${VAR} used in the body but not declared."""
    declared = (t.get("variables") or {}).get("variable", [])
    declared = declared if isinstance(declared, list) else [declared]
    out = [(v["name"], v.get("description", "")) for v in declared]
    seen = {n for n, _ in out}
    for n in _VAR.findall(json.dumps(t.get("config", {}))):
        if n not in seen:
            seen.add(n)
            out.append((n, ""))
    return out


def rpc_name(config: dict | None) -> str:
    """Name of the RPC (first element of the body)."""
    return next(iter(config or {}), "")


def is_read_only(config: dict | None) -> bool:
    """get-* RPCs only read operational data; everything else may change the device."""
    return rpc_name(config).startswith("get-")


def substitute(config: dict, values: dict[str, str]) -> dict:
    """Preview of the RPC with variables filled in (the controller does the real substitution)."""
    text = json.dumps(config)
    return json.loads(_VAR.sub(lambda m: json.dumps(values.get(m.group(1)) or m.group(0))[1:-1], text))
