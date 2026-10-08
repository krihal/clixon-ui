"""Network page: discover neighbours on demand and show them as an interactive map and a table."""

from __future__ import annotations

import time
from html import escape
from urllib.parse import quote

from nicegui import ui

from . import views
from .client import RestconfError
from .network import DEFAULT_SOURCE, SOURCES, Graph, build_graph, filter_graph, group_links, radial_positions
from .rpc_views import run_on_targets
from .style import BTN
from .tables import RAIL_LINK, data_table, page_column

# Last discovery result, kept in server memory so leaving and returning to the page does not lose it.
# Discovery itself only ever runs when the user presses the button.
_LAST: dict = {}

MANAGED_COLOR, EXTERNAL_COLOR = "#3d6fff", "#8a97a8"
LINK_COLOR = {"confirmed": "#7a8aa0", "one side": "#d49a1a", "external": "#9aa6b5"}
# evaluated in the browser, so the labels follow the light/dark theme
THEME_TEXT = "getComputedStyle(document.documentElement).getPropertyValue('--tx').trim()"


def graph_option(g: Graph, layout: str, port_labels: bool) -> dict:
    """ECharts 'graph' series for a Graph. Parallel links are drawn as one line labelled with their count.

    Every node and edge carries a `value`: NiceGUI's click handler reads it and fails without."""
    pos = radial_positions(g) if layout == "radial" else {}
    nodes = []
    for n in g.nodes.values():
        links = [l for l in g.links if n.name in (l.a, l.b)]
        tip = f"<b>{escape(n.name)}</b><br>{'managed device' if n.managed else 'external neighbour'} · {len(links)} link{'s' if len(links) != 1 else ''}"
        if n.chassis:
            tip += "<br>chassis " + escape(", ".join(sorted(n.chassis)))
        node = {"name": n.name, "value": n.name, "managed": n.managed, "symbolSize": 58 if n.managed else 34, "tip": tip,
                "itemStyle": {"color": MANAGED_COLOR if n.managed else EXTERNAL_COLOR, "borderColor": "#ffffff", "borderWidth": 2},
                "label": {"fontWeight": "bold" if n.managed else "normal", "fontSize": 14 if n.managed else 12}}
        if n.name in pos:
            node["x"], node["y"] = pos[n.name][0] * 300, pos[n.name][1] * 300
        nodes.append(node)
    edges = []
    for grp in group_links(g):
        k = len(grp.links)
        first = grp.links[0]
        lines = "<br>".join(f"{escape(l.a)} <b>{escape(l.a_port)}</b> ↔ {escape(l.b)} <b>{escape(l.b_port or '?')}</b>"
                            + (f" · {escape(l.descr)}" if l.descr else "") for l in grp.links)
        text = f"{first.a_port} – {first.b_port or '?'}" if k == 1 else f"×{k}"
        edges.append({"source": grp.a, "target": grp.b, "value": k, "status": grp.status, "tip": f"{lines}<br><i>{grp.status}</i>",
                      "lineStyle": {"color": LINK_COLOR[grp.status], "width": 2 + min(k - 1, 4) * 1.5,
                                    "type": "dashed" if grp.status == "one side" else "solid", "curveness": 0},
                      "label": {"show": port_labels or k > 1, "formatter": text, "fontSize": 11, "fontWeight": "bold" if k > 1 else "normal"}})
    series = {
        "type": "graph", "layout": "none" if layout == "radial" else layout, "roam": True, "draggable": True,
        "data": nodes, "links": edges, "edgeSymbol": ["none", "none"], "lineStyle": {"opacity": 0.95},
        "label": {"show": True, "position": "bottom", "distance": 6, ":color": THEME_TEXT},
        "edgeLabel": {":color": THEME_TEXT},
        "labelLayout": {"hideOverlap": True},
        "emphasis": {"focus": "adjacency", "lineStyle": {"width": 5}},
        "force": {"repulsion": 620, "edgeLength": [100, 190], "gravity": 0.11, "friction": 0.25},
        "circular": {"rotateLabel": False},
    }
    return {"animation": False, "tooltip": {":formatter": "function(p){return p.data && p.data.tip ? p.data.tip : p.name}"}, "series": [series]}


