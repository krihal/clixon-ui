"""Async RESTCONF client for the Clixon controller."""

from __future__ import annotations

import asyncio
import json
from typing import Any
from urllib.parse import quote

import httpx

NS = "clixon-controller"
JSON = "application/yang-data+json"


class RestconfError(Exception):
    """RESTCONF error reply (or transport failure)."""


class Unreachable(RestconfError):
    """The controller could not be reached at all (refused, DNS, timeout while connecting)."""


def _error_message(resp: httpx.Response) -> str:
    try:
        err = resp.json()["ietf-restconf:errors"]["error"]
        err = err[0] if isinstance(err, list) else err
        return err.get("error-message") or err.get("error-tag", resp.text)
    except Exception:
        if "<html" in resp.text[:200].lower():
            return f"The controller's web server answered HTTP {resp.status_code} {resp.reason_phrase}. It may be busy or restarting; try again."
        return f"HTTP {resp.status_code}: {resp.text[:200]}"


class ClixonClient:
    def __init__(self, url: str, verify: bool = True, transport: httpx.AsyncBaseTransport | None = None):
        self.url = url.rstrip("/")
        self.on_unreachable = None  # called with the error each time the controller cannot be reached
        self._http = httpx.AsyncClient(
            base_url=f"{self.url}/restconf",
            verify=verify,
            transport=transport,
            timeout=30,
            headers={"Accept": JSON, "Content-Type": JSON},
        )

    async def aclose(self) -> None:
        await self._http.aclose()

    async def _request(self, method: str, path: str, **kw: Any) -> dict:
        for attempt in range(3 if method == "GET" else 1):
            try:
                resp = await self._http.request(method, path, **kw)
            except (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout) as e:
                err = Unreachable(f"Cannot connect to {self.url}: {e or type(e).__name__}")
                if self.on_unreachable:
                    self.on_unreachable(err)
                raise err from e
            except httpx.HTTPError as e:
                raise RestconfError(f"{type(e).__name__}: {e}") from e
            if resp.status_code not in (502, 503) or attempt == 2 or method != "GET":
                break
            await asyncio.sleep(0.4 * (attempt + 1))  # the controller's nginx sheds load with 502 when busy
        if resp.status_code >= 400:
            raise RestconfError(_error_message(resp))
        return resp.json() if resp.content.strip() else {}

    async def get(self, path: str) -> dict:
        return await self._request("GET", f"/data/{path}")

    async def rpc(self, name: str, **params: Any) -> dict:
        body = {f"{NS}:input": {k: v for k, v in params.items() if v is not None}}
        out = await self._request("POST", f"/operations/{NS}:{name}", json=body)
        return out.get(f"{NS}:output", out)

    # -- devices -----------------------------------------------------------
    async def devices(self) -> list[dict]:
        # State alone omits devices that have none (DISABLED/never connected), so merge the shallow config too.
        state = await self.get(f"{NS}:devices?content=nonconfig")
        try:
            config = await self.get(f"{NS}:devices?content=config&depth=3")
        except RestconfError as e:
            if "Mountpoint operation" not in str(e):
                raise
            config = {}  # a closed device cannot be read through its mountpoint: state alone has to do
        found: dict[str, dict] = {}
        for data in (config, state):
            for chunk in _as_list(data.get(f"{NS}:devices", {})):
                for dev in _as_list(chunk.get("device", [])):
                    found.setdefault(dev["name"], {}).update(dev)
        for dev in found.values():  # no state entry: the controller shows these as DISABLED or CLOSED
            dev.setdefault("conn-state", "DISABLED" if dev.get("enabled") == "false" else "CLOSED")
        return sorted(found.values(), key=lambda d: d["name"])

    async def device_outline(self, name: str) -> dict:
        """Depth-limited device config ({root-module:root: {...}}): section and entry names only."""
        data = await self._request("GET", f"/data/{NS}:devices/device={quote(name, safe='')}?depth=7&content=config")
        return next(iter(_as_list(data.get(f"{NS}:device", [{}]))), {}).get("config") or {}

    async def device_node(self, name: str, path: str, cap: int = 4_000_000) -> dict | None:
        """One subtree of a device's config. Returns None when it is larger than `cap` bytes
        (a whole Junos config is ~30 MB, so large nodes must be browsed per entry)."""
        url = f"/data/{NS}:devices/device={quote(name, safe='')}/config/{path}"
        try:
            async with self._http.stream("GET", url) as r:
                buf = bytearray()
                async for chunk in r.aiter_bytes():
                    buf += chunk
                    if len(buf) > cap:
                        return None
                if r.status_code >= 400:
                    raise RestconfError(_error_message(httpx.Response(r.status_code, content=bytes(buf))))
        except httpx.HTTPError as e:
            raise RestconfError(f"{type(e).__name__}: {e}") from e
        return json.loads(buf)

    async def device_schemas(self, device: str, module: str | None = None, revision: str | None = None,
                             detail: bool = False) -> list[dict]:
        """YANG modules the controller holds for a device (optionally one module, with its YANG text)."""
        inp = {"device": device, "name": module, "revision": revision, "detail": detail or None}
        body = {f"{NS}:input": {k: v for k, v in inp.items() if v is not None}}
        out = (await self._request("POST", f"/operations/{NS}:get-device-schema", json=body)).get(f"{NS}:output", {})
        return _as_list(out.get("schema", []))

    async def device_groups(self) -> list[str]:
        data = await self._request("GET", f"/data/{NS}:devices?content=config&depth=3")
        d = data.get(f"{NS}:devices", {})
        return sorted(g["name"] for g in _as_list(d.get("device-group", [])))

    async def rpc_templates(self) -> list[dict]:
        """All RPC templates with their RPC body and declared variables."""
        data = await self._request("GET", f"/data/{NS}:devices?content=config&depth=3")
        names = sorted(t["name"] for t in _as_list(data.get(f"{NS}:devices", {}).get("rpc-template", [])))

        gate = asyncio.Semaphore(4)  # more than ~15 parallel requests make the controller answer 502

        async def one(n: str) -> dict:
            async with gate:
                d = await self._request("GET", f"/data/{NS}:devices/rpc-template={quote(n, safe='')}")
            return next(iter(_as_list(d.get(f"{NS}:rpc-template", [{"name": n}]))))

        return list(await asyncio.gather(*(one(n) for n in names)))

    async def run_rpc(self, *, device: str | None = None, group: str | None = None, template: str | None = None,
                      inline: dict | None = None, variables: dict[str, str] | None = None) -> int | None:
        """Start an RPC (template or inline body) on a device or device group; returns the transaction id."""
        params: dict = {"type": "RPC", "device": device, "device-group": group, "template": template}
        if inline is not None:
            params["inline"] = {"config": inline}
        if variables:
            params["variables"] = {"variable": [{"name": k, "value": v} for k, v in variables.items()]}
        out = await self.rpc("device-template-apply", **params)
        return int(out["tid"]) if out.get("tid") is not None else None

    async def rpc_result(self, tid: int) -> dict[str, Any]:
        """Replies per device for a finished RPC transaction: {device: data}."""
        out = await self.rpc("device-rpc-result", tid=tid)
        devs = out.get("devices") or {}
        return {d["name"]: d.get("data") for d in _as_list(devs.get("devdata", []))}

    async def connection_change(self, device: str, operation: str) -> int | None:
        """operation: OPEN | CLOSE | RECONNECT"""
        return (await self.rpc("connection-change", device=device, operation=operation)).get("tid")

    async def config_pull(self, device: str, merge: bool = False) -> int | None:
        return (await self.rpc("config-pull", device=device, merge=merge)).get("tid")

    async def device_config(self, device: str, config_type: str) -> Any:
        return (await self.rpc("get-device-config", device=device, config_type=config_type)).get("config")

    # -- diff / commit -----------------------------------------------------
    async def diff_datastores(self, ds1: str = "ietf-datastores:running", ds2: str = "ietf-datastores:candidate") -> str:
        out = await self.rpc("datastore-diff", dsref1=ds1, dsref2=ds2)
        return "\n".join(_as_list(out.get("diff", [])))

    async def diff_device(self, device: str, type1: str = "SYNCED", type2: str = "RUNNING") -> str:
        out = await self.rpc("datastore-diff", device=device, **{"config-type1": type1, "config-type2": type2})
        return "\n".join(_as_list(out.get("diff", [])))

    async def commit(self, device: str | None = None, source: str = "ietf-datastores:candidate",
                     actions: str = "CHANGE", push: str = "VALIDATE") -> int | None:
        """push: NONE | VALIDATE | COMMIT; actions: NONE | CHANGE | FORCE"""
        return (await self.rpc("controller-commit", device=device, source=source,
                               actions=actions, push=push)).get("tid")

    @staticmethod
    def service_instance(name: str, key_leaf: str, key: str) -> str:
        """Syntax accepted by controller-commit service-instance, e.g. l2c[service-name='X']."""
        return f"{name}[{key_leaf}='{key}']"

    async def commit_diff(self, instance: str | None = None, device: str = "*", on_update=None) -> tuple[dict | None, str]:
        """Like the CLI's `commit diff`: run service actions from candidate, push nothing to devices,
        return (transaction, per-device diff of RUNNING vs the computed ACTIONS datastore).
        instance=None runs services whose config changed (CHANGE); otherwise force-reapplies that one."""
        if instance is None:
            tid = await self.commit(device=None, actions="CHANGE", push="NONE")
        else:
            tid = (await self.rpc("controller-commit", source="ietf-datastores:candidate", actions="FORCE",
                                  push="NONE", **{"service-instance": instance})).get("tid")
        tr = await self.wait_transaction(int(tid), on_update=on_update, interval=0.5) if tid is not None else None
        if tr is None or tr.get("result") != "SUCCESS":
            return tr, ""
        return tr, await self.diff_device(device, "RUNNING", "ACTIONS")

    # -- services / transactions ------------------------------------------
    async def services(self) -> dict:
        data = await self.get(f"{NS}:services?content=config")
        return data.get(f"{NS}:services", {})

    async def candidate_services(self) -> dict:
        """Services subtree of the candidate datastore (includes pending, uncommitted edits)."""
        data = await self._request("GET", f"/ds/ietf-datastores:candidate/{NS}:services")
        return data.get(f"{NS}:services", {})

    async def running_services(self) -> dict:
        data = await self._request("GET", f"/ds/ietf-datastores:running/{NS}:services")
        return data.get(f"{NS}:services", {})

    async def commit_service(self, instance: str | None, on_update=None) -> dict | None:
        """Push to the devices and commit. One instance is force-applied; None runs every service whose config changed.

        NB: the controller commits the whole candidate afterwards, not only this instance."""
        tid = (await self.rpc("controller-commit", source="ietf-datastores:candidate",
                              actions="CHANGE" if instance is None else "FORCE", push="COMMIT",
                              **{"service-instance": instance})).get("tid")
        return await self.wait_transaction(int(tid), timeout=600, interval=0.5, on_update=on_update) if tid is not None else None

    # -- inventory: devices, device groups, profiles, templates ---------------------------------------
    INV = "/ds/ietf-datastores:candidate/clixon-controller:devices"

    async def inventory(self) -> dict:
        """All inventory lists of the candidate, shallow (`depth=3`): names and settings, but no device config."""
        data = await self._request("GET", f"{self.INV}?content=config&depth=3")
        return data.get(f"{NS}:devices", {})

    async def inventory_entry(self, kind: str, key: str) -> dict | None:
        """One entry. A device is read with depth=3 so its (huge) mounted config is left out."""
        suffix = "?content=config&depth=3" if kind == "device" else ""
        try:
            data = await self._request("GET", f"{self.INV}/{kind}={quote(key, safe='')}{suffix}")
        except RestconfError:
            return None
        return next(iter(_as_list(data.get(f"{NS}:{kind}", []))), None)

    async def inventory_names(self) -> dict[str, list[str]]:
        inv = await self.inventory()
        return {k: sorted(str(e["name"]) for e in _as_list(inv.get(k, []))) for k in
                ("device", "device-group", "device-profile", "template", "rpc-template")}

    async def inventory_delete(self, kind: str, key: str) -> None:
        await self._request("DELETE", f"{self.INV}/{kind}={quote(key, safe='')}")

    async def inventory_put(self, kind: str, key: str, entry: dict) -> None:
        """Create or replace one entry. NOT for an existing device: replacing it would wipe its mounted config."""
        await self._request("PUT", f"{self.INV}/{kind}={quote(key, safe='')}", json={f"{NS}:{kind}": [entry]})

    async def device_update(self, key: str, old: dict, new: dict) -> None:
        """Change an existing device one top-level setting at a time, leaving the mounted `config` alone.
        `old`/`new` are the entries as RESTCONF objects (without `config`); a setting missing in `new` is deleted."""
        base = f"{self.INV}/device={quote(key, safe='')}"
        for name in sorted((set(old) | set(new)) - {"name", "config"}):
            if old.get(name) == new.get(name):
                continue
            if name not in new:
                await self._request("DELETE", f"{base}/{name}")
            else:
                await self._request("PUT", f"{base}/{name}", json={f"{NS}:{name}": new[name]})

    NACM = "/ds/ietf-datastores:candidate/ietf-netconf-acm:nacm"

    async def nacm(self) -> dict | None:
        """The candidate's NACM configuration, or None when none is configured."""
        try:
            data = await self._request("GET", self.NACM)
        except Unreachable:
            raise
        except RestconfError:
            return None  # "Instance does not exist"
        return data.get("ietf-netconf-acm:nacm")

    async def put_nacm(self, body: dict) -> None:
        """Replace the whole NACM configuration in the candidate."""
        await self._request("PUT", self.NACM, json={"ietf-netconf-acm:nacm": body})

    async def delete_nacm(self) -> None:
        await self._request("DELETE", self.NACM)

    async def local_commit(self) -> None:
        """Plain NETCONF commit of the controller's own candidate into running (no push to devices)."""
        await self._request("POST", "/operations/ietf-netconf:commit", json={"ietf-netconf:input": {}})

    async def delete_service_commit(self, instance: str, on_update=None) -> dict | None:
        """The controller's own delete: removes the service instance and its device configuration, then commits.

        NB: like any commit this applies the whole candidate."""
        tid = (await self.rpc("controller-commit", source="ietf-datastores:candidate", actions="DELETE", push="COMMIT",
                              **{"service-instance": instance})).get("tid")
        return await self.wait_transaction(int(tid), timeout=600, interval=0.5, on_update=on_update) if tid is not None else None

    @staticmethod
    def _service_path(module: str, name: str, key: str) -> str:
        return f"/ds/ietf-datastores:candidate/{NS}:services/{module}:{name}={quote(key, safe='')}"

    async def put_service(self, module: str, name: str, key: str, body: dict) -> None:
        """Create or replace one service instance in the candidate datastore."""
        await self._request("PUT", self._service_path(module, name, key), json=body)

    @staticmethod
    def _property_path(module: str, name: str, key: str | None = None) -> str:
        path = f"/ds/ietf-datastores:candidate/{NS}:services/properties/{module}:{name}"
        # composite keys are comma separated; each part is escaped on its own
        return path + (f"={','.join(quote(k, safe='') for k in key.split(','))}" if key is not None else "")

    async def put_property(self, module: str, name: str, body: dict, key: str | None = None) -> None:
        """Create or replace one `services/properties` container, or (with `key`) one entry of a property list."""
        await self._request("PUT", self._property_path(module, name, key), json=body)

    async def delete_property(self, module: str, name: str, key: str | None = None) -> None:
        await self._request("DELETE", self._property_path(module, name, key))

    async def delete_service(self, module: str, name: str, key: str) -> None:
        await self._request("DELETE", self._service_path(module, name, key))

    async def transactions(self) -> list[dict]:
        try:
            data = await self.get(f"{NS}:transactions")
        except RestconfError:
            return []  # "Instance does not exist" when empty
        t = data.get(f"{NS}:transactions", {})
        return _as_list(t.get("transaction", [])) if isinstance(t, dict) else []

    async def transaction(self, tid: int | str) -> dict | None:
        try:
            data = await self.get(f"{NS}:transactions/transaction={tid}")
        except RestconfError:
            return None
        return next(iter(_as_list(data.get(f"{NS}:transaction", []))), None)

    async def wait_transaction(self, tid: int, timeout: float = 120, interval: float = 1.0,
                               on_update=None) -> dict | None:
        """Poll until the transaction reaches DONE (or vanishes after being seen).
        `on_update(tr)` is called on every poll in which the transaction is visible."""
        seen: dict | None = None
        async with asyncio.timeout(timeout):
            while True:
                tr = next((t for t in await self.transactions() if str(t.get("tid")) == str(tid)), None)
                if tr:
                    seen = tr
                    if on_update:
                        on_update(tr)
                    if tr.get("state") == "DONE":
                        return tr
                elif seen:
                    return seen
                await asyncio.sleep(interval)


def _as_list(x: Any) -> list:
    return x if isinstance(x, list) else [x]
