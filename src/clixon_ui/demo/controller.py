"""A fake Clixon controller: the part of RESTCONF that clixon-ui uses, backed by in-memory data.

It runs inside the UI process (httpx `ASGITransport`), so the demo is one process and nothing else listens.
Datastores are plain RFC 7951 JSON trees. Writes arrive as NETCONF edit-config XML into the candidate, a commit copies
the candidate to running, just like the real thing. Service scripts are not run: a "commit diff" shows a plausible
interface fragment per device that the service touches."""

from __future__ import annotations

import difflib
import fnmatch
import re
import time
from pathlib import Path
from typing import Any
from urllib.parse import unquote

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response
from pyang import context, repository

from .. import confview
from ..rpcutil import substitute
from ..schema import Schema
from ..servicechanges import changed_instances
from . import netconf, seed

CTRL = "clixon-controller"
DEVICES, SERVICES, NACM = f"{CTRL}:devices", f"{CTRL}:services", "ietf-netconf-acm:nacm"
YANG = Path(__file__).parent / "yang"
DEVICE_YANG = Path(__file__).parent / "device_yang"
JSON_TYPE = "application/yang-data+json"


def _error(message: str, status: int = 404) -> JSONResponse:
    return JSONResponse({"ietf-restconf:errors": {"error": [{"error-type": "application", "error-tag": "invalid-value", "error-message": message}]}},
                        status_code=status, media_type=JSON_TYPE)


def _module_files(directory: Path) -> dict[str, dict]:
    """name -> {revision, namespace, text, belongs_to} for every YANG file in a directory (modules and submodules)."""
    out: dict[str, dict] = {}
    for f in sorted(directory.glob("*.yang")):
        name, _, rev = f.stem.partition("@")
        text = f.read_text()
        ns = re.search(r'^\s*namespace\s+"([^"]+)"', text, re.M)
        bt = re.search(r"belongs-to\s+([\w.-]+)", text)
        out[name] = {"name": name, "revision": rev, "namespace": ns.group(1) if ns else None, "text": text, "belongs_to": bt.group(1) if bt else None}
    return out


def _load_schema(modules: dict[str, dict]) -> Schema:
    ctx = context.Context(repository.FileRepository(str(YANG), use_env=False))
    for m in modules.values():
        ctx.add_module(f"{m['name']}@{m['revision']}.yang", m["text"], in_format="yang")
    ctx.validate()
    return Schema(ctx)


def trunc(v: Any, depth: int | None) -> Any:
    """RFC 8040 `depth`: the requested node is level 1; containers cut off at the limit become empty, leaves are kept."""
    if depth is None:
        return v
    if isinstance(v, list):
        return [trunc(x, depth) for x in v]
    if isinstance(v, dict):
        return {} if depth <= 1 else {k: trunc(x, depth - 1) for k, x in v.items()}
    return v


def _local(key: str) -> str:
    return key.rpartition(":")[2]


