"""Device configuration viewer: outline tree on the left, Junos-style text on the right."""

from __future__ import annotations

from html import escape
from urllib.parse import quote, unquote

from nicegui import ui

from . import views
from .client import RestconfError
from .confview import MAX_LINES, outline, to_lines
from .style import BTN
from .tables import page_column


def _lines_html(lines: list[str], needle: str) -> str:
    out = []
    n = needle.lower()
    for i, ln in enumerate(lines, 1):
        if n and n not in ln.lower():
            continue
        txt = escape(ln)
        if n:
            j = ln.lower().find(n)
            txt = escape(ln[:j]) + "<mark>" + escape(ln[j:j + len(n)]) + "</mark>" + escape(ln[j + len(n):])
        out.append(f'<div class="cl"><span class="ln">{i}</span>{txt}</div>')
    return "".join(out) or '<div class="cl mut">No matching lines</div>'


async def device_config_page(name: str):
    name = unquote(name)
    client = views.client
    state = next((d.get("conn-state", "") for d in await client.devices() if d["name"] == name), "")

    with page_column():
        with ui.row().classes("w-full items-center gap-3"):
            ui.button(icon="arrow_back", on_click=lambda: ui.navigate.to("/devices")).props("flat round dense")
            ui.label(name).classes("text-2xl")
            ui.html(f'<span class="pill pill-{state if state in ("OPEN", "CLOSED", "DISABLED") else "other"}">{escape(state or "?")}</span>')
            ui.label("Configuration as held by the controller").classes("mut")
            ui.space()
            ui.button("Show diff", icon="difference", on_click=lambda: ui.navigate.to(f"/commit?device={quote(name)}")).props(
                "outline no-caps no-wrap").classes(BTN)
            ui.button("Reload", icon="refresh", on_click=lambda: views.reload_page()).props("outline no-caps no-wrap").classes(BTN)

        try:
            cfg = await client.device_outline(name)
        except RestconfError as e:
            with ui.card().classes("w-full items-center gap-2 p-8"):
                ui.icon("power_off", size="lg").classes("mut")
                ui.label("Cannot read this device's configuration").classes("text-lg")
                ui.label(str(e)).classes("mut")
                if state != "OPEN":
                    async def open_it() -> None:
                        await views.run_tx(client.connection_change(name, "OPEN"), f"Open {name}")
                        views.reload_page()
                    ui.button("Open device", icon="power", on_click=open_it).props("no-caps no-wrap").classes(BTN + " wr")
            return
        if not cfg:
            ui.label("The controller holds no configuration for this device yet. Pull it from the Devices page.").classes("mut")
            return
        # Junos has one root container; other models (OpenConfig) have several top-level containers and no root
        root = next(iter(cfg)) if len(cfg) == 1 else ""
        nodes = outline(cfg[root] if root else cfg)
        shown = {"lines": [], "path": ""}

        with ui.row().classes("w-full no-wrap items-stretch gap-4 grow").style("min-height:0"):
            with ui.card().classes("w-80 shrink-0 p-3 gap-2").style("min-height:0"):
                ts = ui.input(placeholder="Filter sections…").props("outlined dense clearable").classes("w-full")
                with ts.add_slot("prepend"):
                    ui.icon("search")
                with ui.element("div").classes("w-full grow overflow-auto").style("min-height:0"):
                    tree = ui.tree(nodes, node_key="id", label_key="label", on_select=lambda e: select(e.value)).props("dense no-connectors")
                ts.on_value_change(lambda e: (tree.props(f'filter="{(e.value or "").replace(chr(34), "")}"'), tree.update()))
            with ui.card().classes("grow min-w-0 p-3 gap-2").style("min-height:0"):
                with ui.row().classes("w-full items-center gap-2"):
                    crumb = ui.label("Select a section on the left").classes("font-semibold")
                    meta = ui.label().classes("mut text-sm")
                    ui.space()
                    find = ui.input(placeholder="Find in this section…").props("outlined dense clearable").classes("w-64")
                    with find.add_slot("prepend"):
                        ui.icon("manage_search")
                    ui.button(icon="content_copy", on_click=lambda: (ui.clipboard.write("\n".join(shown["lines"])), ui.notify("Copied"))
                              ).props("flat round dense").tooltip("Copy section")
                body = ui.html("").classes("confbody w-full grow").style("height:auto;min-height:0")
                notice = ui.label().classes("warn-tx text-sm")


    def render() -> None:
        body.set_content(_lines_html(shown["lines"], (find.value or "").strip()))

    find.on_value_change(lambda e: render())

    async def select(node_id: str | None) -> None:
        if not node_id:
            return
        shown["path"] = node_id
        crumb.set_text(" › ".join(unquote(p.partition("=")[0] if "=" not in p else p).rpartition(":")[2] for p in node_id.split("/")))
        meta.set_text("loading…")
        notice.set_text("")
        try:
            obj = await client.device_node(name, f"{root}/{node_id}" if root else node_id)
        except RestconfError as e:
            shown["lines"] = []
            body.set_content("")
            meta.set_text("")
            notice.set_text("This node is a list: expand it and pick one of its entries." if "malformed key" in str(e)
                            else f"Could not read: {e}")
            return
        if obj is None:
            shown["lines"] = []
            body.set_content("")
            meta.set_text("")
            notice.set_text("This section is very large. Expand it in the tree and pick a single entry.")
            return
        lines, trunc = to_lines(obj)
        shown["lines"] = lines
        meta.set_text(f"{len(lines):,} lines")
        notice.set_text(f"Showing the first {MAX_LINES:,} lines only. Pick a smaller entry in the tree to see the rest." if trunc else "")
        render()
