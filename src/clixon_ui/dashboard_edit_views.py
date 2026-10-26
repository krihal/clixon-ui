"""Customize the dashboard (`/dashboard/layout`, admins only): which widgets show, in what order, how wide and how tall,
and which stay pinned at the bottom. One layout for everyone; a preview shows the grid as it is being set up."""

from __future__ import annotations

import copy

from nicegui import ui

from . import auth_views, dashboard_layout, db
from . import dashboard_layout as dl
from .style import BTN

PREVIEW_ROW = {"stat": "2rem", "strip": "1.6rem", "label": "1.5rem"}
PREVIEW_PANEL = {"s": "2.6rem", "m": "3.6rem", "l": "5rem"}  # scaled-down panel heights


async def layout_page():
    access = auth_views.current_access()
    if not access or not access.is_admin:
        ui.label("Only admins can customize the dashboard.").classes("text-xl")
        return
    with db.session() as d:
        work: list[dict] = copy.deepcopy(dl.load(d)["widgets"])

    with ui.row().classes("w-full items-center gap-3"):
        ui.button(icon="arrow_back", on_click=lambda: ui.navigate.to("/")).props("flat round dense").tooltip("Dashboard")
        ui.label("Customize the dashboard").classes("text-2xl grow whitespace-nowrap")
        ui.button("Reset to default", icon="restart_alt", on_click=lambda: reset()).props("outline no-caps no-wrap").classes(BTN)
        ui.button("Cancel", on_click=lambda: ui.navigate.to("/")).props("outline no-caps no-wrap").classes(BTN)
        ui.button("Save", icon="save", on_click=lambda: save()).props("no-caps no-wrap").classes(BTN)
    ui.label("Everyone sees the same dashboard. Choose what it shows and how big each part is; on a phone every part is full width.").classes("mut")

    ui.label("Preview").classes("text-lg mt-2")
    preview = ui.column().classes("w-full gap-2 p-3 rounded-lg border line").style("background:var(--panel)")
    with ui.row().classes("w-full items-center gap-3 mt-2"):
        ui.label("Widgets and labels").classes("text-lg grow")
        ui.button("Add label", icon="label", on_click=lambda: add_label()).props("outline no-caps no-wrap").classes(BTN)
    rows = ui.column().classes("w-full gap-2")

    def title_of(e: dict) -> str:
        return (e["text"].strip()[:40] or "(empty label)") if dl.is_label(e["id"]) else dl.BY_ID[e["id"]].title

    def box(e: dict) -> None:
        kind = "label" if dl.is_label(e["id"]) else dl.BY_ID[e["id"]].kind
        height = PREVIEW_PANEL[e["height"]] if kind == "panel" else PREVIEW_ROW[kind]
        with ui.element("div").classes("min-w-0 overflow-hidden").style(
                f"grid-column:span {dl.SIZES[e['size']][0]};height:{height};border-radius:6px;border:1px solid var(--line);padding:2px 6px;"
                "font-size:11px;background:color-mix(in srgb,var(--acc) 14%,var(--panel))"):
            ui.label(title_of(e)).classes("ellipsis" + (" italic" if kind == "label" else ""))

    def draw_preview() -> None:
        preview.clear()
        main = [e for e in work if e["visible"] and not e["pin"]]
        pinned = [e for e in work if e["visible"] and e["pin"]]
        with preview:
            if not main and not pinned:
                ui.label("Nothing is shown.").classes("mut text-sm")
            with ui.element("div").classes("w-full grid gap-1").style("grid-template-columns:repeat(12,minmax(0,1fr))"):
                for e in main:
                    box(e)
            if pinned:
                ui.label("Pinned at the bottom").classes("mut text-xs mt-1")
                with ui.element("div").classes("w-full grid gap-1").style("grid-template-columns:repeat(12,minmax(0,1fr))"):
                    for e in pinned:
                        box(e)

    def changed() -> None:
        draw_preview()

    def move(i: int, d: int) -> None:
        if 0 <= i + d < len(work):
            work[i], work[i + d] = work[i + d], work[i]
            draw_rows()
            draw_preview()

    def draw_rows() -> None:
        rows.clear()
        with rows:
            for i, e in enumerate(work):
                if dl.is_label(e["id"]):
                    label_row(i, e)
                    continue
                w = dl.BY_ID[e["id"]]
                with ui.card().classes("w-full p-3 gap-2"):
                    with ui.row().classes("w-full items-center gap-3"):
                        ui.switch(value=e["visible"], on_change=lambda ev, e=e: (e.__setitem__("visible", ev.value), changed())).tooltip("Show on the dashboard")
                        ui.icon(w.icon).classes("text-primary")
                        with ui.column().classes("gap-0 grow min-w-0").style("flex-basis:14rem"):
                            ui.label(w.title).classes("font-medium")
                            ui.label(w.description).classes("mut text-xs")
                        ui.select({k: v[1] for k, v in dl.SIZES.items()}, label="Width", value=e["size"],
                                  on_change=lambda ev, e=e: (e.__setitem__("size", ev.value), changed())).props("outlined dense").classes("w-48")
                        if w.kind == "panel":
                            ui.select({k: v[1] for k, v in dl.HEIGHTS.items()}, label="Height", value=e["height"],
                                      on_change=lambda ev, e=e: (e.__setitem__("height", ev.value), changed())).props("outlined dense").classes("w-32")
                        ui.checkbox("Pin to bottom", value=e["pin"], on_change=lambda ev, e=e: (e.__setitem__("pin", ev.value), changed())
                                    ).tooltip("Stays visible below the scrolling part of the dashboard")
                        ui.button(icon="arrow_upward", on_click=lambda i=i: move(i, -1)).props("flat round dense").tooltip("Move up")
                        ui.button(icon="arrow_downward", on_click=lambda i=i: move(i, 1)).props("flat round dense").tooltip("Move down")

    def label_row(i: int, e: dict) -> None:
        with ui.card().classes("w-full p-3 gap-2"):
            with ui.row().classes("w-full items-center gap-3"):
                ui.switch(value=e["visible"], on_change=lambda ev, e=e: (e.__setitem__("visible", ev.value), changed())).tooltip("Show on the dashboard")
                ui.icon("label").classes("text-primary")
                ui.textarea("Label text", value=e["text"], on_change=lambda ev, e=e: (e.__setitem__("text", ev.value or ""), changed())
                            ).props(f"outlined dense autogrow maxlength={dl.MAX_LABEL_TEXT}").classes("grow min-w-0").style("flex-basis:14rem")
                ui.select(dl.LABEL_STYLES, label="Style", value=e["style"], on_change=lambda ev, e=e: (e.__setitem__("style", ev.value), changed())
                          ).props("outlined dense").classes("w-40")
                ui.select({k: v[1] for k, v in dl.SIZES.items()}, label="Width", value=e["size"],
                          on_change=lambda ev, e=e: (e.__setitem__("size", ev.value), changed())).props("outlined dense").classes("w-48")
                ui.checkbox("Pin to bottom", value=e["pin"], on_change=lambda ev, e=e: (e.__setitem__("pin", ev.value), changed()))
                ui.button(icon="arrow_upward", on_click=lambda i=i: move(i, -1)).props("flat round dense").tooltip("Move up")
                ui.button(icon="arrow_downward", on_click=lambda i=i: move(i, 1)).props("flat round dense").tooltip("Move down")
                ui.button(icon="delete", on_click=lambda e=e: remove(e)).props("flat round dense").classes("act-del").tooltip("Delete this label")

    def remove(e: dict) -> None:
        work.remove(e)
        draw_rows()
        draw_preview()

    def add_label() -> None:
        try:
            work.append(dl.new_label({"widgets": work}))
        except ValueError as err:
            ui.notify(str(err), type="warning")
            return
        draw_rows()
        draw_preview()

    def reset() -> None:
        work[:] = copy.deepcopy(dl.default_layout()["widgets"])
        draw_rows()
        draw_preview()
        ui.notify("Back to the default layout (labels removed). Press Save to keep it.", type="info")

    def save() -> None:
        with db.session() as d:
            dashboard_layout.save(d, {"widgets": work}, access.username)
        ui.notify("Dashboard saved.", type="positive")
        ui.navigate.to("/")

    draw_rows()
    draw_preview()
