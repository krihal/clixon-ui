"""Services pages: browse instances per service type, create/edit them with a YANG-generated form."""

from __future__ import annotations

import asyncio
from urllib.parse import quote

from nicegui import ui

from . import diffview, views
from .client import RestconfError
from .formdata import Lookup, entry_from_json, service_to_json, validate
from .forms import render_children
from .style import BTN
from .tables import RAIL_SERVICE, data_table, page_column
from .schema import Node, Schema, load_schema

_schema_task: asyncio.Task | None = None


def preload() -> None:
    """Start loading the YANG schema in the background (call from app startup)."""
    global _schema_task
    _schema_task = asyncio.create_task(load_schema(views.client))


async def get_schema() -> Schema:
    global _schema_task
    if _schema_task is None or (_schema_task.done() and _schema_task.exception()):
        preload()  # first use, or retry after a failed load
    return await asyncio.shield(_schema_task)


def _instances(svc: Node, services: dict) -> list[dict]:
    for k, v in services.items():
        if k.rpartition(":")[2] == svc.name and (":" not in k or k.partition(":")[0] == svc.module):
            return v if isinstance(v, list) else [v]
    return []


def _qname(svc: Node) -> str:
    return f"{svc.module}:{svc.name}"


async def _load_lookup(services: dict) -> Lookup:
    try:
        devs = [{"name": d["name"]} for d in await views.client.devices()]
    except RestconfError:
        devs = []
    return Lookup({"devices": {"device": devs}, "services": services})


async def _prologue(active: str = "/services"):
    views.frame(active)
    try:
        return await get_schema()
    except Exception as e:  # noqa: BLE001 - show anything to the user
        ui.label(f"Could not load YANG schema from controller: {e}").classes("text-negative")
        return None


def _secs(tr: dict) -> str:
    from datetime import datetime
    try:
        t0, t1 = (datetime.fromisoformat(tr[k].replace("Z", "+00:00")) for k in ("timestamp0", "timestamp"))
        return f"{(t1 - t0).total_seconds():.1f}s"
    except (KeyError, ValueError):
        return ""


async def commit_diff_dialog(title: str, what: str, instance: str | None = None, note: str = "", after=None) -> None:
    """Run service actions on the candidate (nothing pushed), showing progress, then the per-device diff.

    `after` is an async cleanup run as soon as the services finished (before results are shown).
    `what` describes in words which services run; `instance` is a controller service-instance
    (force re-apply of that one) or None (run all services whose config changed)."""
    steps = ("INIT", "ACTIONS", "RESOLVED", "DONE")
    with ui.dialog().props("persistent") as wait, ui.card().classes("w-[460px]"):
        ui.label("Running services").classes("text-lg")
        ui.label(what).classes("text-sm text-gray-400")
        with ui.row().classes("items-center gap-2"):
            ui.spinner(size="sm")
            stage = ui.label("Starting transaction…")
        bar = ui.linear_progress(value=0.05, show_value=False).props("instant-feedback")
        detail = ui.label().classes("text-xs text-gray-500")
    wait.open()

    def update(tr: dict) -> None:
        st = tr.get("state", "INIT")
        bar.set_value((steps.index(st) + 1) / len(steps) if st in steps else 0.1)
        nice = {"INIT": "Transaction started", "ACTIONS": "Service scripts running",
                "RESOLVED": "Service output received, computing device config", "DONE": "Done"}
        stage.set_text(f"{nice.get(st, st)} (tid {tr.get('tid')})")
        detail.set_text(tr.get("description", ""))

    err = None
    try:
        tr, diff = await views.client.commit_diff(instance, on_update=update)
    except (RestconfError, TimeoutError) as e:
        tr, diff, err = None, "", str(e)
    finally:
        if after:
            try:
                await after()
            except RestconfError as e:
                ui.notify(f"Could not restore candidate: {e}", type="negative", multi_line=True, close_button=True, timeout=0)
        wait.close()
    try:
        devs = await views.client.devices()
    except RestconfError:
        devs = []
    closed = [d["name"] for d in devs if d.get("conn-state") != "OPEN"]
    ok = bool(tr) and tr.get("result") == "SUCCESS"
    with ui.dialog() as d, ui.card().classes("w-[900px] max-w-full"):
        ui.label(title).classes("text-lg")
        with ui.row().classes("items-center gap-2"):
            ui.icon("check_circle" if ok else "error", color="positive" if ok else "negative")
            ui.label(what).classes("text-sm")
        if tr:
            ui.label(f"Transaction {tr.get('tid')}: {tr.get('result', tr.get('state'))} · {tr.get('description', '')} · {_secs(tr)}"
                     ).classes("text-xs text-gray-400")
            if tr.get("warning"):
                ui.label(f"Warning: {tr['warning']}").classes("text-xs warn-tx")
        ui.label((note + " " if note else "") + "Nothing was pushed to the devices.").classes("text-sm text-gray-400")
        if closed:
            ui.label(f"Not connected: {', '.join(closed)}. Services cannot produce a diff for closed devices — "
                     "open them on the Devices page first.").classes("text-sm warn-tx")
        if ok:
            diffview.render(diff)
        else:
            reason = err or (tr or {}).get("reason") or (tr or {}).get("result") or "no result"
            ui.label(f"Service run failed: {reason.strip()}").classes("err-tx whitespace-pre-wrap")
        with ui.row():
            ui.button("Close", on_click=d.close).props("flat no-caps no-wrap").classes(BTN)
            ui.button("Go to Diff / Commit", icon="difference", on_click=lambda: ui.navigate.to("/commit")).props("outline no-caps no-wrap").classes(BTN)
    d.open()


