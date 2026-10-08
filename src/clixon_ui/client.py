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
        data = await self.get(f"{NS}:devices?content=nonconfig")
        found: dict[str, dict] = {}
        for chunk in _as_list(data.get(f"{NS}:devices", {})):
            for dev in _as_list(chunk.get("device", [])):
                found.setdefault(dev["name"], {}).update(dev)
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

    @staticmethod
    def _service_path(module: str, name: str, key: str) -> str:
        return f"/ds/ietf-datastores:candidate/{NS}:services/{module}:{name}={quote(key, safe='')}"

    async def put_service(self, module: str, name: str, key: str, body: dict) -> None:
        """Create or replace one service instance in the candidate datastore."""
        await self._request("PUT", self._service_path(module, name, key), json=body)

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
