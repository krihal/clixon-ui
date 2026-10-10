"""Services pages: browse instances per service type, create/edit them with a YANG-generated form."""

from __future__ import annotations

import asyncio
from urllib.parse import quote, unquote

from nicegui import ui

from . import diffview, views
from .client import RestconfError
from .formdata import (Lookup, entry_from_json, property_entries, property_key, property_to_json, service_to_json,
                       validate)
from .forms import render_children
from .style import BTN, BTN_BAR, BTN_TOOLBAR
from .tables import RAIL_SERVICE, data_table, page_column
from .schema import Node, Schema, load_schema
from .servicechanges import changed_instances

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


async def commit_diff_dialog(title: str, what: str, instance: str | None = None, note: str = "", after=None, commit=None) -> None:
    """Run service actions on the candidate (nothing pushed), showing progress, then the per-device diff.

    `after` is an async cleanup run as soon as the services finished (before results are shown).
    `what` describes in words which services run; `instance` is a controller service-instance
    (force re-apply of that one) or None (run all services whose config changed).
    `commit` (async, optional) adds a Commit button: it closes this dialog and starts the commit flow."""
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
            if commit and ok:
                async def do_commit() -> None:
                    d.close()
                    await commit()
                ui.button("Commit", icon="rocket_launch", color="negative", on_click=do_commit).props("no-caps no-wrap").classes(BTN)
    d.open()


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


async def commit_flow(name: str, instance: str | None, *, own: str | None = None, apply=None, revert=None) -> bool:
    """Commit one service instance (or, with instance=None, every service whose config changed) after asking.

    1. Dry run (no push) to learn which devices would change, plus a check for other uncommitted candidate edits.
    2. A confirmation dialog that shows exactly that. Nothing is committed unless the user agrees.
    3. The real commit, with progress, and a result dialog.
    `apply`/`revert` (async, optional) put a pending edit into the candidate temporarily for step 1 and take it out
    again; `apply` is called once more right before the commit. `own` is how the instance appears in the
    candidate-vs-running comparison ("l2c 'X'"). Returns True when the commit succeeded."""
    client = views.client
    dlg, update = _progress("Checking what will be committed", f"Dry run for {name}: nothing is pushed yet")
    tr, diff, cand, run, err = None, "", {}, {}, None
    try:
        if apply:
            await apply()
        try:
            tr, diff = await client.commit_diff(instance, on_update=update)
            cand, run = await client.candidate_services(), await client.running_services()
        finally:
            if revert:
                await revert()
    except (RestconfError, TimeoutError) as e:
        err = str(e) or type(e).__name__
    finally:
        dlg.close()
        _dispose(dlg)
    if err or not tr or tr.get("result") != "SUCCESS":
        with ui.dialog() as d, ui.card().classes("w-[560px] gap-2"):
            ui.label("Cannot commit").classes("text-lg")
            ui.label((err or (tr or {}).get("reason") or "The dry run failed").strip()).classes("err-box whitespace-pre-wrap w-full")
            ui.button("Close", on_click=d.close).props("flat no-caps no-wrap").classes(BTN)
        d.open()
        return False

    secs = diffview.parse(diff)
    others = [c for c in changed_instances(cand, run) if c != own] if instance is not None else []
    with ui.dialog() as d, ui.card().classes("w-[900px] max-w-full gap-2"):
        ui.label(f"Commit {name}?").classes("text-lg")
        ui.label("This pushes the configuration to the devices below and commits it."
                 if secs else "No device configuration changes. Only the controller's own configuration is committed.").classes("mut")
        if secs:
            with ui.column().classes("w-full gap-2 overflow-auto").style("max-height:55vh"):
                diffview.render(diff)
        if others:
            ui.label("The candidate also contains uncommitted changes to other services: " + ", ".join(others[:8])
                     + (f" and {len(others) - 8} more" if len(others) > 8 else "") +
                     ". A commit applies the whole candidate, so these are committed too.").classes("warn-tx text-sm")
        with ui.row().classes("justify-end w-full"):
            ui.button("Cancel", on_click=d.close).props("flat no-caps no-wrap").classes(BTN)
            ui.button("Commit anyway" if others else "Commit", icon="rocket_launch", color="negative",
                      on_click=lambda: d.submit(True)).props("no-caps no-wrap").classes(BTN)
    confirmed = await d
    _dispose(d)
    if not confirmed:
        return False

    return await _run_commit(name, lambda upd: client.commit_service(instance, on_update=upd), apply=apply)


