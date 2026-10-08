"""Inventory pages: add, edit, duplicate and delete devices, device groups, profiles and templates.

Everything is written to the controller's *candidate* first; a local commit (plain NETCONF commit, nothing is pushed
to devices) makes it take effect. All five kinds share one YANG-generated form (see forms.py)."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from urllib.parse import quote, unquote

from nicegui import ui

from . import diffview, views
from .client import RestconfError
from .formdata import Lookup, entry_from_json, service_to_json, validate
from .forms import render_children
from .schema import Node, data_children
from .service_views import _dispose, _progress, get_schema
from .style import BTN, BTN_BAR
from .tables import RAIL_INVENTORY, data_table, page_column


@dataclass(frozen=True)
class Kind:
    key: str  # YANG list name
    title: str
    plural: str
    list_route: str


KINDS = {
    "device": Kind("device", "Device", "Devices", "/"),
    "device-group": Kind("device-group", "Device group", "Device groups", "/groups"),
    "device-profile": Kind("device-profile", "Profile", "Profiles", "/profiles"),
    "template": Kind("template", "Template", "Templates", "/templates"),
    "rpc-template": Kind("rpc-template", "RPC template", "RPC templates", "/templates?tab=rpc"),
}


def form_url(kind: str, key: str = "", copy: str = "") -> str:
    q = f"?key={quote(key, safe='')}" if key else (f"?copy={quote(copy, safe='')}" if copy else "")
    return f"/inventory/{kind}/form{q}"


def summary(kind: str, e: dict) -> str:
    """One line describing an entry in the list tables."""
    if kind == "device-group":
        members = list(e.get("device-name", [])) + [f"group {g}" for g in e.get("device-group", [])]
        return ", ".join(members) or "(empty)"
    if kind == "device-profile":
        parts = [f"user {e['user']}" if e.get("user") else "", f"port {e['port']}" if e.get("port") else "", str(e.get("conn-type", ""))]
        return " · ".join(p for p in parts if p)
    if kind in ("template", "rpc-template"):
        vs = [v.get("name", "") for v in ((e.get("variables") or {}).get("variable") or [])]
        return ("variables: " + ", ".join(vs)) if vs else "no variables"
    return ""


# --------------------------------------------------------------------------------------------- list pages
async def _list(kind_key: str) -> None:
    """Table of one inventory list with add / edit / duplicate / delete."""
    kind = KINDS[kind_key]
    client = views.client
    try:
        inv = await client.inventory()
    except RestconfError as e:
        ui.label(f"Could not read {kind.plural.lower()}: {e}").classes("err-box")
        return
    entries = inv.get(kind_key, [])
    entries = entries if isinstance(entries, list) else [entries]
    rows = sorted(({"name": str(e["name"]), "descr": str(e.get("description", "")), "summary": summary(kind_key, e)} for e in entries),
                  key=lambda r: r["name"])
    with ui.row().classes("w-full items-center"):
        ui.label(kind.plural).classes("text-2xl")
        ui.label(f"{len(rows)}").classes("mut")
        ui.space()
        flt = ui.input(placeholder="Filter…").props("dense outlined clearable").classes("w-56")
        ui.button(f"New {kind.title.lower()}", icon="add", on_click=lambda: ui.navigate.to(form_url(kind_key))
                  ).props("dense no-caps no-wrap").classes(BTN)
    table = data_table(
        [{"name": "name", "label": "Name", "field": "name", "align": "left", "sortable": True, "classes": "name"},
         {"name": "descr", "label": "Description", "field": "descr", "align": "left"},
         {"name": "summary", "label": "Details", "field": "summary", "align": "left"},
         {"name": "act", "label": "", "field": "name", "align": "right"}],
        rows, "name", RAIL_INVENTORY)
    table.bind_filter_from(flt, "value")
    table.add_slot("body-cell-act", """
        <q-td :props="props" class="row-actions">
          <q-btn flat dense round size="md" icon="edit" @click.stop="$parent.$emit('edit', props.row.name)"><q-tooltip>Edit</q-tooltip></q-btn>
          <q-btn flat dense round size="md" icon="content_copy" @click.stop="$parent.$emit('dup', props.row.name)"><q-tooltip>Duplicate</q-tooltip></q-btn>
          <q-btn flat dense round size="md" icon="delete" class="act-del" @click.stop="$parent.$emit('del', props.row.name)"><q-tooltip>Delete</q-tooltip></q-btn>
        </q-td>""")
    table.on("edit", lambda e: ui.navigate.to(form_url(kind_key, key=e.args)))
    table.on("rowClick", lambda e: ui.navigate.to(form_url(kind_key, key=e.args[1]["name"])))
    table.on("dup", lambda e: ui.navigate.to(form_url(kind_key, copy=e.args)))

    async def delete(key: str) -> None:
        ctx = ui.context.client
        if await delete_flow(kind_key, key):
            views.reload_page(ctx)

    table.on("del", lambda e: delete(e.args))


async def groups_page() -> None:
    with page_column():
        await _list("device-group")


async def profiles_page() -> None:
    with page_column():
        await _list("device-profile")


async def templates_page(tab: str = "config") -> None:
    with page_column():
        which = ui.toggle({"template": "Configuration templates", "rpc-template": "RPC templates"},
                          value="rpc-template" if tab == "rpc" else "template").props(
            "no-caps no-wrap dense unelevated toggle-color=primary color=transparent text-color=dark")
        box = ui.column().classes("w-full grow gap-2 no-wrap").style("min-height:0")

        async def show() -> None:
            box.clear()
            with box:
                await _list(which.value)

        which.on_value_change(lambda e: show())
        await show()


# --------------------------------------------------------------------------------------------- flows
async def _confirm_commit(title: str, intro: str, diff_text: str, warn: str = "", button: str = "Commit") -> bool:
    secs = diffview.parse(diff_text)
    with ui.dialog() as d, ui.card().classes("w-[760px] max-w-full gap-2"):
        ui.label(title).classes("text-lg")
        ui.label(intro).classes("mut")
        if warn:
            ui.label(warn).classes("warn-tx text-sm")
        with ui.scroll_area().classes("w-full").style("max-height:42vh"):
            if secs:
                diffview.render(diff_text)
            else:
                ui.label("The candidate is identical to the running configuration; there is nothing to commit.").classes("mut")
        with ui.row().classes("justify-end w-full"):
            ui.button("Cancel", on_click=lambda: d.submit(False)).props("flat no-caps no-wrap").classes(BTN)
            ui.button(button, icon="rocket_launch", color="negative", on_click=lambda: d.submit(True)).props("no-caps no-wrap").classes(BTN)
    ok = bool(await d)
    _dispose(d)
    return ok


async def _local_commit_dialog(title: str, extra=None) -> bool:
    """Run the local commit with a progress dialog, then show the result. `extra` adds buttons to a success dialog."""
    dlg, _ = _progress(title, "Committing the controller's candidate configuration")
    err = None
    try:
        await views.client.local_commit()
    except RestconfError as e:
        err = str(e) or "failed"
    finally:
        dlg.close()
        _dispose(dlg)
    with ui.dialog() as d, ui.card().classes("w-[560px] gap-2"):
        with ui.row().classes("items-center gap-2"):
            ui.icon("check_circle" if not err else "error", color="positive" if not err else "negative")
            ui.label("Committed" if not err else "Commit failed").classes("text-lg")
        if err:
            ui.label(err.strip()).classes("err-box whitespace-pre-wrap w-full")
        with ui.row():
            ui.button("Close", on_click=d.close).props("flat no-caps no-wrap").classes(BTN)
            if not err and extra:
                extra()
    await d
    _dispose(d)
    await asyncio.sleep(0.4)  # let the dialog finish closing before the caller rebuilds the page
    return err is None


def _connect_buttons(name: str, existed: bool):
    """After a device was committed the controller needs a (re)connect to use the new settings."""
    def build() -> None:
        label = "Reconnect device" if existed else "Open device"
        op = "RECONNECT" if existed else "OPEN"
        ui.button(label, icon="power", on_click=lambda: views.run_tx(views.client.connection_change(name, op), f"{label} {name}")
                  ).props("outline no-caps no-wrap").classes(BTN)
    return build


async def delete_flow(kind_key: str, key: str) -> bool:
    """Delete from the candidate, optionally followed by a (confirmed) commit. True when something was deleted."""
    kind = KINDS[kind_key]
    name = f"{kind.title.lower()} '{key}'"
    client = views.client
    with ui.dialog() as d, ui.card().classes("w-[620px] max-w-full gap-2"):
        ui.label(f"Delete {name}?").classes("text-lg")
        ui.label("Delete from candidate only removes it from the controller's candidate configuration. "
                 "Nothing changes until the candidate is committed.").classes("text-sm")
        ui.label("Delete & commit also commits the whole candidate right away." + (
            " The device and its stored configuration are removed from the controller." if kind_key == "device" else ""
        )).classes("warn-tx text-sm")
        with ui.row().classes("justify-end w-full"):
            ui.button("Cancel", on_click=lambda: d.submit("")).props("flat no-caps no-wrap").classes(BTN)
            ui.button("Delete from candidate", on_click=lambda: d.submit("candidate")).props("outline no-caps no-wrap").classes(BTN)
            ui.button("Delete & commit", icon="delete_forever", color="negative", on_click=lambda: d.submit("commit")).props("no-caps no-wrap").classes(BTN)
    choice = await d
    _dispose(d)
    if not choice:
        return False
    try:
        await client.inventory_delete(kind_key, key)
    except RestconfError as e:
        ui.notify(f"Could not delete: {e}", type="negative", multi_line=True, close_button=True, timeout=0)
        return False
    if choice == "candidate":
        ui.notify(f"Deleted {name} from the candidate. Not committed yet.", type="positive")
        return True
    text = await client.diff_datastores()
    if not await _confirm_commit(f"Commit the deletion of {name}?", "A commit applies the whole candidate. This is what will change:",
                                 text, button="Delete & commit"):
        ui.notify(f"{name} is deleted in the candidate but not committed.", type="warning")
        return True
    return await _local_commit_dialog(f"Deleting {name}")


# --------------------------------------------------------------------------------------------- edit form
async def inventory_form_page(kind: str, key: str = "", copy: str = "") -> None:
    kind_key = unquote(kind)
    if kind_key not in KINDS:
        ui.label(f"Unknown kind {kind_key}").classes("text-negative")
        return
    spec = KINDS[kind_key]
    client = views.client
    try:
        schema = await get_schema()
        node: Node = schema.inventory()[kind_key]
        names = await client.inventory_names()
    except Exception as e:  # noqa: BLE001 - show anything to the user
        ui.label(f"Could not load the schema or inventory: {e}").classes("err-box")
        return
    lookup = Lookup({"devices": {k: [{"name": n} for n in v] for k, v in names.items()}})
    known = {c.name for c in data_children(node)}
    editing = bool(key)
    data: dict = {}
    original: dict = {}  # the entry as it is in the candidate now (only the settings the form knows)
    if editing or copy:
        src_key = key or copy
        found = await client.inventory_entry(kind_key, src_key)
        if found is None:
            ui.label(f"{spec.title} '{src_key}' not found in the candidate").classes("text-negative")
            return
        found = {k: v for k, v in found.items() if k in known}
        data = entry_from_json(node, found)
        if editing:
            original = found
        else:
            new_key, n = f"{copy}-copy", 2
            while new_key in names[kind_key]:
                new_key, n = f"{copy}-copy{n}", n + 1
            data["name"] = new_key
    dirty = {"v": False}

    with page_column():
        head = ui.column().classes("w-full gap-1 shrink-0")
        body = ui.column().classes("w-full gap-3 grow overflow-auto pr-2").style("min-height:0")
        footer = ui.row().classes("w-full items-center gap-3 shrink-0 py-3 border-t line")
    with head:
        with ui.row().classes("w-full items-center"):
            ui.button(icon="arrow_back", on_click=lambda: ui.navigate.to(spec.list_route)).props("flat round dense")
            ui.label(f"{'Edit' if editing else 'New'} {spec.title.lower()}" + (f": {key}" if editing else "")).classes("text-2xl")
            if copy and not editing:
                ui.label(f"copy of {copy}").classes("mut")
        status = ui.label().classes("text-sm text-warning")
        if kind_key == "device":
            ui.label("Changes to connection settings take effect when the device is reconnected (offered after you commit)."
                     ).classes("mut text-sm")

    def touch() -> None:
        dirty["v"] = True
        status.set_text("Unsaved changes")

    with body:
        render_children(node, data, lookup, touch, locked={"name"} if editing else set())

    # ---- helpers
    def check() -> str | None:
        errs = validate(node, data, lookup)
        k = data.get("name")
        if not editing and k and str(k) in names[kind_key]:
            errs.append(f"{kind_key}/name: '{k}' already exists")
        if errs:
            with ui.dialog() as d, ui.card():
                ui.label("Fix these before saving").classes("text-lg")
                for e in errs[:30]:
                    ui.label("• " + e).classes("text-sm err-tx")
                ui.button("OK", on_click=d.close).props("no-caps").classes(BTN)
            d.open()
            return None
        return str(data["name"])

    def new_obj() -> dict:
        return service_to_json(node, data)[f"{node.module}:{node.name}"][0]

    async def write(k: str) -> bool:
        try:
            obj = new_obj()
            if editing and kind_key == "device":
                await client.device_update(k, original, obj)  # setting by setting: the mounted config must survive
            else:
                await client.inventory_put(kind_key, k, obj)
        except RestconfError as e:
            ui.notify(f"The controller rejected the change: {e}", type="negative", multi_line=True, close_button=True, timeout=0)
            return False
        return True

    async def revert(k: str) -> None:
        if editing and kind_key == "device":
            await client.device_update(k, new_obj(), original)
        elif editing:
            await client.inventory_put(kind_key, k, original)
        else:
            await client.inventory_delete(kind_key, k)

    async def save() -> None:
        k = check()
        if k is not None and await write(k):
            dirty["v"] = False
            status.set_text("Saved to the candidate (not committed)")
            ui.notify(f"Saved {spec.title.lower()} '{k}' to the candidate. Not committed yet.", type="positive")

    async def with_pending(k: str, action):
        """Run `action` with this form's edit temporarily in the candidate (if there is one), then take it out again."""
        pending = dirty["v"] or not editing
        if pending and not await write(k):
            return None
        try:
            return await action()
        finally:
            if pending:
                await revert(k)

    async def review() -> None:
        k = check()
        if k is None:
            return
        try:
            text = await with_pending(k, client.diff_datastores)
        except RestconfError as e:
            ui.notify(str(e), type="negative", multi_line=True, close_button=True)
            return
        with ui.dialog() as d, ui.card().classes("w-[800px] max-w-full gap-2"):
            ui.label("Pending changes in the candidate").classes("text-lg")
            ui.label("This is everything that differs from the running configuration, not only this form. "
                     + ("Your edits were applied temporarily to show it, then taken out again." if dirty["v"] or not editing else "")
                     ).classes("mut text-sm")
            with ui.scroll_area().classes("w-full").style("max-height:55vh"):
                diffview.render(text or "")
            ui.button("Close", on_click=d.close).props("flat no-caps no-wrap").classes(BTN)
        d.open()

    async def commit() -> None:
        ctx = ui.context.client
        k = check()
        if k is None:
            return
        try:
            text = await with_pending(k, client.diff_datastores)
        except RestconfError as e:
            ui.notify(str(e), type="negative", multi_line=True, close_button=True)
            return
        if not await _confirm_commit(f"Commit {spec.title.lower()} '{k}'?",
                                     "A commit applies the whole candidate (all uncommitted changes, not only this form). "
                                     "Nothing is pushed to the devices. This is what will change:", text or ""):
            return
        if (dirty["v"] or not editing) and not await write(k):
            return
        if await _local_commit_dialog(f"Committing {spec.title.lower()} {k}",
                                      _connect_buttons(k, editing) if kind_key == "device" else None):
            dirty["v"] = False
            views.navigate_to(spec.list_route, ctx)

    async def delete_here() -> None:
        ctx = ui.context.client
        if await delete_flow(kind_key, key):
            views.navigate_to(spec.list_route, ctx)

    def show_json() -> None:
        with ui.dialog() as d, ui.card().classes("w-[700px]"):
            ui.code(json.dumps(service_to_json(node, data), indent=2), language="json").classes("w-full")
            ui.button("Close", on_click=d.close).props("no-caps").classes(BTN)
        d.open()

    with footer:
        ui.button("Save", icon="save", on_click=save).props("no-caps no-wrap").classes(BTN_BAR).tooltip(
            "Save to the candidate. Nothing takes effect until you commit.")
        ui.button("Review", icon="preview", on_click=review).props("no-caps no-wrap outline").classes(BTN_BAR).tooltip(
            "Show what would change compared to the running configuration, without saving")
        ui.button("Commit", icon="rocket_launch", color="negative", on_click=commit).props("no-caps no-wrap").classes(BTN_BAR).tooltip(
            "Save and commit the candidate. You are asked to confirm first.")
        if editing:
            ui.button("Delete", icon="delete", on_click=delete_here).props("outline no-caps no-wrap").classes(BTN_BAR)
            ui.button("Duplicate", icon="content_copy", on_click=lambda: ui.navigate.to(form_url(kind_key, copy=key))
                      ).props("outline no-caps no-wrap").classes(BTN_BAR)
        ui.button("Show JSON", icon="data_object", on_click=show_json).props("outline no-caps no-wrap").classes(BTN_BAR)
