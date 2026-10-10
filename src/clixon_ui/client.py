"""Async RESTCONF client for the Clixon controller."""

from __future__ import annotations

import asyncio
import contextvars
import json
from typing import Any
from urllib.parse import quote

import httpx

from .access import is_write
from .rpcutil import inline_is_read_only, is_read_only
from .netconfxml import NC, Op, edit_config_body

NS = "clixon-controller"
JSON = "application/yang-data+json"
XML = "application/yang-data+xml"
NS_URI = "http://clicon.org/controller"
USER_HEADER = "X-Forwarded-User"  # nginx: fastcgi_param REMOTE_USER $http_x_forwarded_user;


_read_rpc: contextvars.ContextVar[bool] = contextvars.ContextVar("read_rpc", default=False)


def user_headers(user: str) -> dict[str, str]:
    """Identify the acting user: X-Forwarded-User (nginx -> REMOTE_USER) and a literal HTTP_AUTHORIZATION header.
    nginx drops headers with underscores unless it has `underscores_in_headers on;` (it then passes HTTP_HTTP_AUTHORIZATION)."""
    return {USER_HEADER: user, "HTTP_AUTHORIZATION": user}


class RestconfError(Exception):
    """RESTCONF error reply (or transport failure)."""


class PermissionDenied(RestconfError):
    """The signed-in user may not change anything (view-only account)."""


class Unreachable(RestconfError):
    """The controller could not be reached at all (refused, DNS, timeout while connecting)."""


def _error_message(resp: httpx.Response) -> str:
    try:
        err = resp.json()["ietf-restconf:errors"]["error"]
        err = err[0] if isinstance(err, list) else err
        msg = err.get("error-message") or err.get("error-tag", resp.text)
    except Exception:
        if "<html" in resp.text[:200].lower():
            return f"The controller's web server answered HTTP {resp.status_code} {resp.reason_phrase}. It may be busy or restarting; try again."
        return f"HTTP {resp.status_code}: {resp.text[:200]}"
    if "Mountpoint operation on closed device" in msg:
        msg += ". All devices must be connected (open) to read this; open or disable the closed device and try again."
    return msg