async def _run_commit(name: str, runner, *, apply=None, verb: str = "Commit") -> bool:
    """Run the real commit transaction with progress and show its result. `runner(update)` starts and awaits it."""
    dlg, update = _progress(f"{ {'Commit': 'Committing', 'Delete': 'Deleting'}.get(verb, verb) } {name}", "Pushing to the devices and committing")
    tr, err = None, None
    try:
        if apply:
            await apply()
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

        async def commit_changed() -> None:
            client = ui.context.client
            if await commit_flow("all changed services", None):
                views.reload_page(client)

        with ui.row().classes("w-full items-center gap-3"):
            ui.button("Commit diff", icon="preview",
                      on_click=lambda: commit_diff_dialog(
                          "Device diff for changed services", "Running all services whose configuration has changed in candidate",
                          commit=commit_changed)
                      ).props("dense no-caps no-wrap outline").classes(BTN).tooltip(
                "Run service actions on the candidate and show the device diff. Nothing is pushed.")

            ui.button("Commit", icon="rocket_launch", color="negative", on_click=commit_changed).props("dense no-caps no-wrap").classes(BTN + " wr").tooltip(
                "Push every service whose configuration changed in the candidate to the devices and commit")
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
              <q-btn flat dense round size="md" icon="visibility" @click.stop="$parent.$emit('diff', props.row.key)"><q-tooltip>Commit diff: show what would change on the devices</q-tooltip></q-btn>
              <q-btn flat dense round size="md" icon="rocket_launch" class="wr act-commit" @click.stop="$parent.$emit('commit', props.row.key)"><q-tooltip>Commit this service to the devices…</q-tooltip></q-btn>
              <q-btn flat dense round size="md" icon="edit" class="wr" @click.stop="$parent.$emit('edit', props.row.key)"><q-tooltip>Edit</q-tooltip></q-btn>
              <q-btn flat dense round size="md" icon="content_copy" class="wr" @click.stop="$parent.$emit('dup', props.row.key)"><q-tooltip>Duplicate</q-tooltip></q-btn>
              <q-btn flat dense round size="md" icon="delete" class="wr act-del" @click.stop="$parent.$emit('del', props.row.key)"><q-tooltip>Delete</q-tooltip></q-btn>
            </q-td>""")
        async def commit_row(key: str) -> None:
            client = ui.context.client
            name = f"{svc.name} '{key}'"
            if await commit_flow(name, views.client.service_instance(svc.name, svc.keys[0], key), own=name):
                views.reload_page(client)

        table.on("diff", lambda e: commit_diff_dialog(
            f"Device diff for {svc.name} '{e.args}'", f"Re-applying service {svc.name} '{e.args}' (force)",
            views.client.service_instance(svc.name, svc.keys[0], e.args), commit=lambda: commit_row(e.args)))

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
        ui.navigate.to("/commit" if review else f"/services/{quote(qname)}")

    async def revert_edit(k: str) -> None:
        """Put the candidate back exactly as it was before this form's edit was applied."""
        if editing:
            await views.client.put_service(svc.module, svc.name, k, {f"{svc.module}:{svc.name}": [original]})
        else:
            await views.client.delete_service(svc.module, svc.name, k, svc.keys)

    async def commit_click() -> None:
        """Commit this instance. Pending form edits are applied only for the dry run, then reverted; they are saved
        for real only if the user confirms."""
        client = ui.context.client
        k = check()
        if k is None:
            return
        name = f"{svc.name} '{k}'"
        pending = dirty["v"] or not editing

        async def apply_edit() -> None:
            if not await write(k):
                raise RestconfError("The controller did not accept the change")

        ok = await commit_flow(name, views.client.service_instance(svc.name, key_leaf, k), own=name,
                               apply=apply_edit if pending else None,
                               revert=(lambda: revert_edit(k)) if pending else None)
        if ok:
            dirty["v"] = False
            if editing:
                views.reload_page(client)  # fresh `created` and status
            else:
                views.navigate_to(f"/services/{quote(qname)}", client)

    async def preview() -> None:
        """Commit diff of this instance. Edits are applied to candidate only temporarily and reverted."""
        k = check()
        if k is None:
            return
        title, what = f"Device diff for {svc.name} '{k}'", f"Re-applying service {svc.name} '{k}' (force)"
        inst = views.client.service_instance(svc.name, key_leaf, k)
        if editing and not dirty["v"]:
            await commit_diff_dialog(title, what, inst, commit=commit_click)  # nothing to apply, candidate untouched
            return
        if not await write(k):
            return

        await commit_diff_dialog(title, what, inst, after=lambda: revert_edit(k), commit=commit_click,
                                 note="Your edits were applied temporarily to compute this diff and then reverted; the candidate is unchanged.")

    async def delete_here() -> None:
        client = ui.context.client
        if await delete_flow(svc, key):
            views.navigate_to(f"/services/{quote(qname)}", client)

    with footer:
        ui.button("Save", icon="save", on_click=lambda: save(False)).props("no-caps no-wrap").classes(BTN_BAR + " wr").tooltip(
            "Save to the candidate datastore. Nothing is pushed to the devices.")
        ui.button("Commit diff", icon="preview", on_click=preview).props("no-caps no-wrap outline").classes(BTN_BAR).tooltip(
            "Show what this would change on the devices, without saving. Nothing is pushed.")
        ui.button("Commit", icon="rocket_launch", color="negative", on_click=commit_click).props("no-caps no-wrap").classes(BTN_BAR + " wr").tooltip(
            "Push this service to the devices and commit. You are asked to confirm first.")
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
