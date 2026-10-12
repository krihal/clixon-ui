"""Services pages: browse instances per service type, create/edit them with a YANG-generated form."""

from __future__ import annotations

import asyncio
import time
from urllib.parse import quote, unquote

from nicegui import ui

from . import pending, views
from .client import RestconfError
from .formdata import (Lookup, entry_from_json, property_entries, property_key, property_to_json, service_to_json,
                       validate)
from .forms import render_children
from .style import BTN, BTN_BAR, BTN_TOOLBAR
from .tables import RAIL_SERVICE, data_table, page_column
from .schema import Node, Schema, load_schema, module_list

_schema_task: asyncio.Task | None = None


SCHEMA_RECHECK_SECONDS = 60  # how often the YANG library is looked at to see whether modules changed
_schema_checked = 0.0


def preload(force: bool = False) -> None:
    """Start loading the YANG schema in the background (call from app startup). `force`: fetch every module again."""
    global _schema_task, _schema_checked
    _schema_task = asyncio.create_task(load_schema(views.client, force=force))
    _schema_checked = time.monotonic()


async def get_schema() -> Schema:
    """The parsed schema. At most once a minute the controller's module list (a small read) is compared with the one the
    schema was built from; if modules were added, removed or got a new revision, the schema is loaded again."""
    global _schema_task, _schema_checked
    if _schema_task is None or (_schema_task.done() and _schema_task.exception()):
        preload()  # first use, or retry after a failed load
    schema = await asyncio.shield(_schema_task)
    if time.monotonic() - _schema_checked > SCHEMA_RECHECK_SECONDS:
        _schema_checked = time.monotonic()
        try:
            if module_list(await views.client.yang_modules()) != schema.modules:
                preload()
                schema = await asyncio.shield(_schema_task)
        except RestconfError:
            pass  # keep what we have; the next page load looks again
    return schema


async def reload_schema() -> None:
    """For a YANG file that changed without a new revision: nothing else can tell, so the user asks."""
    preload(force=True)
    await get_schema()


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


async def _prologue():
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


STAGES = ("INIT", "ACTIONS", "RESOLVED", "DONE")
STAGE_TEXT = {"INIT": "Transaction started", "ACTIONS": "Service scripts running",
              "RESOLVED": "Computing device config", "DONE": "Done"}


def _dispose(*dialogs) -> None:
    """Delete closed dialogs once their hide animation has finished, so no stale backdrop stays in the page."""
    asyncio.get_running_loop().call_later(0.6, lambda: [d.delete() for d in dialogs if not d.is_deleted])


def _progress(title: str, what: str):
    """A small modal with live transaction progress. Returns (dialog, update(tr))."""
    with ui.dialog().props("persistent") as dlg, ui.card().classes("w-[460px]"):
        ui.label(title).classes("text-lg")
        ui.label(what).classes("text-sm mut")
        with ui.row().classes("items-center gap-2"):
            ui.spinner(size="sm")
            stage = ui.label("Starting transaction…")
        bar = ui.linear_progress(value=0.05, show_value=False).props("instant-feedback")
        detail = ui.label().classes("text-xs mut")
    dlg.open()

    def update(tr: dict) -> None:
        st = tr.get("state", "INIT")
        bar.set_value((STAGES.index(st) + 1) / len(STAGES) if st in STAGES else 0.1)
        stage.set_text(f"{STAGE_TEXT.get(st, st)} (tid {tr.get('tid')})")
        detail.set_text(tr.get("description", ""))

    return dlg, update


async def _run_commit(name: str, runner, *, verb: str = "Commit") -> bool:
    """Run the real commit transaction with progress and show its result. `runner(update)` starts and awaits it."""
    dlg, update = _progress(f"{ {'Commit': 'Committing', 'Delete': 'Deleting'}.get(verb, verb) } {name}", "Pushing to the devices and committing")
    tr, err = None, None
    try:
        tr = await runner(update)
    except (RestconfError, TimeoutError) as e:
        err = str(e) or type(e).__name__
    finally:
        dlg.close()
        _dispose(dlg)
    ok = bool(tr) and tr.get("result") == "SUCCESS" and not err
    with ui.dialog() as d, ui.card().classes("w-[560px] gap-2"):
        with ui.row().classes("items-center gap-2"):
            ui.icon("check_circle" if ok else "error", color="positive" if ok else "negative")
            ui.label(f"{verb} {'succeeded' if ok else 'failed'}").classes("text-lg")
        if tr:
            ui.label(f"Transaction {tr.get('tid')} · {tr.get('description', '')} · {_secs(tr)}").classes("text-xs mut")
        if not ok:
            ui.label((err or (tr or {}).get("reason") or "no result").strip()).classes("err-box whitespace-pre-wrap w-full")
        with ui.row():
            ui.button("Close", on_click=d.close).props("flat no-caps no-wrap").classes(BTN)
            if tr:
                ui.button("Transaction details", icon="receipt_long", on_click=lambda: views.transaction_dialog(tr)).props("outline no-caps no-wrap").classes(BTN)
    await d  # return (and let the caller reload the page) only once the user has read the result and closed it
    _dispose(d)
    await asyncio.sleep(0.4)  # let the dialog finish its hide animation; removing it mid-way leaves a click-blocking backdrop
    return ok


