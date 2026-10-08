"""RPC page: run operational commands (RPC templates or custom RPCs) on devices and show the replies."""

from __future__ import annotations

import asyncio
import json
from html import escape

from nicegui import app, ui

from . import views
from .client import RestconfError
from .confview import to_lines
from .rpcutil import cli_request, is_read_only, rpc_name, substitute, template_vars
from .rpcschema import RpcIndex
from .style import BTN
from .tables import RAIL_RPC, data_table

CUSTOM_EXAMPLE = '''{
  "get-interface-information": {
    "terse": {}
  }
}'''


def _text_html(lines: list[str]) -> str:
    return "".join(f'<div class="cl"><span class="ln">{i}</span>{escape(ln)}</div>' for i, ln in enumerate(lines, 1))


async def run_on_targets(client, req: dict, devs: list[str], grps: list[str]) -> list[tuple[str, dict]]:
    """Run one RPC (template or inline) on devices/groups, at most 4 at a time; returns (label, result) pairs."""
    jobs = [(d, {"device": d}) for d in devs] + [(f"group {g}", {"group": g}) for g in grps]
    gate = asyncio.Semaphore(4)  # the controller's web server answers 502 under heavy parallelism

    async def job(label: str, tgt: dict) -> tuple[str, dict]:
        async with gate:
            try:
                tid = await client.run_rpc(template=req.get("template"), inline=req.get("inline"),
                                           variables=req.get("variables"), **tgt)
                tr = await client.wait_transaction(tid, timeout=120, interval=0.5)
                if not tr or tr.get("result") != "SUCCESS":
                    return label, {"error": (tr or {}).get("reason", "failed").strip() or "failed", "tid": tid}
                return label, {"tid": tid, "replies": await client.rpc_result(tid), "tr": tr}
            except (RestconfError, TimeoutError) as e:
                return label, {"error": str(e)}

    return list(await asyncio.gather(*(job(lbl, t) for lbl, t in jobs)))


def _show_results(done: list[tuple[str, dict]]) -> None:
    for label, res in done:
        if "error" in res:
            _show_card(label, None, res["error"])
            continue
        for dev, data in (res["replies"] or {}).items() or [(label, None)]:
            _show_card(dev, data, None)


async def _confirm_run(req: dict, devs: list[str], grps: list[str]) -> bool:
    """Confirm dialog for RPCs that are not read-only. Returns True to go ahead."""
    with ui.dialog() as d, ui.card().classes("w-[520px] gap-2"):
        ui.label(f"Run {req['label']}?").classes("text-lg")
        ui.label("This is not a read-only command and can change the device state.").classes("warn-tx")
        ui.code(json.dumps(req["body"], indent=2), language="json").classes("w-full")
        ui.label("Targets: " + ", ".join(devs + [f"group {g}" for g in grps])).classes("text-sm")
        with ui.row().classes("justify-end w-full"):
            ui.button("Cancel", on_click=d.close).props("flat no-caps no-wrap").classes(BTN)
            ui.button("Run", color="negative", on_click=lambda: d.submit(True)).props("no-caps no-wrap").classes(BTN)
    return bool(await d)


def _show_card(name: str, data, error: str | None) -> None:
    with ui.expansion(value=True).classes("w-full border border-[#e3e8ee] line rounded-lg").props("dense expand-separator") as ex:
        with ex.add_slot("header"):
            with ui.row().classes("items-center gap-2 w-full"):
                ui.icon("error" if error else "check_circle", color="negative" if error else "positive", size="sm")
                ui.label(name).classes("font-medium")
        if error:
            ui.label(error).classes("err-box whitespace-pre-wrap w-full")
            return
        lines, trunc = to_lines(data if data is not None else {})
        view = ui.toggle({"text": "Text", "json": "JSON"}, value="text").props("dense no-caps unelevated toggle-color=primary color=white text-color=grey-8")
        text = ui.html(_text_html(lines) or '<div class="cl mut">Empty reply</div>').classes("confbody w-full").style("height:auto;max-height:50vh")
        raw = ui.code(json.dumps(data, indent=2), language="json").classes("w-full")
        text.bind_visibility_from(view, "value", lambda v: v == "text")
        raw.bind_visibility_from(view, "value", lambda v: v == "json")
        if trunc:
            ui.label("Output truncated in the text view; see JSON.").classes("warn-tx text-sm")