@ui.page("/network", response_timeout=30)
async def network_page():
    views.frame("/network")
    client = views.client
    try:
        devices = await client.devices()
    except RestconfError as e:
        ui.label(f"Could not read devices: {e}").classes("err-box")
        return
    all_names = [d["name"] for d in devices]
    open_names = [d["name"] for d in devices if d.get("conn-state") == "OPEN"]
    last = _LAST.get(client.url)  # {"graph", "when", "n", "errors", "source"}

    with page_column():
        with ui.row().classes("w-full items-baseline gap-3"):
            ui.label("Network").classes("text-2xl")
            ui.label("Neighbours your devices see. Nothing is queried until you press Discover.").classes("mut")

        with ui.card().classes("w-full p-3 gap-2"):
            with ui.row().classes("w-full items-start no-wrap gap-3"):
                source = ui.select({k: s.label for k, s in SOURCES.items()}, value=DEFAULT_SOURCE, label="Discovery method"
                                   ).props("outlined dense").classes("w-64")
                if len(SOURCES) < 2:
                    source.props("readonly")
                targets = ui.select(all_names, label="Devices", multiple=True, with_input=True,
                                    value=[n for n in (last["devs"] if last else open_names) if n in all_names]
                                    ).props("use-chips outlined dense").classes("grow min-w-64")
                ui.button("Discover", icon="travel_explore", on_click=lambda: discover()).props("no-caps no-wrap").classes(BTN)
            with ui.row().classes("w-full items-center gap-4"):
                status = ui.label().classes("mut text-sm")
                ui.space()
                layout = ui.select({"radial": "Radial layout", "force": "Force layout", "circular": "Circle layout"},
                                   value="radial").props("outlined dense").classes("w-44")
                show_ext = ui.switch("External", value=True).tooltip("Switches, customers and other neighbours that are not managed devices")
                show_mgmt = ui.switch("Management ports", value=False).tooltip("Out-of-band ports such as re0:mgmt-0 and fxp0")
                show_loops = ui.switch("Loops", value=False).tooltip("Links from a device back to itself")
                port_labels = ui.switch("Port labels", value=False)
                view = ui.toggle({"map": "Map", "links": "Links"}, value="map").props(
                    "no-caps no-wrap dense unelevated toggle-color=primary color=transparent text-color=dark")
            problems = ui.label().classes("warn-tx text-sm whitespace-pre-wrap")

        empty = ui.card().classes("w-full grow items-center justify-center gap-1").style("min-height:0")
        with empty:
            ui.icon("lan", size="lg").classes("mut")
            ui.label("No network map yet").classes("font-medium")
            ui.label("Choose the devices to ask and press Discover.").classes("mut text-sm")

        map_box = ui.row().classes("w-full no-wrap items-stretch gap-3 grow").style("min-height:0")
        with map_box:
            with ui.card().classes("grow min-w-0 p-0").style("min-height:0"):
                chart = ui.echart(graph_option(Graph(), "force", False), on_point_click=lambda e: node_clicked(e)
                                  ).classes("w-full h-full").style("min-height:0")
            side = ui.card().classes("w-80 shrink-0 p-3 gap-2 overflow-auto").style("min-height:0")
        links_box = ui.element("div").classes("w-full grow flex flex-col").style("min-height:0")
        rows: list[dict] = []
        with links_box:
            table = data_table(
                [{"name": "device", "label": "Device", "field": "device", "align": "left", "sortable": True, "classes": "name"},
                 {"name": "port", "label": "Port", "field": "port", "align": "left", "classes": "mono"},
                 {"name": "neighbour", "label": "Neighbour", "field": "neighbour", "align": "left", "sortable": True, "classes": "name"},
                 {"name": "nport", "label": "Neighbour port", "field": "nport", "align": "left", "classes": "mono"},
                 {"name": "descr", "label": "Description", "field": "descr", "align": "left"},
                 {"name": "status", "label": "Status", "field": "status", "align": "left"}],
                rows, "key", RAIL_LINK)
            table.on("rowClick", lambda e: select_node(e.args[1]["device"]))

    def show_empty_state(has_graph: bool) -> None:
        empty.set_visibility(not has_graph)
        map_box.set_visibility(has_graph and view.value == "map")
        links_box.set_visibility(has_graph and view.value == "links")

    def select_node(name: str) -> None:
        full: Graph | None = _LAST.get(client.url, {}).get("graph")
        g = filter_graph(full, show_ext.value, show_mgmt.value, show_loops.value) if full else None  # same view as the map
        side.clear()
        with side:
            if not g or name not in g.nodes:
                ui.label("Click a node to see its links.").classes("mut text-sm")
                return
            n = g.nodes[name]
            ui.label(n.name).classes("text-lg")
            ui.label("Managed device" if n.managed else "External neighbour").classes("mut text-sm")
            if n.chassis:
                ui.label("Chassis " + ", ".join(sorted(n.chassis))).classes("mut text-xs mono")
            mine = [l for l in g.links if name in (l.a, l.b)]
            ui.label(f"{len(mine)} link{'s' if len(mine) != 1 else ''}").classes("font-medium mt-2")
            for l in mine:
                here, there = (l.a_port, l.b_port or "?") if l.a == name else (l.b_port or "?", l.a_port)
                other = l.b if l.a == name else l.a
                with ui.column().classes("gap-0 border-b line pb-2 w-full"):
                    ui.label(f"{here}  →  {other} {there}").classes("text-sm mono")
                    if l.descr:
                        ui.label(l.descr).classes("text-xs mut")
                    ui.label(l.status + (f" · {l.parent}" if l.parent else "")).classes("text-xs mut")
            if n.managed:
                ui.button("Open configuration", icon="description", on_click=lambda: ui.navigate.to(f"/devices/{quote(name)}")
                          ).props("outline no-caps no-wrap").classes(BTN)

    def node_clicked(e) -> None:
        if getattr(e, "data_type", None) == "edge":
            return
        select_node(e.name)

    def render(info: dict | None) -> None:
        if not info:
            show_empty_state(False)
            status.set_text("Not discovered yet")
            return
        g: Graph = info["graph"]
        shown = filter_graph(g, show_ext.value, show_mgmt.value, show_loops.value)
        chart.options.clear()
        chart.options.update(graph_option(shown, layout.value, port_labels.value))
        chart.update()
        rows[:] = [{"key": f"{l.a}|{l.a_port}|{l.b}", "device": l.a, "port": l.a_port, "neighbour": l.b, "nport": l.b_port or "",
                    "descr": l.descr, "status": l.status} for l in sorted(g.links, key=lambda x: (x.a, x.a_port))]
        table.rows = rows
        table.update()
        ext = sum(1 for n in g.nodes.values() if not n.managed)
        status.set_text(f"Discovered {time.strftime('%H:%M:%S', time.localtime(info['when']))} · {info['n']} device"
                        f"{'s' if info['n'] != 1 else ''} asked · {len(g.links)} links · {ext} external neighbour{'s' if ext != 1 else ''}")
        problems.set_text("" if not info["errors"] else
                          f"{len(info['errors'])} device(s) did not answer:\n" +
                          "\n".join(f"• {d}: {why[:140]}" for d, why in sorted(info["errors"].items())))
        show_empty_state(True)
        select_node("")

    async def discover() -> None:
        devs = list(targets.value)
        if not devs:
            ui.notify("Choose at least one device", type="warning")
            return
        src = SOURCES[source.value]
        closed = [d for d in devs if d not in open_names]
        n = ui.notification(f"{src.label}: asking {len(devs)} device{'s' if len(devs) != 1 else ''}…", spinner=True, timeout=None)
        try:
            done = await run_on_targets(client, {"inline": src.rpc, "label": src.label}, devs, [])
        finally:
            n.dismiss()
        adjs, errors = [], {}
        for label, res in done:
            if "error" in res:
                errors[label] = res["error"].strip().splitlines()[0] if res["error"].strip() else "failed"
                continue
            for dev, data in (res["replies"] or {}).items():
                adjs += src.parse(dev, data)
        graph = build_graph(adjs, all_names)
        # managed devices nobody reported on and that were not asked would only clutter the map
        asked = set(devs)
        for name in [m for m, node in graph.nodes.items() if node.managed and m not in asked
                     and not any(m in (l.a, l.b) for l in graph.links)]:
            del graph.nodes[name]
        info = {"graph": graph, "when": time.time(), "n": len(devs), "errors": errors, "devs": devs, "source": source.value}
        _LAST[client.url] = info
        if closed:
            ui.notify(f"Not connected: {', '.join(closed)}", type="warning")
        render(info)

    for control in (layout, port_labels, show_ext, show_mgmt, show_loops):
        control.on_value_change(lambda e: last_info() and render(last_info()))
    view.on_value_change(lambda e: show_empty_state(bool(last_info())))

    def last_info() -> dict | None:
        return _LAST.get(client.url)

    render(last)
    if not last:
        show_empty_state(False)
    select_node("")
