"""RPC page: run operational commands (RPC templates or custom RPCs) on devices and show the replies."""

from __future__ import annotations

import asyncio
import json
from html import escape

from nicegui import ui

from . import views
from .client import RestconfError
from .confview import to_lines
from .rpcutil import is_read_only, rpc_name, substitute, template_vars
from .style import BTN

CUSTOM_EXAMPLE = '''{
  "get-interface-information": {
    "terse": {}
  }
}'''


def _text_html(lines: list[str]) -> str:
    return "".join(f'<div class="cl"><span class="ln">{i}</span>{escape(ln)}</div>' for i, ln in enumerate(lines, 1))


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
        if not req["read_only"]:
            with ui.dialog() as d, ui.card().classes("w-[520px] gap-2"):
                ui.label(f"Run {req['label']}?").classes("text-lg")
                ui.label("This RPC is not read-only and can change the device state.").classes("warn-tx")
                ui.code(json.dumps(req["body"], indent=2), language="json").classes("w-full")
                ui.label("Targets: " + ", ".join(devs + [f"group {g}" for g in grps])).classes("text-sm")
                with ui.row().classes("justify-end w-full"):
                    ui.button("Cancel", on_click=d.close).props("flat no-caps no-wrap").classes(BTN)
                    ui.button("Run RPC", color="negative", on_click=lambda: d.submit(True)).props("no-caps no-wrap").classes(BTN)
            if not await d:
                return
        await execute(req, devs, grps)

    async def execute(req: dict, devs: list[str], grps: list[str]) -> None:
        run_btn.set_enabled(False)
        prog.set_visibility(True)
        stage.set_text(f"Running {req['label']}…")
        results.clear()
        jobs = [(d, {"device": d}) for d in devs] + [(f"group {g}", {"group": g}) for g in grps]

        async def job(label: str, tgt: dict) -> tuple[str, dict]:
            try:
                tid = await client.run_rpc(template=req.get("template"), inline=req.get("inline"),
                                           variables=req.get("variables"), **tgt)
                tr = await client.wait_transaction(tid, timeout=120, interval=0.5)
                if not tr or tr.get("result") != "SUCCESS":
                    return label, {"error": (tr or {}).get("reason", "failed").strip() or "failed", "tid": tid}
                return label, {"tid": tid, "replies": await client.rpc_result(tid), "tr": tr}
            except (RestconfError, TimeoutError) as e:
                return label, {"error": str(e)}

        gate = asyncio.Semaphore(4)

        async def limited(lbl: str, t: dict) -> tuple[str, dict]:
            async with gate:
                return await job(lbl, t)

        done = await asyncio.gather(*(limited(lbl, t) for lbl, t in jobs))
        prog.set_visibility(False)
        run_btn.set_enabled(True)
        with results:
            for label, res in done:
                if "error" in res:
                    show_card(label, None, res["error"])
                    continue
                for dev, data in (res["replies"] or {}).items() or [(label, None)]:
                    show_card(dev, data, None)

    def show_card(name: str, data, error: str | None) -> None:
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
