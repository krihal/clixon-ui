"""Which RPCs does a device offer? Parsed from the device's YANG as stored on the controller."""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .client import ClixonClient

_RPC = re.compile(r"^[ \t]*rpc[ \t]+([\w.-]+)[ \t]*\{", re.M)
_STR = re.compile(r'"(?:[^"\\]|\\.)*"|\'[^\']*\'')
_BRACE = re.compile(r"[{}]")
_DESC = re.compile(r'description\s+"((?:[^"\\]|\\.)*)"', re.S)
_STMT = re.compile(r"^[ \t]*(leaf-list|leaf|container|list|choice)[ \t]+([\w.-]+)", re.M)


@dataclass
class DeviceRpc:
    name: str
    module: str  # e.g. junos-rpc-chassis
    description: str = ""
    args: list[str] = field(default_factory=list)

    @property
    def area(self) -> str:
        return self.module.removeprefix("junos-rpc-")

    @property
    def read_only(self) -> bool:
        return self.name.startswith("get-")


def _blank_strings(text: str) -> str:
    """Replace string literals by spaces of equal length so braces inside descriptions don't count."""
    return _STR.sub(lambda m: m.group(0)[0] + " " * (len(m.group(0)) - 2) + m.group(0)[-1], text)


def _block_end(stripped: str, open_idx: int) -> int:
    depth = 0
    for m in _BRACE.finditer(stripped, open_idx):
        depth += 1 if m.group(0) == "{" else -1
        if depth == 0:
            return m.end()
    return len(stripped)


def parse_module(text: str, module: str) -> list[DeviceRpc]:
    """Extract rpc statements (name, description, top-level input arguments) from YANG text."""
    stripped = _blank_strings(text)
    out: list[DeviceRpc] = []
    pos = 0
    while (m := _RPC.search(stripped, pos)):
        end = _block_end(stripped, m.end() - 1)
        body, body_orig = stripped[m.end():end], text[m.end():end]
        d = _DESC.search(body_orig[: body.find("input") if "input" in body else len(body_orig)])
        desc = " ".join(d.group(1).split()) if d else ""
        args: list[str] = []
        im = re.search(r"\binput[ \t]*\{", body)
        if im:
            iend = _block_end(body, im.end() - 1)
            block = body[im.end():iend]
            # only top-level statements of the input block (depth 0 inside it)
            depth, start = 0, 0
            for b in _BRACE.finditer(block):
                if depth == 0:
                    args += [s.group(2) for s in _STMT.finditer(block[start:b.start()])]
                depth += 1 if b.group(0) == "{" else -1
                if depth == 0:
                    start = b.end()
            args += [s.group(2) for s in _STMT.finditer(block[start:])] if depth == 0 else []
        out.append(DeviceRpc(m.group(1), module, desc, args))
        pos = end
    return out


REVALIDATE_SECONDS = 600  # how often a device's module list is compared with the cached index


class RpcIndex:
    """Lazily built, cached index of the RPCs each device offers.

    A device's YANG module is identified by (name, revision); the parsed RPCs of a module are kept in memory and on
    disk under that pair, so a module is downloaded and parsed once, ever, for any device that has it. The module list of
    a device (small) is compared every few minutes: a software upgrade brings new revisions and the index is rebuilt,
    fetching only the modules that are new. `refresh=True` ignores everything cached for that device."""

    def __init__(self, client: ClixonClient, cache: Path | None = None):
        self.client = client
        key = hashlib.sha1(client.url.encode()).hexdigest()[:10]
        self.dir = (cache or Path.home() / ".cache" / "clixon-ui") / key / "rpc"
        self._modules: dict[tuple[str, str], list[DeviceRpc]] = {}
        self._devices: dict[str, tuple[list[DeviceRpc], list[tuple[str, str]], float]] = {}  # rpcs, module list, checked at
        self._locks: dict[str, asyncio.Lock] = {}

    def _file(self, key: tuple[str, str]) -> Path:
        return self.dir / f"{key[0]}@{key[1]}.json"

    def _from_disk(self, key: tuple[str, str]) -> list[DeviceRpc] | None:
        try:
            return [DeviceRpc(**r) for r in json.loads(self._file(key).read_text())]
        except (OSError, ValueError, TypeError):
            return None  # missing or damaged: fetch again

    def _to_disk(self, key: tuple[str, str], rpcs: list[DeviceRpc]) -> None:
        try:
            self.dir.mkdir(parents=True, exist_ok=True)
            self._file(key).write_text(json.dumps([asdict(r) for r in rpcs]))
        except OSError:
            pass  # a read-only cache directory only costs speed

    async def for_device(self, device: str, refresh: bool = False) -> list[DeviceRpc]:
        lock = self._locks.setdefault(device, asyncio.Lock())
        async with lock:
            schemas = None
            if device in self._devices and not refresh:
                rpcs, known, checked = self._devices[device]
                if time.monotonic() - checked < REVALIDATE_SECONDS:
                    return rpcs
                schemas = await self.client.device_schemas(device)
                if _module_list(schemas) == known:
                    self._devices[device] = (rpcs, known, time.monotonic())
                    return rpcs
            if schemas is None:
                schemas = await self.client.device_schemas(device)
            wanted = [s for s in schemas if "rpc" in s["name"].lower()] or [
                s for s in schemas if not s["name"].startswith(("ietf-", "iana-", "junos-conf-", "junos-common"))]
            gate = asyncio.Semaphore(4)  # the controller's web server answers 502 under heavy parallelism

            async def one(s: dict) -> list[DeviceRpc]:
                key = (s["name"], s.get("revision", ""))
                if not refresh:
                    if key in self._modules:
                        return self._modules[key]
                    if (cached := self._from_disk(key)) is not None:
                        self._modules[key] = cached
                        return cached
                async with gate:
                    got = await self.client.device_schemas(device, s["name"], s.get("revision"), detail=True)
                text = got[0].get("data", "") if got else ""
                if not text:
                    return []  # not cached: the YANG may simply not be there yet
                self._modules[key] = await asyncio.to_thread(parse_module, text, s["name"])
                self._to_disk(key, self._modules[key])
                return self._modules[key]

            rpcs = [r for part in await asyncio.gather(*(one(s) for s in wanted)) for r in part]
            rpcs.sort(key=lambda r: r.name)
            if schemas:  # a device that was never connected has no YANG yet: do not cache the empty answer
                self._devices[device] = (rpcs, _module_list(schemas), time.monotonic())
            return rpcs


def _module_list(schemas: list[dict]) -> list[tuple[str, str]]:
    return sorted((s["name"], s.get("revision", "")) for s in schemas)