class Controller:
    """State plus the RESTCONF app. `reset()` brings back the initial data."""

    def __init__(self) -> None:
        self.modules = _module_files(YANG)
        self.device_modules = _module_files(DEVICE_YANG)
        self.ns2mod = {m["namespace"]: n for n, m in self.modules.items() if m["namespace"]}
        self.meta = netconf.Meta.from_schema(_load_schema(self.modules))
        self.reset()
        self.app = self._build_app()

    # ------------------------------------------------------------------------------------------ state
    def reset(self) -> None:
        s = seed.fresh()
        self.running: dict[str, Any] = {DEVICES: s["inventory"], SERVICES: s["services"], NACM: s["nacm"]}
        self.candidate = seed.clone(self.running)
        self.configs: dict[str, dict] = s["configs"]
        self.state: dict[str, dict] = s["state"]
        self.transactions: list[dict] = s["transactions"]
        self.rpc_results: dict[str, dict[str, Any]] = {}
        self.actions: dict[str, list[str]] = {}  # device -> diff lines of the last dry run (the ACTIONS datastore)
        self.reset_at = time.time()

    def new_tx(self, description: str, user: str, devices: list[str], result: str = "SUCCESS", reason: str | None = None) -> str:
        tid = str(max((int(t["tid"]) for t in self.transactions), default=0) + 1)
        start = seed.now()
        tx = {"tid": tid, "description": description, "state": "DONE", "result": result, "username": user, "timestamp0": seed.ts(start),
              "timestamp": seed.ts(start), "devices": {"device": [{"name": d, "result": result} for d in devices]}}
        if reason:
            tx["reason"] = reason
        self.transactions.append(tx)
        del self.transactions[:-200]
        return tid

    def device_names(self, ds: dict | None = None) -> list[str]:
        return [d["name"] for d in (ds or self.running)[DEVICES].get("device", [])]

    def targets(self, device: str | None, group: str | None = None) -> list[str]:
        names = self.device_names()
        if group:
            members = next((g.get("device-name", []) for g in self.running[DEVICES].get("device-group", []) if g["name"] == group), [])
            return [n for n in names if n in members]
        return [n for n in names if fnmatch.fnmatch(n, device or "*")]

    def view(self, ds: dict, content: str = "all", running: bool = True) -> dict[str, Any]:
        """The tree a GET sees: the datastore plus the controller's operational data (state, mounted config, transactions)."""
        tree: dict[str, Any] = {}
        for key, val in ds.items():
            if key != DEVICES:
                if content != "nonconfig":
                    tree[key] = seed.clone(val)
                continue
            devices = seed.clone(val)
            if content == "nonconfig":
                devices = {"device": [{"name": d["name"], **self.state.get(d["name"], {})} for d in devices.get("device", [])]}
            else:
                for d in devices.get("device", []):
                    if content == "all":
                        d.update(self.state.get(d["name"], {}))
                    if d["name"] in self.configs and d["name"] in self.state:
                        d["config"] = seed.clone(self.configs[d["name"]])
            tree[key] = devices
        if running and content != "config":
            tree[f"{CTRL}:transactions"] = {"transaction": seed.clone(self.transactions)}
        return tree

    # ------------------------------------------------------------------------------------------ path resolution
    def resolve(self, tree: dict, segments: list[str]) -> tuple[Any, str] | None:
        """(value, member name `module:name`) of a RESTCONF path inside `tree`, or None. List entries come back as [entry]."""
        node: Any = tree
        module, member, parent_local = "", "", ""
        for seg in segments:
            name, _, keyval = seg.partition("=")
            mod, _, local = name.rpartition(":")
            if not isinstance(node, dict):
                return None
            found = next((k for k in node if k == name or (_local(k) == local and (not mod or k.rpartition(":")[0] in ("", mod)))), None)
            if found is None:
                return None
            module = found.rpartition(":")[0] or mod or module
            parent_local = _local(member) if member else ""
            member, node = f"{module}:{local}", node[found]
            if keyval:
                if not isinstance(node, list):
                    return None
                keys = self.meta.keys(parent_local, local, module)
                wanted = [unquote(k) for k in keyval.split(",")]
                node = next((e for e in node if [str(e.get(k)) for k in keys] == wanted), None)
                if node is None:
                    return None
                node = [node] if seg is segments[-1] else node
        return node, member

    # ------------------------------------------------------------------------------------------ service "script"
    @staticmethod
    def _fragment(entry: dict) -> dict[str, list[str]]:
        """Device -> lines the service would configure there. Invented: an interface block per `device` container."""
        out: dict[str, list[str]] = {}
        label = f"{entry.get('service-name', '')}: {entry.get('description', '')}".strip(": ")

        def walk(x: Any) -> None:
            if isinstance(x, dict):
                dev = x.get("device")
                if isinstance(dev, dict) and dev.get("name"):
                    ifaces = dev.get("interface")
                    ifaces = ifaces if isinstance(ifaces, list) else [{"interface-name": ifaces}] if ifaces else []
                    cfg = {"interfaces": {"interface": [{"name": i.get("interface-name", ""), "description": label,
                                                         **{k: v for k, v in i.items() if k in ("description", "mtu", "speed")}} for i in ifaces]}}
                    out.setdefault(dev["name"], []).extend(confview.to_lines(cfg)[0])
                for v in x.values():
                    walk(v)
            elif isinstance(x, list):
                for v in x:
                    walk(v)

        walk(entry)
        return out

    def _instance_index(self, services: dict) -> dict[str, dict]:
        out = {}
        for key, val in services.items():
            if key in ("service-timeout", "properties"):
                continue
            for e in val if isinstance(val, list) else []:
                out[f"{_local(key)} '{e.get('service-name', '?')}'"] = e
        return out

    def dry_run(self, instance: str | None) -> None:
        """Compute what a commit would change on the devices (the ACTIONS datastore), as +/- lines per device."""
        cand, run = self._instance_index(self.candidate[SERVICES]), self._instance_index(self.running[SERVICES])
        if instance:
            m = re.match(r"([\w-]+)\[service-name='(.*)'\]$", instance)
            names = [f"{m.group(1)} '{m.group(2)}'"] if m else []
        else:
            names = changed_instances(self.candidate[SERVICES], self.running[SERVICES])
        self.actions = {}
        for n in names:
            new = self._fragment(cand.get(n, {}))
            old = self._fragment(run.get(n, {}))
            for dev in sorted(new.keys() | old.keys()):
                lines = [ln for ln in difflib.unified_diff(old.get(dev, []), new.get(dev, []), lineterm="", n=2) if not ln.startswith(("---", "+++", "@@"))]
                if lines:
                    self.actions.setdefault(dev, []).extend(lines)

    def datastore_diff(self, left: dict, right: dict) -> str:
        def lines(ds: dict) -> list[str]:
            return confview.to_lines({k: v for k, v in ds.items()})[0]

        return "\n".join(ln for ln in difflib.unified_diff(lines(left), lines(right), lineterm="", n=2) if not ln.startswith(("---", "+++", "@@")))

    def commit_candidate(self) -> None:
        """candidate -> running. A service that was not deployed yet becomes deployed (`created`)."""
        for key, val in self.candidate[SERVICES].items():
            for e in val if isinstance(val, list) else []:
                e.setdefault("created", {"path": [f"/{CTRL}:devices/device={d}" for d in self._fragment(e)]})
        self.running = seed.clone(self.candidate)
        names = self.device_names()
        for n in names:  # a new device exists but is not connected; a removed one takes its state with it
            self.state.setdefault(n, {"conn-state": "CLOSED"})
        for n in list(self.state):
            if n not in names:
                self.state.pop(n)
                self.configs.pop(n, None)

    # ------------------------------------------------------------------------------------------ the app
    def _build_app(self) -> FastAPI:
        app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
        c = self

        def segments(request: Request, prefix: str) -> list[str]:
            raw = request.scope.get("raw_path", request.url.path.encode()).decode().split("?")[0]
            rest = raw.split(prefix, 1)[1].strip("/")
            return [s for s in rest.split("/") if s]

        def respond(tree: dict, segs: list[str], request: Request) -> Response:
            depth = request.query_params.get("depth")
            hit = c.resolve(tree, segs)
            if hit is None:
                return _error("Instance does not exist")
            value, member = hit
            return JSONResponse({member: trunc(value, int(depth) if depth and depth.isdigit() else None)}, media_type=JSON_TYPE)

        @app.get("/restconf/data/ietf-yang-library:yang-library/module-set=top")
        async def yang_library() -> Response:
            subs: dict[str, list] = {}
            for m in c.modules.values():
                if m["belongs_to"]:
                    subs.setdefault(m["belongs_to"], []).append({"name": m["name"], "revision": m["revision"]})
            mods = [{"name": m["name"], "revision": m["revision"], "namespace": m["namespace"], **({"submodule": subs[m["name"]]} if m["name"] in subs else {})}
                    for m in c.modules.values() if not m["belongs_to"]]
            return JSONResponse({"ietf-yang-library:module-set": [{"name": "top", "module": mods}]}, media_type=JSON_TYPE)

        @app.get("/restconf/data/{path:path}")
        async def get_data(request: Request) -> Response:
            content = request.query_params.get("content", "all")
            return respond(c.view(c.running, content), segments(request, "/restconf/data"), request)

        @app.get("/restconf/ds/{ds}/{path:path}")
        async def get_ds(ds: str, request: Request) -> Response:
            store = c.candidate if ds.endswith("candidate") else c.running
            content = request.query_params.get("content", "all")
            return respond(c.view(store, content, running=False), segments(request, f"/restconf/ds/{ds}"), request)

        @app.post("/restconf/operations/{op}")
        async def operation(op: str, request: Request) -> Response:
            user = request.headers.get("x-forwarded-user") or "demo"
            body = await request.body()
            name = op.rpartition(":")[2]
            if op.startswith("ietf-netconf-monitoring:get-schema"):
                return c.get_schema(body)
            if op.startswith("ietf-netconf:"):
                return c.netconf_op(name, body.decode())
            inp = ((await request.json()) if body.strip() else {}).get(f"{CTRL}:input", {})
            try:
                out = c.rpc(name, inp, user)
            except KeyError as e:
                return _error(f"Unknown device or argument: {e}", 400)
            return JSONResponse({f"{CTRL}:output": out}, media_type=JSON_TYPE)

        @app.api_route("/restconf/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
        async def other(path: str) -> Response:
            return _error("The demo controller does not implement this request. Use the pages of the UI.", 400)

        return app

    # ------------------------------------------------------------------------------------------ operations
    def get_schema(self, body: bytes) -> Response:
        import json
        inp = json.loads(body)["ietf-netconf-monitoring:input"]
        m = self.modules.get(inp["identifier"]) or self.device_modules.get(inp["identifier"])
        if m is None:
            return _error("No such schema")
        return JSONResponse({"ietf-netconf-monitoring:output": {"data": m["text"]}}, media_type=JSON_TYPE)

    def netconf_op(self, name: str, body: str) -> Response:
        if name == "commit":
            self.commit_candidate()
            return Response(status_code=204)
        if name == "edit-config":
            netconf.apply(self.candidate, netconf.parse(body, self.ns2mod), self.meta)
            return Response(status_code=204)
        if name == "get-config":
            m = re.search(r"/c:devices/c:([\w-]+)/c:name", body)
            lst = m.group(1) if m else ""
            entries = [{"name": e["name"]} for e in self.running[DEVICES].get(lst, [])]
            return JSONResponse({"ietf-restconf:output": {"data": {"devices": {lst: entries} if entries else {}}}}, media_type=JSON_TYPE)
        return _error(f"netconf operation {name} is not implemented", 400)

    def rpc(self, name: str, inp: dict, user: str) -> dict:
        if name == "controller-commit":
            return self.rpc_commit(inp, user)
        if name == "datastore-diff":
            if "dsref1" in inp:
                pick = {"ietf-datastores:running": self.running, "ietf-datastores:candidate": self.candidate}
                text = self.datastore_diff(pick.get(inp["dsref1"], self.running), pick.get(inp.get("dsref2"), self.candidate))
            else:  # per device: only the dry run of a service commit (RUNNING -> ACTIONS) has anything to show
                shown = self.actions if (inp.get("config-type1"), inp.get("config-type2")) == ("RUNNING", "ACTIONS") else {}
                text = "\n".join(f"{d}:\n" + "\n".join(ls) for d, ls in sorted(shown.items()) if fnmatch.fnmatch(d, inp.get("device") or "*"))
            return {"diff": text} if text else {}
        if name == "device-template-apply":
            return self.rpc_apply(inp, user)
        if name == "device-rpc-result":
            data = self.rpc_results.get(str(inp.get("tid")), {})
            return {"devices": {"devdata": [{"name": d, "data": v} for d, v in data.items()]}}
        if name == "connection-change":
            devs = self.targets(inp.get("device"))
            for d in devs:
                if inp["operation"] == "CLOSE":
                    self.state[d]["conn-state"] = "CLOSED"
                else:
                    self.state.setdefault(d, {"conn-state": "OPEN"})["conn-state"] = "OPEN"
                    self.configs.setdefault(d, {"junos-conf-root:configuration": {"junos-conf-system:system": {"host-name": d}}})
                self.state[d]["conn-state-timestamp"] = seed.ts(seed.now())
            return {"tid": self.new_tx(f"{inp['operation'].title()} {inp.get('device')}", user, devs)}
        if name == "config-pull":
            devs = self.targets(inp.get("device"))
            for d in devs:
                self.state[d]["sync-timestamp"] = seed.ts(seed.now())
            return {"tid": self.new_tx(f"Pull config of {inp.get('device')}", user, devs)}
        if name == "get-device-config":
            return {"config": self.configs[inp["device"]]}
        if name == "get-device-schema":
            mods = [m for m in self.device_modules.values() if not inp.get("name") or m["name"] == inp["name"]]
            return {"schema": [{"name": m["name"], "revision": m["revision"], **({"data": m["text"]} if inp.get("detail") else {})} for m in mods]}
        raise KeyError(f"rpc {name} is not implemented by the demo controller")

    def rpc_commit(self, inp: dict, user: str) -> dict:
        push, actions, instance = inp.get("push", "VALIDATE"), inp.get("actions", "CHANGE"), inp.get("service-instance")
        if push == "NONE":
            self.dry_run(instance if actions == "FORCE" else None)
            return {"tid": self.new_tx("Commit diff (nothing is pushed)", user, list(self.actions))}
        if actions == "DELETE" and instance:
            m = re.match(r"([\w-]+)\[service-name='(.*)'\]$", instance)
            if m:
                for ds in (self.candidate, self.running):
                    for key, val in list(ds[SERVICES].items()):
                        if _local(key) == m.group(1) and isinstance(val, list):
                            val[:] = [e for e in val if e.get("service-name") != m.group(2)]
                            if not val:
                                del ds[SERVICES][key]
            return {"tid": self.new_tx(f"Delete {instance}", user, [])}
        self.dry_run(instance if actions == "FORCE" else None)
        devs = list(self.actions) or self.device_names()
        if push == "COMMIT":
            self.commit_candidate()
        return {"tid": self.new_tx(f"{'Commit' if push == 'COMMIT' else 'Validate'}: {instance or 'changed services'}", user, devs)}

    def rpc_apply(self, inp: dict, user: str) -> dict:
        devs = self.targets(inp.get("device"), inp.get("device-group"))
        if inp.get("template"):
            tpl = next(t for t in self.running[DEVICES].get("rpc-template", []) if t["name"] == inp["template"])
            body = tpl["config"]
        else:
            body = inp["inline"]["config"]
        values = {v["name"]: v["value"] for v in (inp.get("variables") or {}).get("variable", [])}
        body = substitute(body, values)
        results = {}
        for d in devs:
            if self.state.get(d, {}).get("conn-state") != "OPEN":
                continue
            results[d] = seed.reply(d, body)
        tid = self.new_tx(f"RPC {next(iter(body), '')} on {inp.get('device') or inp.get('device-group')}", user, list(results))
        self.rpc_results[tid] = results
        return {"tid": tid}