@ui.page("/services", response_timeout=30)
async def services_overview():
    schema = await _prologue()
    ui.label("Services").classes("text-2xl")
    if schema is None:
        return
    services = await views.guarded(views.client.candidate_services()) or {}
    ui.label("Service instances in the candidate datastore. Pick a type to create or edit instances.").classes("text-gray-400")
    with ui.element("div").classes("w-full gap-4 mt-2 grid").style(
            "grid-template-columns:repeat(auto-fill,minmax(250px,1fr))"):
        for svc in schema.services:
            n = len(_instances(svc, services))
            with ui.card().classes("w-full h-36 cursor-pointer hover:border-primary overflow-hidden").on(
                    "click", lambda s=svc: ui.navigate.to(f"/services/{quote(_qname(s))}")):
                with ui.row().classes("items-center w-full"):
                    ui.icon("hub").classes("text-primary")
                    ui.label(svc.name).classes("text-lg font-medium")
                ui.label(f"{n} instance{'s' if n != 1 else ''}").classes("text-gray-400")
                ui.label(svc.description or svc.module).classes("text-xs text-gray-500 line-clamp-2")


@ui.page("/services/{qname}", response_timeout=30)
async def service_type_page(qname: str):
    schema = await _prologue()
    if schema is None:
        return
    svc = schema.service(qname)
    if svc is None:
        ui.label(f"Unknown service type {qname}").classes("text-negative")
        return
    services = await views.guarded(views.client.candidate_services()) or {}
    rows = [{"key": str(e.get("service-name", "")), "desc": str(e.get("description", "")),
             "status": "Deployed" if e.get("created") else "Not deployed"} for e in _instances(svc, services)]
    rows.sort(key=lambda r: r["key"])

    with page_column():
        with ui.row().classes("w-full items-center"):
            ui.button(icon="arrow_back", on_click=lambda: ui.navigate.to("/services")).props("flat round dense")
            ui.label(svc.name).classes("text-2xl")
            ui.label(f"{len(rows)} instances").classes("mut")
            ui.space()
            flt = ui.input(placeholder="Filter…").props("dense outlined clearable").classes("w-56")
            ui.button("Commit diff", icon="preview",
                      on_click=lambda: commit_diff_dialog(
                          "Device diff for changed services", "Running all services whose configuration has changed in candidate")
                      ).props("dense no-caps no-wrap outline").classes(BTN).tooltip(
                "Run service actions on the candidate and show the device diff. Nothing is pushed.")
            ui.button(f"New {svc.name}", icon="add", on_click=lambda: ui.navigate.to(f"/services/{quote(qname)}/form")
                      ).props("dense no-caps no-wrap").classes(BTN)

        table = data_table(
            [{"name": "key", "label": "service-name", "field": "key", "align": "left", "sortable": True, "classes": "name"},
             {"name": "desc", "label": "Description", "field": "desc", "align": "left"},
             {"name": "status", "label": "Status", "field": "status", "align": "left"},
             {"name": "act", "label": "", "field": "key", "align": "right"}],
            rows, "key", RAIL_SERVICE)
        table.bind_filter_from(flt, "value")
        table.add_slot("body-cell-status", '''
            <q-td :props="props"><span :class="'pill ' + (props.value == 'Deployed' ? 'pill-OPEN' : 'pill-other')">{{props.value}}</span></q-td>''')
        table.add_slot("body-cell-act", """
            <q-td :props="props" class="row-actions">
              <q-btn flat dense round size="md" icon="visibility" @click.stop="$parent.$emit('diff', props.row.key)"><q-tooltip>Preview device diff (no changes)</q-tooltip></q-btn>
              <q-btn flat dense round size="md" icon="edit" @click.stop="$parent.$emit('edit', props.row.key)"><q-tooltip>Edit</q-tooltip></q-btn>
              <q-btn flat dense round size="md" icon="content_copy" @click.stop="$parent.$emit('dup', props.row.key)"><q-tooltip>Duplicate</q-tooltip></q-btn>
              <q-btn flat dense round size="md" icon="delete" class="act-del" @click.stop="$parent.$emit('del', props.row.key)"><q-tooltip>Delete</q-tooltip></q-btn>
            </q-td>""")
        table.on("diff", lambda e: commit_diff_dialog(
            f"Device diff for {svc.name} '{e.args}'", f"Re-applying service {svc.name} '{e.args}' (force)",
            views.client.service_instance(svc.name, svc.keys[0], e.args)))
        table.on("dup", lambda e: ui.navigate.to(f"/services/{quote(qname)}/form?copy={quote(e.args, safe='')}"))
        table.on("edit", lambda e: ui.navigate.to(f"/services/{quote(qname)}/form?key={quote(e.args, safe='')}"))
        table.on("rowClick", lambda e: ui.navigate.to(f"/services/{quote(qname)}/form?key={quote(e.args[1]['key'], safe='')}"))

    async def delete(key: str) -> None:
        with ui.dialog() as d, ui.card():
            ui.label(f"Delete {svc.name} '{key}' from the candidate datastore?").classes("text-lg")
            ui.label("Takes effect on devices only after you deploy (Diff / Commit).").classes("mut")
            with ui.row():
                ui.button("Cancel", on_click=d.close).props("flat no-caps no-wrap").classes(BTN)
                ui.button("Delete", color="negative", on_click=lambda: d.submit(True)).props("no-caps no-wrap").classes(BTN)
        if await d:
            await views.guarded(views.client.delete_service(svc.module, svc.name, key), f"Deleted {key} (candidate)")
            ui.navigate.reload()

    table.on("del", lambda e: delete(e.args))