@ui.page("/rpc", response_timeout=60)
async def rpc_page():
    views.frame("/rpc")
    client = views.client
    try:
        templates = await client.rpc_templates()
        groups = await client.device_groups()
        devices = await client.devices()
    except RestconfError as e:
        ui.label(f"Could not load RPC templates: {e}").classes("err-box")
        return
    by_name = {t["name"]: t for t in templates}
    open_devs = [d["name"] for d in devices if d.get("conn-state") == "OPEN"]
    all_devs = [d["name"] for d in devices]

    with ui.row().classes("items-baseline gap-3"):
        ui.label("RPC").classes("text-2xl")
        ui.label("Run an operational command on devices.").classes("mut")

    with ui.tabs().classes("mb-2") as tabs:
        run_tab = ui.tab("Run an RPC").props("no-caps")
        avail_tab = ui.tab("Available RPCs").props("no-caps")
        cli_tab = ui.tab("CLI").props("no-caps")
    with ui.tab_panels(tabs, value=run_tab).classes("w-full bg-transparent").props("animated=false"):
        with ui.tab_panel(run_tab).classes("p-0 gap-4"):
            # ---- 1. what to run
            with ui.card().classes("w-full p-4 gap-3"):
                kind = ui.toggle({"template": "RPC template", "custom": "Custom RPC"}, value="template").props(
                    "no-caps no-wrap dense unelevated padding=6px\u00a016px toggle-color=primary color=white text-color=grey-8")
                tpl_box = ui.column().classes("w-full gap-3")
                custom_box = ui.column().classes("w-full gap-2")
                tpl_box.bind_visibility_from(kind, "value", lambda v: v == "template")
                custom_box.bind_visibility_from(kind, "value", lambda v: v == "custom")

                with tpl_box:
                    opts = {n: f"{n}   ({'read' if is_read_only(by_name[n].get('config')) else 'ACTION'})" for n in by_name}
                    tpl = ui.select(opts, label="Template", with_input=True, value=None).props("outlined dense").classes("w-full max-w-xl")
                    var_box = ui.row().classes("w-full gap-3")
                    preview = ui.code("", language="json").classes("w-full max-w-xl")
                    warn = ui.label().classes("warn-tx text-sm")
                with custom_box:
                    ui.label("The RPC as JSON: the first element is the RPC name, as for an RPC template.").classes("mut text-sm")
                    custom = ui.textarea(value=CUSTOM_EXAMPLE).props(
                        'outlined input-style="min-height:280px;font-family:var(--mono);font-size:13px;line-height:1.5"').classes("w-full")

                var_inputs: dict[str, ui.input] = {}

                def refresh_template(_=None) -> None:
                    var_box.clear()
                    var_inputs.clear()
                    t = by_name.get(tpl.value)
                    if not t:
                        preview.set_content("")
                        warn.set_text("")
                        return
                    with var_box:
                        for name, descr in template_vars(t):
                            inp = ui.input(name + " *", on_change=refresh_preview).props("outlined dense").classes("w-60")
                            if descr:
                                inp.tooltip(descr)
                            var_inputs[name] = inp
                    refresh_preview()

                def refresh_preview(_=None) -> None:
                    t = by_name.get(tpl.value)
                    if not t:
                        return
                    vals = {k: i.value for k, i in var_inputs.items()}
                    preview.set_content(json.dumps(substitute(t.get("config", {}), vals), indent=2))
                    warn.set_text("" if is_read_only(t.get("config")) else
                                  f"{rpc_name(t.get('config'))} is not a read-only RPC: it can change the device. You will be asked to confirm.")

                tpl.on_value_change(refresh_template)

            # ---- 2. where
            with ui.card().classes("w-full p-4 gap-3"):
                with ui.row().classes("w-full items-start gap-4"):
                    targets = ui.select(all_devs, label="Devices", multiple=True, with_input=True, value=[]).props("use-chips outlined dense").classes("w-96")
                    tgroups = ui.select(groups, label="Device groups", multiple=True, with_input=True, value=[]).props("use-chips outlined dense").classes("w-72")
                    ui.space()
                    run_btn = ui.button("Run", icon="play_arrow", on_click=lambda: start()).props("no-caps no-wrap").classes(BTN)
                closed = ui.label().classes("warn-tx text-sm")

                def check_targets(_=None) -> None:
                    bad = [d for d in targets.value if d not in open_devs]
                    closed.set_text(f"Not connected: {', '.join(bad)}. Open them on the Devices page first." if bad else "")

                targets.on_value_change(check_targets)

            # ---- 3. results
            with ui.card().classes("w-full p-4 gap-3"):
                prog = ui.row().classes("items-center gap-2")
                with prog:
                    ui.spinner(size="sm")
                    stage = ui.label()
                prog.set_visibility(False)
                results = ui.column().classes("w-full gap-2")
                with results:
                    with ui.column().classes("w-full items-center py-8 gap-1"):
                        ui.icon("terminal", size="lg").classes("mut")
                        ui.label("No results yet").classes("font-medium")
                        ui.label("Pick a command and a target, then press Run.").classes("mut text-sm")

            def build_request() -> dict | None:
                """Validated RPC request {template|inline, variables, label, read_only} or None."""
                if kind.value == "template":
                    t = by_name.get(tpl.value)
                    if not t:
                        ui.notify("Choose a template", type="warning")
                        return None
                    vals = {k: (i.value or "").strip() for k, i in var_inputs.items()}
                    missing = [k for k, v in vals.items() if not v]
                    if missing:
                        ui.notify(f"Fill in: {', '.join(missing)}", type="warning")
                        return None
                    return {"template": t["name"], "variables": vals, "label": t["name"], "read_only": is_read_only(t.get("config")),
                            "body": substitute(t.get("config", {}), vals)}
                try:
                    body = json.loads(custom.value)
                    assert isinstance(body, dict) and body
                except (ValueError, AssertionError):
                    ui.notify("The custom RPC must be a JSON object, e.g. {\"get-system-information\": {}}", type="negative")
                    return None
                return {"inline": body, "label": rpc_name(body), "read_only": is_read_only(body), "body": body}

            async def start() -> None:
                req = build_request()
                if req is None:
                    return
                devs, grps = list(targets.value), list(tgroups.value)
                if not devs and not grps:
                    ui.notify("Choose at least one device or group", type="warning")
                    return
                if not req["read_only"] and not await _confirm_run(req, devs, grps):
                    return
                await execute(req, devs, grps)

            async def execute(req: dict, devs: list[str], grps: list[str]) -> None:
                run_btn.set_enabled(False)
                prog.set_visibility(True)
                stage.set_text(f"Running {req['label']}…")
                results.clear()
                done = await run_on_targets(client, req, devs, grps)
                prog.set_visibility(False)
                run_btn.set_enabled(True)
                with results:
                    _show_results(done)


        with ui.tab_panel(avail_tab).classes("p-0 gap-3"):
            index = _index(client)
            only_read = {"v": False}
            rows: list[dict] = []
            with ui.row().classes("w-full items-center gap-3"):
                dev_pick = ui.select(all_devs, label="Device", value=(open_devs or all_devs or [None])[0],
                                     with_input=True).props("outlined dense").classes("w-64")
                find = ui.input(placeholder="Search RPCs by name, area or description…").props("outlined dense clearable").classes("grow min-w-64")
                with find.add_slot("prepend"):
                    ui.icon("search")
                read_sw = ui.switch("Read-only only", on_change=lambda e: (only_read.update(v=e.value), load(False)))
                count = ui.label().classes("mut")
                ui.button("Reload", icon="refresh", on_click=lambda: load(True)).props("outline dense no-caps no-wrap").classes(BTN)
            status = ui.row().classes("items-center gap-2")
            with status:
                ui.spinner(size="sm")
                status_text = ui.label("Loading RPCs from the device's YANG…").classes("mut")
            status.set_visibility(False)
            avail = data_table(
                [{"name": "name", "label": "RPC", "field": "name", "align": "left", "sortable": True, "classes": "name"},
                 {"name": "type", "label": "Type", "field": "type", "align": "left"},
                 {"name": "area", "label": "Area", "field": "area", "align": "left", "sortable": True},
                 {"name": "description", "label": "Description", "field": "description", "align": "left"},
                 {"name": "args", "label": "Arguments", "field": "args", "align": "left", "classes": "mono"},
                 {"name": "use", "label": "", "field": "name", "align": "right"}],
                rows, "name", RAIL_RPC, height="calc(100vh - 290px)", virtual=True)
            avail.bind_filter_from(find, "value")
            avail.add_slot("body-cell-type", '''
                <q-td :props="props"><span :class="'pill ' + (props.value == 'read' ? 'pill-OPEN' : 'pill-other')">{{props.value}}</span></q-td>''')
            avail.add_slot("body-cell-description", '''
                <q-td :props="props" style="max-width:420px;white-space:normal">{{props.value}}</q-td>''')
            avail.add_slot("body-cell-args", '''
                <q-td :props="props" style="max-width:260px;white-space:normal">{{props.value}}</q-td>''')
            avail.add_slot("body-cell-use", '''
                <q-td :props="props"><q-btn flat dense no-caps color="primary" label="Use" @click.stop="$parent.$parent.$emit('use', props.row.name)"><q-tooltip>Copy into the Custom RPC editor</q-tooltip></q-btn></q-td>''')

            def use(name: str) -> None:
                kind.value = "custom"
                custom.value = json.dumps({name: {}}, indent=2)
                if dev_pick.value:
                    targets.value = [dev_pick.value]
                tabs.value = run_tab
                ui.notify(f"{name} copied into the Custom RPC editor", type="positive")

            avail.on("use", lambda e: use(e.args))
            avail.on("rowClick", lambda e: use(e.args[1]["name"]))

            async def load(refresh: bool = False) -> None:
                if not dev_pick.value:
                    return
                status.set_visibility(True)
                try:
                    rpcs = await index.for_device(dev_pick.value, refresh=refresh)
                except (RestconfError, TimeoutError) as e:
                    status.set_visibility(False)
                    ui.notify(f"Could not read RPCs: {e}", type="negative", multi_line=True, close_button=True)
                    return
                status.set_visibility(False)
                shown = [r for r in rpcs if r.read_only or not only_read["v"]]
                rows[:] = [{"name": r.name, "type": "read" if r.read_only else "ACTION", "area": r.area,
                            "description": r.description, "args": ", ".join(r.args)} for r in shown]
                avail.rows = rows
                avail.update()
                count.set_text(f"{len(shown):,} RPCs")

            dev_pick.on_value_change(lambda e: load(False))
            ui.timer(0.1, lambda: load(False), once=True)


        with ui.tab_panel(cli_tab).classes("p-0 gap-3"):
            ui.label("Run an operational command as you would type it in the device's CLI (Junos). "
                     "The reply comes back as structured data. Only plain “show” commands run without asking; "
                     "anything else (request, clear, restart, ping, …) needs confirmation.").classes("mut text-sm max-w-3xl")
            with ui.card().classes("w-full p-4 gap-3"):
                with ui.row().classes("w-full items-start gap-3 no-wrap"):
                    cli_devs = ui.select(all_devs, label="Devices", multiple=True, with_input=True, value=[]
                                         ).props("use-chips outlined dense").classes("w-80")
                    cli_groups = ui.select(groups, label="Groups", multiple=True, with_input=True, value=[]
                                           ).props("use-chips outlined dense").classes("w-48")
                    cmd = ui.input(placeholder="show version").props("outlined dense clearable autofocus").classes("grow font-mono")
                    with cmd.add_slot("prepend"):
                        ui.icon("chevron_right")
                    cli_run = ui.button("Run", icon="play_arrow", on_click=lambda: run_cli()).props("no-caps no-wrap").classes(BTN)
                recent = ui.row().classes("items-center gap-2")
            cli_prog = ui.row().classes("items-center gap-2")
            with cli_prog:
                ui.spinner(size="sm")
                cli_stage = ui.label().classes("mut")
            cli_prog.set_visibility(False)
            cli_results = ui.column().classes("w-full gap-2")

            def draw_recent() -> None:
                recent.clear()
                hist = app.storage.user.get("cli_history", [])
                with recent:
                    if hist:
                        ui.label("Recent").classes("mut text-sm")
                    for c in hist:
                        ui.chip(c, on_click=lambda c=c: cmd.set_value(c)).props("outline dense clickable").classes("font-mono")

            async def run_cli() -> None:
                text = (cmd.value or "").strip()
                devs, grps = list(cli_devs.value), list(cli_groups.value)
                if not text:
                    ui.notify("Type a command first", type="warning")
                    return
                if not devs and not grps:
                    ui.notify("Choose at least one device or group", type="warning")
                    return
                req = cli_request(text)
                if not req["read_only"] and not await _confirm_run(req, devs, grps):
                    return
                hist = [text] + [c for c in app.storage.user.get("cli_history", []) if c != text]
                app.storage.user["cli_history"] = hist[:10]
                draw_recent()
                cli_run.set_enabled(False)
                cli_prog.set_visibility(True)
                cli_stage.set_text(f"Running “{text}”…")
                cli_results.clear()
                done = await run_on_targets(client, req, devs, grps)
                cli_prog.set_visibility(False)
                cli_run.set_enabled(True)
                with cli_results:
                    _show_results(done)

            cmd.on("keydown.enter", lambda: run_cli())
            draw_recent()


_INDEX: dict = {}


def _index(client) -> RpcIndex:
    """One shared index per controller connection, so modules are fetched once for all devices."""
    if _INDEX.get("client") is not client:
        _INDEX.update(client=client, index=RpcIndex(client))
    return _INDEX["index"]