async def delete_flow(svc: Node, key: str) -> bool:
    """Delete a service instance. Two ways, the staged one first:
    - from the candidate only (nothing reaches the devices until a later commit), or
    - "delete & commit": the controller's DELETE action removes the instance *and its device configuration*.
    Returns True when something was deleted."""
    name = f"{svc.name} '{key}'"
    with ui.dialog() as d, ui.card().classes("w-[600px] max-w-full gap-2"):
        ui.label(f"Delete {name}?").classes("text-lg")
        ui.label("Delete from candidate removes the instance from the controller's candidate configuration only. "
                 "The devices keep their configuration until the next commit.").classes("text-sm")
        ui.label("Delete & commit removes the instance AND the configuration it created on the devices, and commits "
                 "right away. A commit applies the whole candidate.").classes("warn-tx text-sm")
        with ui.row().classes("justify-end w-full"):
            ui.button("Cancel", on_click=lambda: d.submit("")).props("flat no-caps no-wrap").classes(BTN)
            ui.button("Delete from candidate", on_click=lambda: d.submit("candidate")).props("outline no-caps no-wrap").classes(BTN)
            ui.button("Delete & commit", icon="delete_forever", color="negative", on_click=lambda: d.submit("commit")).props("no-caps no-wrap").classes(BTN)
    choice = await d
    _dispose(d)
    if choice == "candidate":
        try:
            await views.client.delete_service(svc.module, svc.name, key, svc.keys)
        except RestconfError as e:
            ui.notify(f"Could not delete: {e}", type="negative", multi_line=True, close_button=True, timeout=0)
            return False
        ui.notify(f"Deleted {name} from the candidate", type="positive")
        return True
    if choice == "commit":
        inst = views.client.service_instance(svc.name, svc.keys[0], key)
        # one more explicit question, because this reaches the devices
        with ui.dialog() as d2, ui.card().classes("w-[520px] gap-2"):
            ui.label(f"Really delete {name} from the devices?").classes("text-lg")
            ui.label("Its configuration is removed from the devices and the change is committed. This cannot be undone.").classes("warn-tx")
            with ui.row().classes("justify-end w-full"):
                ui.button("Cancel", on_click=d2.close).props("flat no-caps no-wrap").classes(BTN)
                ui.button("Delete & commit", icon="delete_forever", color="negative", on_click=lambda: d2.submit(True)).props("no-caps no-wrap").classes(BTN)
        confirmed2 = await d2
        _dispose(d2)
        if not confirmed2:
            return False
        return await _run_commit(name, lambda upd: views.client.delete_service_commit(inst, on_update=upd), verb="Delete")
    return False


async def services_overview():
    schema = await _prologue()
    with ui.row().classes("w-full items-center"):
        ui.label("Services").classes("text-2xl")
        ui.space()

        async def reload() -> None:
            client = ui.context.client
            ui.notify("Loading the YANG schema again…")
            try:
                await reload_schema()
            except Exception as e:  # noqa: BLE001 - show anything to the user
                ui.notify(f"Could not load the schema: {e}", type="negative", multi_line=True, close_button=True)
                return
            views.reload_page(client)

        ui.button("Reload YANG", icon="refresh", on_click=reload).props("outline no-caps no-wrap dense").classes(BTN_TOOLBAR).tooltip(
            "Fetch the controller's YANG modules again. New revisions are picked up by themselves; use this when a module "
            "changed without a new revision.")
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
    if props := schema.properties():
        ui.label("Properties").classes("text-lg mt-4")
        ui.label("Settings shared by all instances of a service type.").classes("mut")
        with ui.element("div").classes("w-full gap-4 mt-2 grid").style(
                "grid-template-columns:repeat(auto-fill,minmax(250px,1fr))"):
            for p in props:
                with ui.card().classes("w-full h-36 cursor-pointer hover:border-primary overflow-hidden").on(
                        "click", lambda p=p: ui.navigate.to(f"/service-properties/{quote(_qname(p))}")):
                    with ui.row().classes("items-center w-full"):
                        ui.icon("tune").classes("text-primary")
                        ui.label(p.name).classes("text-lg font-medium")
                    ui.label(p.description or p.module).classes("text-xs text-gray-500 line-clamp-3")