@ui.page("/services/{qname}/form", response_timeout=30)
async def service_form_page(qname: str, key: str = "", copy: str = ""):
    """Create (no key), edit (key) or duplicate (copy=<key of the source>) a service instance."""
    schema = await _prologue()
    if schema is None:
        return
    svc = schema.service(qname)
    if svc is None:
        ui.label(f"Unknown service type {qname}").classes("text-negative")
        return
    services = await views.guarded(views.client.candidate_services()) or {}
    lookup = await _load_lookup(services)
    editing = bool(key)
    data: dict = {}
    original: dict = {}  # instance as it is in candidate now, for reverting a temporary apply
    preserved: dict = {}  # controller-managed nodes hidden from the form; must survive the PUT
    if editing:
        found = next((e for e in _instances(svc, services) if str(e.get("service-name")) == key), None)
        if found is None:
            ui.label(f"{svc.name} '{key}' not found in candidate").classes("text-negative")
            return
        data = entry_from_json(svc, found)
        preserved = {k: v for k, v in found.items() if k == "created"}
        original = found
    key_leaf = svc.keys[0]
    if copy and not editing:
        src = next((e for e in _instances(svc, services) if str(e.get(key_leaf)) == copy), None)
        if src is None:
            ui.label(f"{svc.name} '{copy}' not found in candidate").classes("text-negative")
            return
        # a copy is a brand-new instance: same settings, new name, and none of the controller's
        # bookkeeping (`created` is dropped because it belongs to the source's device config)
        data = entry_from_json(svc, src)
        taken = {str(e.get(key_leaf)) for e in _instances(svc, services)}
        new_key, n = f"{copy}-copy", 2
        while new_key in taken:
            new_key, n = f"{copy}-copy{n}", n + 1
        data[key_leaf] = new_key

    with ui.row().classes("w-full items-center"):
        ui.button(icon="arrow_back", on_click=lambda: ui.navigate.to(f"/services/{quote(qname)}")).props("flat round dense")
        ui.label(f"{'Edit' if editing else 'New'} {svc.name}" + (f": {key}" if editing else "")).classes("text-2xl")
        if copy and not editing:
            ui.label(f"copy of {copy}").classes("mut")
        ui.space()
        if editing:
            ui.button("Duplicate", icon="content_copy",
                      on_click=lambda: ui.navigate.to(f"/services/{quote(qname)}/form?copy={quote(key, safe='')}")
                      ).props("outline no-caps no-wrap").classes(BTN).tooltip("Create a new instance with the same settings")
        ui.button("Show JSON", icon="data_object", on_click=lambda: _show_json(svc, data, preserved)).props("outline no-caps no-wrap").classes(BTN)
    if svc.description:
        ui.label(svc.description).classes("text-gray-400")

    status = ui.label().classes("text-sm text-warning")
    dirty = {"v": False}

    def touch() -> None:
        dirty["v"] = True
        status.set_text("Unsaved changes")

    with ui.column().classes("w-full gap-3 mt-2"):
        render_children(svc, data, lookup, touch, locked={key_leaf} if editing else set())

    def check() -> str | None:
        """Validate the form; returns the key, or None after showing the errors."""
        errs = validate(svc, data, lookup)
        if not editing and (k := data.get(key_leaf)) and any(
                str(e.get(key_leaf)) == str(k) for e in _instances(svc, services)):
            errs.append(f"{svc.name}/{key_leaf}: '{k}' already exists")
        if errs:
            with ui.dialog() as d, ui.card():
                ui.label("Fix these before saving").classes("text-lg")
                for e in errs[:30]:
                    ui.label("• " + e).classes("text-sm err-tx")
                ui.button("OK", on_click=d.close).props("no-caps no-wrap").classes(BTN)
            d.open()
            return None
        return str(data[key_leaf])

    async def write(k: str) -> bool:
        try:
            await views.client.put_service(svc.module, svc.name, k, service_to_json(svc, data, preserved))
        except RestconfError as e:
            ui.notify(f"Controller rejected the change: {e}", type="negative", multi_line=True, close_button=True, timeout=0)
            return False
        return True

    async def persist() -> str | None:
        """Validate the form and write it to the candidate datastore; returns the key or None."""
        k = check()
        if k is None or not await write(k):
            return None
        dirty["v"] = False
        status.set_text("Saved to candidate")
        return k

    async def save(review: bool) -> None:
        if (k := await persist()) is None:
            return
        ui.notify(f"Saved {svc.name} '{k}' to candidate", type="positive")
        ui.navigate.to("/commit" if review else f"/services/{quote(qname)}")

    async def preview() -> None:
        """Commit diff of this instance. Edits are applied to candidate only temporarily and reverted."""
        k = check()
        if k is None:
            return
        title, what = f"Device diff for {svc.name} '{k}'", f"Re-applying service {svc.name} '{k}' (force)"
        inst = views.client.service_instance(svc.name, key_leaf, k)
        if editing and not dirty["v"]:
            await commit_diff_dialog(title, what, inst)  # nothing to apply, candidate untouched
            return
        if not await write(k):
            return

        async def revert() -> None:
            if editing:  # put back exactly what was in candidate before
                await views.client.put_service(svc.module, svc.name, k, {f"{svc.module}:{svc.name}": [original]})
            else:
                await views.client.delete_service(svc.module, svc.name, k)

        await commit_diff_dialog(title, what, inst, after=revert,
                                 note="Your edits were applied temporarily to compute this diff and then reverted; the candidate is unchanged.")

    ui.separator()
    with ui.row().classes("w-full items-center sticky bottom-0 bg-page py-2"):
        ui.button("Save to candidate", icon="save", on_click=lambda: save(False)).props("no-caps no-wrap").classes(BTN)
        ui.button("Commit diff", icon="preview", on_click=preview).props("no-caps no-wrap outline").classes(BTN).tooltip(
            "Show what this would change on the devices, without saving. Nothing is pushed.")
        ui.button("Save & go to Commit", icon="difference", on_click=lambda: save(True)).props("no-caps no-wrap flat").classes(BTN).tooltip(
            "Save to candidate, then open the Diff / Commit page (where you can deploy)")
        ui.button("Cancel", on_click=lambda: ui.navigate.to(f"/services/{quote(qname)}")).props("flat no-caps no-wrap").classes(BTN)


def _show_json(svc: Node, data: dict, preserved: dict) -> None:
    import json
    with ui.dialog() as d, ui.card().classes("w-[700px]"):
        ui.code(json.dumps(service_to_json(svc, data, preserved), indent=2), language="json").classes("w-full")
        ui.button("Close", on_click=d.close).props("no-caps no-wrap").classes(BTN)
    d.open()