class ClixonClient:
    def __init__(self, url: str, verify: bool = True, transport: httpx.AsyncBaseTransport | None = None):
        self.url = url.rstrip("/")
        self._namespaces: dict[str, str] | None = None
        self.user_provider = None  # returns the signed-in user name; sent as X-Forwarded-User so the controller records who acted
        self.write_guard = None  # called with the error text of a write; raises PermissionDenied when the session may not write
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
        if self.write_guard and is_write(method, path, kw.get("json"), _read_rpc.get()):
            self.write_guard()
        if self.user_provider and (user := self.user_provider()):
            kw["headers"] = {**kw.get("headers", {}), **user_headers(user)}
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

    async def namespaces(self) -> dict[str, str]:
        """module name -> XML namespace, from the controller's YANG library (cached)."""
        if self._namespaces is None:
            lib = await self.get("ietf-yang-library:yang-library/module-set=top")
            mods = lib["ietf-yang-library:module-set"][0]["module"]
            self._namespaces = {m["name"]: m["namespace"] for m in mods if "namespace" in m}
        return self._namespaces

    async def edit_config(self, tree: dict) -> None:
        """NETCONF edit-config of the candidate (see netconfxml). Unlike a write through the RESTCONF datastore path
        this is NOT autocommitted: the change stays in the candidate until `local_commit` / a controller commit."""
        body = edit_config_body(tree, await self.namespaces())
        await self._request("POST", "/operations/ietf-netconf:edit-config", content=body, headers={"Content-Type": XML})

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

    async def _device_list_names(self, lst: str) -> list[str]:
        """Names of a `devices` list (rpc-template, device-group, ...). `devices?depth=3` fails with "Mountpoint
        operation on closed device", so ask NETCONF get-config with an xpath filter that never touches devices."""
        body = (f'<input xmlns="{NC}"><source><running/></source><filter type="xpath" '
                f'select="/c:devices/c:{lst}/c:name" xmlns:c="{NS_URI}"/></input>')
        data = await self._request("POST", "/operations/ietf-netconf:get-config", content=body, headers={"Content-Type": XML})
        devices = data.get("ietf-restconf:output", {}).get("data", {}).get("devices", {})
        return sorted(e["name"] for e in _as_list(devices.get(lst, [])))

    async def device_groups(self) -> list[str]:
        return await self._device_list_names("device-group")

    async def rpc_templates(self) -> list[dict]:
        """All RPC templates with their RPC body and declared variables."""
        names = await self._device_list_names("rpc-template")

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
        if self.write_guard:  # a view-only session may still run RPCs that only read
            if inline is not None:
                read_only = inline_is_read_only(inline)
            else:
                tpl = (await self._request("GET", f"/data/{NS}:devices/rpc-template={quote(template or '', safe='')}")).get(f"{NS}:rpc-template", [{}])
                read_only = is_read_only(next(iter(_as_list(tpl)), {}).get("config"))
            token = _read_rpc.set(read_only)
        else:
            token = None
        if inline is not None:
            params["inline"] = {"config": inline}
        if variables:
            params["variables"] = {"variable": [{"name": k, "value": v} for k, v in variables.items()]}
        try:
            out = await self.rpc("device-template-apply", **params)
        finally:
            if token is not None:
                _read_rpc.reset(token)
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
        await self.edit_config({f"{NS}:devices": {kind: [Op("remove", {"name": key})]}})

    async def inventory_put(self, kind: str, key: str, entry: dict) -> None:
        """Create or replace one entry. NOT for an existing device: replacing it would wipe its mounted config."""
        await self.edit_config({f"{NS}:devices": {kind: [Op("replace", entry)]}})

    async def device_update(self, key: str, old: dict, new: dict) -> None:
        """Change an existing device one top-level setting at a time, leaving the mounted `config` alone.
        `old`/`new` are the entries as RESTCONF objects (without `config`); a setting missing in `new` is deleted."""
        changes: dict = {}
        for name in sorted((set(old) | set(new)) - {"name", "config"}):
            if old.get(name) == new.get(name):
                continue
            if name not in new:
                changes[name] = Op("remove", old[name] if isinstance(old[name], list) else None)
            elif isinstance(new[name], list):  # (leaf-)list: replace entries, drop the ones that went away
                gone = [i for i in _as_list(old.get(name, [])) if i not in new[name] and not isinstance(i, dict)]
                changes[name] = [Op("replace", i) for i in new[name]] + [Op("remove", i) for i in gone]
            else:
                changes[name] = Op("replace", new[name])
        if changes:
            await self.edit_config({f"{NS}:devices": {"device": [{"name": key, **changes}]}})

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
        await self.edit_config({"ietf-netconf-acm:nacm": Op("replace", body)})

    async def delete_nacm(self) -> None:
        await self.edit_config({"ietf-netconf-acm:nacm": Op("remove", {})})

    async def local_commit(self) -> None:
        """Plain NETCONF commit of the controller's own candidate into running (no push to devices)."""
        await self._request("POST", "/operations/ietf-netconf:commit")  # no body: the CLI sends a bare <commit/>

    async def delete_service_commit(self, instance: str, on_update=None) -> dict | None:
        """The controller's own delete: removes the service instance and its device configuration, then commits.

        NB: like any commit this applies the whole candidate."""
        tid = (await self.rpc("controller-commit", source="ietf-datastores:candidate", actions="DELETE", push="COMMIT",
                              **{"service-instance": instance})).get("tid")
        return await self.wait_transaction(int(tid), timeout=600, interval=0.5, on_update=on_update) if tid is not None else None

    @staticmethod
    def _key_entry(keys: list[str], key: str) -> dict:
        """The key leaves of a list entry from its (comma separated) key string."""
        return dict(zip(keys, key.split(",") if len(keys) > 1 else [key]))

    async def put_service(self, module: str, name: str, key: str, body: dict) -> None:
        """Create or replace one service instance in the candidate datastore."""
        member = f"{module}:{name}"
        await self.edit_config({f"{NS}:services": {member: [Op("replace", e) for e in _as_list(body[member])]}})

    async def put_property(self, module: str, name: str, body: dict, key: str | None = None) -> None:
        """Create or replace one `services/properties` container, or (with `key`) one entry of a property list."""
        member = f"{module}:{name}"
        value = body[member]
        await self.edit_config({f"{NS}:services": {"properties": {member: Op("replace", value)}}})

    async def delete_property(self, module: str, name: str, key: str | None = None, keys: list[str] | None = None) -> None:
        """Remove a property container, or (with `key` and the list's key leaf names) one entry of a property list."""
        member = f"{module}:{name}"
        target = [Op("remove", self._key_entry(keys or [], key))] if key is not None else Op("remove", {})
        await self.edit_config({f"{NS}:services": {"properties": {member: target}}})

    async def delete_service(self, module: str, name: str, key: str, keys: list[str]) -> None:
        await self.edit_config({f"{NS}:services": {f"{module}:{name}": [Op("remove", self._key_entry(keys, key))]}})

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
