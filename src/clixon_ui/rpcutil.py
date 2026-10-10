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


def inline_is_read_only(inline: dict | None) -> bool:
    """Read-only check for an inline RPC body: a get-* RPC or a plain CLI `show` command."""
    return is_read_only(inline) or (rpc_name(inline) == "command" and is_read_only_cli(str(inline["command"])))


def substitute(config: dict, values: dict[str, str]) -> dict:
    """Preview of the RPC with variables filled in (the controller does the real substitution)."""
    text = json.dumps(config)
    return json.loads(_VAR.sub(lambda m: json.dumps(values.get(m.group(1)) or m.group(0))[1:-1], text))


def is_read_only_cli(command: str) -> bool:
    """A CLI command is treated as read-only only if it is a plain `show ...` without a pipe that
    writes somewhere (`| save`, `| tee`...). Everything else asks for confirmation."""
    words = command.strip().lower().split()
    if not words or words[0] != "show":
        return False
    pipes = [part.strip().split()[0] for part in command.lower().split("|")[1:] if part.strip()]
    return not any(p in ("save", "tee", "request") for p in pipes)


def cli_request(command: str) -> dict:
    """RPC request for a CLI command, in the shape rpc_views' runner expects (Junos `<command>` element)."""
    command = command.strip()
    return {"inline": {"command": command}, "label": command, "read_only": is_read_only_cli(command),
            "body": {"command": command}}
