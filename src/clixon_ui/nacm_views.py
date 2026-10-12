"""Access control (NACM, ietf-netconf-acm) of the controller: one YANG-generated form for the whole `nacm` container.

The controller's own candidate is edited; the commit happens in the pending-changes panel (a local commit, nothing is
pushed to the devices). PUT replaces the whole container, so the form always shows (and writes back) everything. A
wrong rule can lock users out, so the panel warns when access-control changes are pending."""

from __future__ import annotations

import json

from nicegui import ui

from . import pending, views
from .client import RestconfError
from .formdata import Lookup, entry_from_json, entry_to_json, validate
from .forms import render_children
from .service_views import get_schema
from .style import BTN, BTN_BAR
from .tables import page_column


def _body(node, data: dict) -> dict:
    return entry_to_json(node, data)


async def nacm_page() -> None:
    client = views.client
    try:
        node = (await get_schema()).nacm()
        original = await client.nacm()
    except Exception as e:  # noqa: BLE001 - show anything to the user
        ui.label(f"Could not load the schema or the NACM configuration: {e}").classes("err-box")
        return
    if node is None:
        ui.label("The controller does not have the ietf-netconf-acm module.").classes("err-box")
        return
    data = entry_from_json(node, original or {})
    lookup = Lookup({})

    with page_column():
        head = ui.column().classes("w-full gap-1 shrink-0")
        body = ui.column().classes("w-full gap-3 grow overflow-auto pr-2").style("min-height:0")
        footer = ui.row().classes("w-full items-center gap-3 shrink-0 py-3 border-t line")
    with head:
        ui.label("Access control").classes("text-2xl")
        ui.label("NETCONF access control model (NACM): who may read, write and execute what on the controller. "
                 "Rules are evaluated in order.").classes("mut")
        status = ui.label().classes("text-sm text-warning")
    dirty = {"v": False}

    def touch() -> None:
        dirty["v"] = True
        status.set_text("Unsaved changes")

    with body:
        render_children(node, data, lookup, touch)

    def check() -> bool:
        errs = validate(node, data, lookup)
        if errs:
            with ui.dialog() as d, ui.card():
                ui.label("Fix these before saving").classes("text-lg")
                for e in errs[:30]:
                    ui.label("• " + e).classes("text-sm err-tx")
                ui.button("OK", on_click=d.close).props("no-caps no-wrap").classes(BTN)
            d.open()
        return not errs

    async def write() -> bool:
        try:
            obj = _body(node, data)
            if obj:
                await client.put_nacm(obj)
            elif original is not None:
                await client.delete_nacm()
        except RestconfError as e:
            ui.notify(f"The controller rejected the change: {e}", type="negative", multi_line=True, close_button=True, timeout=0)
            return False
        return True

    async def revert() -> None:
        if original is None:
            await client.delete_nacm()
        else:
            await client.put_nacm(original)

    async def save() -> None:
        if check() and await write():
            dirty["v"] = False
            status.set_text("Saved to the candidate (not committed)")
            ui.notify("Saved access control to the candidate. Not committed yet.", type="positive")

    async def save_review() -> None:
        await save()
        if not dirty["v"]:
            await pending.open_panel()

    def show_json() -> None:
        with ui.dialog() as d, ui.card().classes("w-[700px]"):
            ui.code(json.dumps({"ietf-netconf-acm:nacm": _body(node, data)}, indent=2), language="json").classes("w-full")
            ui.button("Close", on_click=d.close).props("no-caps").classes(BTN)
        d.open()

    with footer:
        ui.button("Save", icon="save", on_click=save).props("no-caps no-wrap").classes(BTN_BAR + " wr").tooltip(
            "Save to the candidate. Nothing takes effect until you commit.")
        ui.button("Save & review", icon="rocket_launch", on_click=save_review).props("no-caps no-wrap outline").classes(BTN_BAR + " wr").tooltip(
            "Save to the candidate, then review all pending changes and commit them.")
        ui.button("Show JSON", icon="data_object", on_click=show_json).props("outline no-caps no-wrap").classes(BTN_BAR)