async def service_type_page(qname: str):
    qname = unquote(qname)
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
        with ui.row().classes("w-full items-center h-11 shrink-0"):
            ui.button(icon="arrow_back", on_click=lambda: ui.navigate.to("/services")).props("flat round dense")
            ui.label(svc.name).classes("text-2xl")
            ui.label(f"{len(rows)} instances").classes("mut")

        with ui.row().classes("w-full items-center gap-3"):
            ui.button("Review & commit", icon="rocket_launch", color="negative", on_click=lambda: pending.open_panel()).props(
                "dense no-caps no-wrap").classes(BTN + " wr").tooltip(
                "Show everything pending (candidate and device changes) and commit it")
            flt = ui.input(placeholder="Filter…").props("dense outlined clearable").classes("grow min-w-52")
            ui.button("Add", icon="add", on_click=lambda: ui.navigate.to(f"/services/{quote(qname)}/form")
                      ).props("dense no-caps no-wrap").classes(BTN_TOOLBAR + " wr")

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
                            <q-btn flat dense round size="md" icon="rocket_launch" class="wr act-commit" @click.stop="$parent.$emit('commit', props.row.key)"><q-tooltip>Review and re-apply this service on the devices…</q-tooltip></q-btn>
              <q-btn flat dense round size="md" icon="edit" class="wr" @click.stop="$parent.$emit('edit', props.row.key)"><q-tooltip>Edit</q-tooltip></q-btn>
              <q-btn flat dense round size="md" icon="content_copy" class="wr" @click.stop="$parent.$emit('dup', props.row.key)"><q-tooltip>Duplicate</q-tooltip></q-btn>
              <q-btn flat dense round size="md" icon="delete" class="wr act-del" @click.stop="$parent.$emit('del', props.row.key)"><q-tooltip>Delete</q-tooltip></q-btn>
            </q-td>""")
        async def commit_row(key: str) -> None:
            name = f"{svc.name} '{key}'"
            await pending.open_panel(views.client.service_instance(svc.name, svc.keys[0], key), name)

        table.on("commit", lambda e: commit_row(e.args))
        table.on("dup", lambda e: ui.navigate.to(f"/services/{quote(qname)}/form?copy={quote(e.args, safe='')}"))
        table.on("edit", lambda e: ui.navigate.to(f"/services/{quote(qname)}/form?key={quote(e.args, safe='')}"))
        table.on("rowClick", lambda e: ui.navigate.to(f"/services/{quote(qname)}/form?key={quote(e.args[1]['key'], safe='')}"))

    async def delete(key: str) -> None:
        client = ui.context.client
        if await delete_flow(svc, key):
            views.reload_page(client)

    table.on("del", lambda e: delete(e.args))


async def service_form_page(qname: str, key: str = "", copy: str = ""):
    qname = unquote(qname)
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
    preserved: dict = {}  # controller-managed nodes hidden from the form; must survive the PUT
    if editing:
        found = next((e for e in _instances(svc, services) if str(e.get("service-name")) == key), None)
        if found is None:
            ui.label(f"{svc.name} '{key}' not found in candidate").classes("text-negative")
            return
        data = entry_from_json(svc, found)
        preserved = {k: v for k, v in found.items() if k == "created"}
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

    # window-height layout: title on top, the fields scroll in the middle, the action bar stays at the bottom
    with page_column():
        head = ui.column().classes("w-full gap-1 shrink-0")
        body = ui.column().classes("w-full gap-3 grow overflow-auto pr-2").style("min-height:0")
        footer = ui.row().classes("w-full items-center gap-3 shrink-0 py-3 border-t line")
    with head:
        with ui.row().classes("w-full items-center"):
            ui.button(icon="arrow_back", on_click=lambda: ui.navigate.to(f"/services/{quote(qname)}")).props("flat round dense")
            ui.label(f"{'Edit' if editing else 'New'} {svc.name}" + (f": {key}" if editing else "")).classes("text-2xl")
            if copy and not editing:
                ui.label(f"copy of {copy}").classes("mut")
        if svc.description:
            ui.label(svc.description).classes("mut")
        status = ui.label().classes("text-sm text-warning")
    dirty = {"v": False}

    def touch() -> None:
        dirty["v"] = True
        status.set_text("Unsaved changes")

    with body:
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
        if review:
            await pending.open_panel()
        else:
            ui.navigate.to(f"/services/{quote(qname)}")

    async def delete_here() -> None:
        client = ui.context.client
        if await delete_flow(svc, key):
            views.navigate_to(f"/services/{quote(qname)}", client)

    with footer:
        ui.button("Save", icon="save", on_click=lambda: save(False)).props("no-caps no-wrap").classes(BTN_BAR + " wr").tooltip(
            "Save to the candidate datastore. Nothing is pushed to the devices.")
        ui.button("Save & review", icon="rocket_launch", on_click=lambda: save(True)).props("no-caps no-wrap outline").classes(BTN_BAR + " wr").tooltip(
            "Save to the candidate, then review all pending changes and commit them.")
        # always shown (disabled for a new instance) so every button keeps its place
        ui.button("Delete", icon="delete", on_click=delete_here).props("outline no-caps no-wrap").classes(BTN_BAR + " wr").tooltip(
            "Delete this instance (from the candidate, or from the devices too)").set_enabled(editing)
        ui.button("Duplicate", icon="content_copy",
                  on_click=lambda: ui.navigate.to(f"/services/{quote(qname)}/form?copy={quote(key, safe='')}")
                  ).props("outline no-caps no-wrap").classes(BTN_BAR).tooltip("Create a new instance with the same settings").set_enabled(editing)
        ui.button("Show JSON", icon="data_object", on_click=lambda: _show_json(service_to_json(svc, data, preserved))).props("outline no-caps no-wrap").classes(BTN_BAR)


async def property_form_page(qname: str):
    """Edit one `services/properties` container. Saved to the candidate; services pick it up on the next commit."""
    qname = unquote(qname)
    schema = await _prologue()
    if schema is None:
        return
    prop = schema.property(qname)
    if prop is None:
        ui.label(f"Unknown service property {qname}").classes("text-negative")
        return
    services = await views.guarded(views.client.candidate_services()) or {}
    lookup = await _load_lookup(services)
    current = (services.get("properties") or {})
    found = next((v for k, v in current.items() if k.rpartition(":")[2] == prop.name), None)
    is_list = prop.kind == "list"
    # a list property is edited as one list inside a holder container; a container property directly
    holder = Node(kind="container", name="properties", module=prop.module, children=[prop]) if is_list else prop
    stored = [e for e in (found if isinstance(found, list) else [found] if found else []) if isinstance(e, dict)]
    data = ({prop.name: [entry_from_json(prop, e) for e in stored]} if is_list
            else entry_from_json(prop, found) if isinstance(found, dict) else {})
    old_keys = {property_key(prop, entry_from_json(prop, e)) for e in stored} if is_list else set()

    with page_column():
        head = ui.column().classes("w-full gap-1 shrink-0")
        body = ui.column().classes("w-full gap-3 grow overflow-auto pr-2").style("min-height:0")
        footer = ui.row().classes("w-full items-center gap-3 shrink-0 py-3 border-t line")
    with head:
        with ui.row().classes("w-full items-center"):
            ui.button(icon="arrow_back", on_click=lambda: ui.navigate.to("/services")).props("flat round dense")
            ui.label(f"Properties: {prop.name}").classes("text-2xl")
        if prop.description:
            ui.label(prop.description).classes("mut")
        status = ui.label().classes("text-sm text-warning")

    def touch() -> None:
        status.set_text("Unsaved changes")

    with body:
        render_children(holder, data, lookup, touch)

    async def save() -> None:
        errs = validate(holder, data, lookup)
        if errs:
            with ui.dialog() as d, ui.card():
                ui.label("Fix these before saving").classes("text-lg")
                for e in errs[:30]:
                    ui.label("• " + e).classes("text-sm err-tx")
                ui.button("OK", on_click=d.close).props("no-caps no-wrap").classes(BTN)
            d.open()
            return
        try:
            if is_list:
                entries = property_entries(prop, data)
                for k, body_ in entries.items():
                    await views.client.put_property(prop.module, prop.name, body_, k)
                for k in old_keys - entries.keys():
                    await views.client.delete_property(prop.module, prop.name, k, prop.keys)
                old_keys.clear()
                old_keys.update(entries)
            else:
                await views.client.put_property(prop.module, prop.name, property_to_json(prop, data))
        except RestconfError as e:
            ui.notify(f"Controller rejected the change: {e}", type="negative", multi_line=True, close_button=True, timeout=0)
            return
        status.set_text("Saved to candidate")
        ui.notify(f"Saved properties {prop.name} to candidate", type="positive")

    with footer:
        ui.button("Save", icon="save", on_click=save).props("no-caps no-wrap").classes(BTN_BAR + " wr").tooltip(
            "Save to the candidate datastore. Nothing is pushed to the devices.")
        ui.button("Show JSON", icon="data_object",
                  on_click=lambda: _show_json(property_to_json(prop, data))).props("outline no-caps no-wrap").classes(BTN_BAR)


def _show_json(body: dict) -> None:
    import json
    with ui.dialog() as d, ui.card().classes("w-[700px]"):
        ui.code(json.dumps(body, indent=2), language="json").classes("w-full")
        ui.button("Close", on_click=d.close).props("no-caps no-wrap").classes(BTN)
    d.open()
