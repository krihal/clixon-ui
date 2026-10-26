"""Guide editor (`/guides/{gid}/edit`, `new` for a new guide). Admins only.

The editor works on a copy of the guide in memory; Save checks it with `guide.validate` and stores it. Fields write
straight into the step dicts. The step list is redrawn only when its structure changes (add, move, delete), and the
steps that were open stay open."""

from __future__ import annotations

import copy
import json

from nicegui import ui

from . import auth_views, db, guide, guides, views
from .client import RestconfError
from .guide import GuideError
from .rpc_views import _index
from .rpcrun import run_on_targets
from .style import BTN
from .tables import data_table

MONO_AREA = 'outlined stack-label input-style="font-family:var(--mono);font-size:13px;line-height:1.5;min-height:72px"'


def next_id(steps: list[dict]) -> str:
    used = {s["id"] for s in steps}
    n = len(steps) + 1
    while f"s{n}" in used:
        n += 1
    return f"s{n}"


def variables_before(steps: list[dict], index: int) -> list[str]:
    """Variables set by input steps above `index`, as `{{name}}` tokens."""
    return [f"{{{{{s['var']}}}}}" for s in steps[:index] if s["type"] == "input" and s.get("var")]


def unique(*lists: list[str]) -> list[str]:
    seen: dict[str, None] = {}
    for items in lists:
        for x in items:
            seen.setdefault(x, None)
    return list(seen)


async def edit_page(gid: str):
    if not auth_views.current_access() or not auth_views.current_access().is_admin:
        ui.label("Only admins can edit guides.").classes("text-xl")
        return
    client = views.client
    row_id = int(gid) if gid.isdigit() else None
    with db.session() as s:
        row = guides.get(s, row_id) if row_id else None
        if row_id and row is None:
            ui.label("The guide does not exist.").classes("text-xl")
            ui.button("Guides", icon="menu_book", on_click=lambda: ui.navigate.to("/guides")).props("no-caps no-wrap").classes(BTN)
            return
        work = copy.deepcopy(guides.body(row)) if row else {"name": "", "description": "", "steps": []}
    try:
        all_devs = await client.devices(cached=True)
        devices = sorted(d["name"] for d in all_devs)
        open_devices = [d for d in devices if next((x for x in all_devs if x["name"] == d), {}).get("conn-state") == "OPEN"]
        groups = sorted(await client.device_groups())
    except RestconfError:
        devices, open_devices, groups = [], [], []
    remembered: dict[str, str] = {}  # values given for {{variables}} while trying an RPC, kept for the next try
    steps: list[dict] = work["steps"]
    opened: set[str] = {s["id"] for s in steps[:1]}

    # ------------------------------------------------------------------------------------------ header
    with ui.row().classes("w-full items-center gap-3"):  # wraps on a phone
        ui.button(icon="arrow_back", on_click=lambda: ui.navigate.to("/guides")).props("flat round dense").tooltip("Guides")
        ui.label("Edit guide" if row else "New guide").classes("text-2xl grow whitespace-nowrap")
        mode = ui.toggle({"form": "Form", "json": "JSON"}, value="form", on_change=lambda e: switch(e)).props(
            "no-caps no-wrap dense unelevated toggle-color=primary color=transparent text-color=dark")
        ui.button("Cancel", on_click=lambda: ui.navigate.to("/guides")).props("flat no-caps no-wrap").classes(BTN)
        ui.button("Save", icon="save", on_click=lambda: save()).props("no-caps no-wrap").classes(BTN)
    problems = ui.label().classes("err-box whitespace-pre-wrap w-full")
    problems.set_visibility(False)

    with ui.column().classes("w-full gap-3") as form_col:
        with ui.card().classes("w-full p-4 gap-3"):
            ui.input("Name").bind_value(work, "name").props("outlined dense").classes("w-full max-w-xl")
            ui.textarea("What is this guide for?").bind_value(work, "description").props("outlined dense autogrow").classes("w-full max-w-3xl")

        ui.label("Steps").classes("text-lg mt-2")
        box = ui.column().classes("w-full gap-2")
        add_row = ui.row().classes("items-center gap-3")

    with ui.column().classes("w-full gap-2") as json_col:
        ui.label("The whole guide as JSON, the same format as Export and Import. It is read back when you switch to Form or press Save.").classes("mut text-sm")
        json_text = ui.textarea().props('outlined stack-label input-style="font-family:var(--mono);font-size:13px;line-height:1.5;min-height:62vh"'
                                        ).classes("w-full")
    json_col.set_visibility(False)

    # ------------------------------------------------------------------------------------------ small editors
    def target_editor(target: dict, index: int) -> None:
        pool = unique(devices, variables_before(steps, index))
        with ui.row().classes("w-full gap-3 items-start"):
            for key, label, options in (("devices", "Devices", pool), ("groups", "Device groups", unique(groups, variables_before(steps, index)))):
                ui.select(unique(options, target[key]), label=label, multiple=True, with_input=True, new_value_mode="add-unique",
                          value=target[key], on_change=lambda e, k=key: target.__setitem__(k, list(e.value))
                          ).props("use-chips outlined dense").classes("w-96 max-w-full")
        ui.label("Pick devices or groups, or type {{variable}} to take it from an input step.").classes("mut text-xs")

    def rules_editor(rules: list[dict], label: str = "Must"):
        holder = ui.column().classes("w-full gap-2")

        def draw() -> None:
            holder.clear()
            with holder:
                for rule in rules:
                    with ui.row().classes("w-full items-start gap-2 no-wrap"):
                        ui.input("Path in the reply", placeholder="software-information/junos-version").bind_value(rule, "path").props(
                            "outlined dense stack-label").classes("grow font-mono")
                        op = ui.select(guide.OPERATORS, label=label, value=rule["op"], on_change=lambda e, r=rule: (
                            r.__setitem__("op", e.value), draw())).props("outlined dense").classes("w-48")
                        if rule["op"] not in ("exists", "not-exists"):
                            ui.input("Value").bind_value(rule, "value").props("outlined dense").classes("w-64")
                        op.tooltip("Several values found: equals/contains/regex/exists pass if any matches; the negated ones if none does.")
                        ui.button(icon="close", on_click=lambda r=rule: (rules.remove(r), draw())).props("flat round dense").tooltip("Remove rule")
                ui.button("Add rule", icon="add", on_click=lambda: (rules.append({"path": "", "op": "equals", "value": ""}), draw())
                          ).props("outline dense no-caps no-wrap").classes("w-32")

        draw()
        return lambda rule: (rules.append(rule), draw())

    def fields_editor(show: list[dict]):
        """The fields of the reply the runner shows as a summary above the full reply. Returns the function that adds one."""
        holder = ui.column().classes("w-full gap-2")

        def draw() -> None:
            holder.clear()
            with holder:
                for f in show:
                    with ui.row().classes("w-full items-start gap-2 no-wrap"):
                        ui.input("Label").bind_value(f, "label").props("outlined dense stack-label").classes("w-56")
                        ui.input("Path in the reply").bind_value(f, "path").props("outlined dense").classes("grow font-mono")
                        ui.button(icon="close", on_click=lambda f=f: (show.remove(f), draw())).props("flat round dense").tooltip("Remove field")
                ui.button("Add field", icon="add", on_click=lambda: (show.append({"path": "", "label": ""}), draw())
                          ).props("outline dense no-caps no-wrap").classes("w-32")

        draw()
        return lambda path, label: (show.append({"path": path, "label": label}), draw())

    async def ask_values(names: list[str]) -> dict[str, str] | None:
        """Values for the {{variables}} an RPC uses, for this try only."""
        if not names:
            return {}
        with ui.dialog() as d, ui.card().classes("w-[480px] gap-2"):
            ui.label("Values for this try").classes("text-lg")
            ui.label("The RPC uses variables. Give them a value for this try; nothing is saved in the guide.").classes("mut text-sm")
            fields = {n: ui.input(n, value=remembered.get(n, "")).props("outlined dense").classes("w-full") for n in names}
            with ui.row().classes("justify-end w-full"):
                ui.button("Cancel", on_click=lambda: d.submit(None)).props("flat no-caps no-wrap").classes(BTN)
                ui.button("Try it", icon="play_arrow", on_click=lambda: d.submit({n: (f.value or "").strip() for n, f in fields.items()})
                          ).props("no-caps no-wrap").classes(BTN)
        got = await d
        d.delete()
        if got is not None and not all(got.values()):
            ui.notify("Give every variable a value.", type="warning")
            return None
        remembered.update(got or {})
        return got

    def try_panel(probe: dict, hooks: dict, modes: dict[str, str]) -> None:
        """Run the probe's RPC once on a connected device and list what came back; clicking a value picks it."""
        with ui.row().classes("items-center gap-3"):
            wanted = [d for d in probe["target"]["devices"] if d in open_devices]
            dev = ui.select(open_devices, label="Try it on", with_input=True, value=(wanted or open_devices or [None])[0]
                            ).props("outlined dense").classes("w-72")
            ui.button("Try it", icon="play_arrow", on_click=lambda: tryit()).props("outline dense no-caps no-wrap").classes(BTN)
            spin = ui.spinner(size="sm")
            spin.set_visibility(False)
        ui.label("Runs the RPC once on that device (read-only RPCs only) so you can pick values from the reply.").classes("mut text-xs")
        result = ui.column().classes("w-full gap-2")

        async def tryit() -> None:
            body = probe.get("body")
            if not body:
                ui.notify("Pick or write the RPC first.", type="warning")
                return
            if not guide.inline_is_read_only(body):
                ui.notify("Only read-only RPCs (get-*, show ...) can be tried while editing a guide.", type="negative")
                return
            if not dev.value:
                ui.notify("No connected device to try it on.", type="warning")
                return
            if (values := await ask_values(guide.variables_in(body))) is None:
                return
            try:
                real = guide.fill(body, values)
            except GuideError as e:
                ui.notify(str(e), type="negative")
                return
            spin.set_visibility(True)
            done = await run_on_targets(client, {"inline": real, "label": next(iter(real))}, [dev.value], [])
            spin.set_visibility(False)
            res = done[0][1]
            result.clear()
            with result:
                if "error" in res:
                    ui.label(res["error"]).classes("err-box whitespace-pre-wrap w-full")
                    return
                data = next(iter((res["replies"] or {}).values()), None)
                rows = guide.leaf_paths(data)
                if not rows:
                    ui.label("The reply is empty.").classes("mut")
                    return
                with ui.row().classes("items-center gap-3"):
                    ui.label("Click a value to").classes("font-medium text-sm")
                    mode = ui.toggle(modes, value=next(iter(modes))).props(
                        "no-caps no-wrap dense unelevated toggle-color=primary color=transparent text-color=dark")
                flt = ui.input(placeholder="Filter…").props("dense outlined clearable").classes("w-full")
                full = {r["path"]: r["sample"] for r in rows}
                table = data_table(
                    [{"name": "path", "label": "Path", "field": "path", "align": "left", "classes": "font-mono"},
                     {"name": "sample", "label": "Value", "field": "shown", "align": "left"},
                     {"name": "count", "label": "Values", "field": "count", "align": "right"}],
                    [{**r, "shown": r["sample"][:90]} for r in rows], "path", "'rail-ok'", height="32vh")
                table.bind_filter_from(flt, "value")

                def picked(e) -> None:
                    path = e.args[1]["path"]
                    hooks[mode.value](path, full[path])
                    ui.notify(f"Added {path}", type="positive")

                table.on("rowClick", picked)

    def probe_editor(probe: dict, index: int, *, read_only: bool, rules: bool) -> None:
        """The RPC to send (picked, typed as a CLI command, or as JSON), where to send it, and what to check in the reply."""
        warn = ui.label().classes("warn-tx text-sm")
        body_text = ui.textarea("RPC as JSON (the first element is the RPC name)", value=json.dumps(probe["body"], indent=2) if probe["body"] else "",
                                placeholder='{"get-system-information": {}}').props(MONO_AREA + " autogrow").classes("w-full max-w-3xl")

        def parse(e=None) -> None:
            try:
                body = json.loads(body_text.value) if body_text.value.strip() else {}
                assert isinstance(body, dict)
            except (ValueError, AssertionError):
                warn.set_text("This is not a JSON object yet.")
                return
            probe["body"] = body
            warn.set_text("Only read-only RPCs (get-*, show ...) can be used here." if read_only and body and not guide.inline_is_read_only(body)
                          else "")

        body_text.on_value_change(parse)
        with ui.row().classes("items-start gap-3 no-wrap w-full"):
            ui.button("Pick from device RPCs", icon="search", on_click=lambda: pick()).props("outline dense no-caps no-wrap").classes(BTN)
            cli = ui.input(placeholder="or a CLI command, e.g. show version").props("outlined dense clearable").classes("grow max-w-xl font-mono")
            ui.button("Use command", on_click=lambda: use_cli()).props("outline dense no-caps no-wrap").classes(BTN)

        def use_cli() -> None:
            if (text := (cli.value or "").strip()):
                body_text.set_value(json.dumps({"command": text}, indent=2))

        async def pick() -> None:
            name = await pick_rpc(read_only)
            if name:
                body_text.set_value(json.dumps({name: {}}, indent=2))

        target_editor(probe["target"], index)
        hooks: dict = {}
        try_panel(probe, hooks, {"rule": "add a rule that checks it"} if rules else
                  {"show": "show it", "success": "require it for success", "failure": "fail on it"})
        if rules:
            ui.label("Check the reply of every target. All of them must pass.").classes("mut text-sm")
            add_rule = rules_editor(probe["rules"])
            hooks["rule"] = lambda path, sample: add_rule({"path": path, "op": "equals", "value": sample})
        else:
            ui.label("Fields to show: a summary table above the full reply. Leave empty to show only the reply.").classes("mut text-sm")
            add_field = fields_editor(probe.setdefault("show", []))
            hooks["show"] = lambda path, sample: add_field(path, guide.label_for(path))
            ui.label("Succeeded when (optional): every rule must hold in the reply of every target. Without rules the step succeeds "
                     "when the device answers without an error.").classes("mut text-sm")
            add_ok = rules_editor(probe.setdefault("success", []))
            hooks["success"] = lambda path, sample: add_ok({"path": path, "op": "equals", "value": sample})
            ui.label("Failed when (optional): the step fails as soon as one of these rules holds in a reply.").classes("mut text-sm")
            add_bad = rules_editor(probe.setdefault("failure", []), label="Fails if it")
            hooks["failure"] = lambda path, sample: add_bad({"path": path, "op": "equals", "value": sample})
        parse()

    async def pick_rpc(read_only: bool) -> str | None:
        """Search the RPCs a device offers (from its YANG on the controller) and return the chosen name."""
        index = _index(client)
        try:
            all_devices = [d["name"] for d in await client.devices(cached=True) if d.get("conn-state") == "OPEN"]
        except RestconfError:
            all_devices = []
        with ui.dialog() as d, ui.card().classes("w-[900px] max-w-full gap-2"):
            ui.label("Pick an RPC").classes("text-lg")
            ui.label("The list comes from the YANG of one connected device." + (" Only read-only RPCs are shown." if read_only else "")).classes("mut text-sm")
            with ui.row().classes("w-full items-center gap-3 no-wrap"):
                dev = ui.select(all_devices, label="Device", with_input=True, value=all_devices[0] if all_devices else None).props("outlined dense").classes("w-72")
                flt = ui.input(placeholder="Filter…").props("dense outlined clearable").classes("grow")
                spin = ui.spinner(size="sm")
            table = data_table(
                [{"name": "name", "label": "RPC", "field": "name", "align": "left", "sortable": True, "classes": "name"},
                 {"name": "type", "label": "Effect", "field": "type", "align": "left"},
                 {"name": "area", "label": "Area", "field": "area", "align": "left"},
                 {"name": "description", "label": "Description", "field": "description", "align": "left"}],
                [], "name", "row.type == 'read' ? 'rail-ok' : 'rail-warn'", height="46vh", virtual=True)
            table.bind_filter_from(flt, "value")
            table.on("rowClick", lambda e: d.submit(e.args[1]["name"]))
            with ui.row().classes("justify-end w-full"):
                ui.button("Cancel", on_click=lambda: d.submit(None)).props("flat no-caps no-wrap").classes(BTN)

            async def load() -> None:
                if not dev.value:
                    spin.set_visibility(False)
                    return
                spin.set_visibility(True)
                try:
                    rpcs = await index.for_device(dev.value)
                except (RestconfError, TimeoutError) as e:
                    ui.notify(f"Could not read the RPCs: {e}", type="negative", multi_line=True)
                    rpcs = []
                spin.set_visibility(False)
                table.rows = [{"name": r.name, "type": "read" if r.read_only else "ACTION", "area": r.area, "description": r.description}
                              for r in rpcs if r.read_only or not read_only]
                table.update()

            dev.on_value_change(load)
            ui.timer(0.1, load, once=True)
        name = await d
        d.delete()
        return name

    def device_field(step: dict, index: int, key: str = "device") -> None:
        options = unique(["*"], devices, variables_before(steps, index), [step[key]] if step.get(key) else [])
        ui.select(options, label="Device (* = all)", with_input=True, new_value_mode="add-unique", value=step.get(key) or None,
                  on_change=lambda e: step.__setitem__(key, e.value or "")).props("outlined dense").classes("w-96 max-w-full")

    # ------------------------------------------------------------------------------------------ one step
    def step_body(step: dict, index: int) -> None:
        t = step["type"]
        ui.input("Title").bind_value(step, "title").props("outlined dense").classes("w-full max-w-xl")
        vars_here = variables_before(steps, index)
        hint = ("Variables you can use here: " + ", ".join(vars_here)) if vars_here else "No input step above, so no variables yet."
        if t == "text":
            ui.textarea("Instruction (markdown)").bind_value(step, "text").props("outlined autogrow").classes("w-full max-w-3xl")
            ui.label(hint).classes("mut text-xs")
        elif t == "confirm":
            ui.textarea("Question the operator must confirm").bind_value(step, "question").props("outlined autogrow").classes("w-full max-w-3xl")
            ui.label(hint).classes("mut text-xs")
        elif t == "input":
            with ui.row().classes("w-full gap-3 items-start"):
                ui.input("Variable name", placeholder="device").bind_value(step, "var").props("outlined dense stack-label").classes("w-56")
                ui.input("Question shown").bind_value(step, "label").props("outlined dense").classes("w-80")
                kind = ui.select({"text": "Free text", "device": "A device", "group": "A device group", "choice": "One of a list"},
                                 label="Kind", value=step["kind"], on_change=lambda e: (step.__setitem__("kind", e.value), choices.set_visibility(e.value == "choice"))
                                 ).props("outlined dense").classes("w-56")
                ui.input("Default").bind_value(step, "default").props("outlined dense").classes("w-56")
            choices = ui.input("Choices, separated by commas", value=", ".join(step.get("choices", [])),
                               on_change=lambda e: step.__setitem__("choices", [c.strip() for c in e.value.split(",") if c.strip()])
                               ).props("outlined dense").classes("w-full max-w-xl")
            choices.set_visibility(kind.value == "choice")
            ui.label("Later steps use the answer as {{name}}.").classes("mut text-xs")
        elif t == "rpc":
            probe_editor(step, index, read_only=False, rules=False)
            ui.label("Anything that is not read-only is always confirmed by the operator before it runs.").classes("mut text-xs")
        elif t == "check":
            probe_editor(step, index, read_only=True, rules=True)
        elif t == "wait":
            mode = ui.toggle({"fixed": "Fixed time", "until": "Until a check passes"}, value="until" if step.get("until") else "fixed"
                             ).props("no-caps no-wrap dense unelevated toggle-color=primary color=transparent text-color=dark")
            fixed = ui.column().classes("gap-2")
            polled = ui.column().classes("w-full gap-2")
            with fixed:
                ui.number("Seconds", value=step.get("seconds", 60), min=1, max=guide.MAX_WAIT_SECONDS, precision=0,
                          on_change=lambda e: step.__setitem__("seconds", int(e.value or 0))).props("outlined dense").classes("w-40")
            if not step.get("until"):
                step_until = {"body": {}, "target": {"devices": [], "groups": []}, "rules": [], "interval": 10, "timeout": 600}
            else:
                step_until = step["until"]
            with polled:
                ui.label("Ask the device again and again until the reply passes the rules, e.g. until it is back after a reboot. "
                         "Errors while it is down count as 'not yet'.").classes("mut text-sm")
                probe_editor(step_until, index, read_only=True, rules=True)
                with ui.row().classes("gap-3"):
                    ui.number("Ask every (seconds)", value=step_until["interval"], min=1, precision=0,
                              on_change=lambda e: step_until.__setitem__("interval", int(e.value or 10))).props("outlined dense").classes("w-48")
                    ui.number("Give up after (seconds)", value=step_until["timeout"], min=1, max=guide.MAX_WAIT_SECONDS, precision=0,
                              on_change=lambda e: step_until.__setitem__("timeout", int(e.value or 0))).props("outlined dense").classes("w-56")

            def switch(e) -> None:
                fixed.set_visibility(e.value == "fixed")
                polled.set_visibility(e.value == "until")
                if e.value == "until":
                    step["until"] = step_until
                else:
                    step.pop("until", None)

            mode.on_value_change(switch)
            fixed.set_visibility(not step.get("until"))
            polled.set_visibility(bool(step.get("until")))
        elif t == "connection":
            ui.select(list(guide.CONNECTION_OPERATIONS), label="Operation", value=step["operation"],
                      on_change=lambda e: step.__setitem__("operation", e.value)).props("outlined dense").classes("w-56")
            device_field(step, index)
            ui.label("All devices (*) is one controller transaction; OPEN only opens enabled devices that are closed. "
                     "The operator is asked before it runs.").classes("mut text-xs")
        elif t == "pull":
            device_field(step, index)
            ui.checkbox("Merge into the controller's copy instead of replacing it").bind_value(step, "merge")
            ui.label("The operator is asked before it runs.").classes("mut text-xs")

    def draw_steps() -> None:
        box.clear()
        with box:
            if not steps:
                with ui.column().classes("w-full items-center py-6 gap-1"):
                    ui.icon("playlist_add", size="lg").classes("mut")
                    ui.label("No steps yet").classes("font-medium")
                    ui.label("Add the first step below.").classes("mut text-sm")
            for i, step in enumerate(steps):
                label, icon = guide.STEP_TYPES[step["type"]]
                with ui.expansion(value=step["id"] in opened, on_value_change=lambda e, sid=step["id"]: opened.add(sid) if e.value else opened.discard(sid)
                                  ).classes("w-full border line rounded-lg").props("dense expand-separator") as ex:
                    with ex.add_slot("header"):
                        with ui.row().classes("items-center gap-2 w-full no-wrap"):
                            ui.icon(icon, size="sm").classes("mut hide-phone")
                            ui.label(f"{i + 1}.").classes("mut")
                            ui.label().bind_text_from(step, "title").classes("font-medium grow truncate min-w-0")
                            ui.label(label).classes("mut text-sm hide-phone")
                            for ic, tip, fn in (("arrow_upward", "Move up", lambda i=i: move(i, -1)), ("arrow_downward", "Move down", lambda i=i: move(i, 1)),
                                                ("content_copy", "Duplicate", lambda i=i: duplicate(i)), ("delete", "Remove", lambda i=i: remove(i))):
                                ui.button(icon=ic, on_click=fn).props("flat round dense").classes("hide-phone" if ic == "content_copy" else "").tooltip(tip
                                                                                                                                      ).on("click.stop", lambda: None)
                    with ui.column().classes("w-full gap-3 p-3"):
                        step_body(step, i)

    def move(i: int, d: int) -> None:
        if 0 <= i + d < len(steps):
            steps[i], steps[i + d] = steps[i + d], steps[i]
            draw_steps()

    def duplicate(i: int) -> None:
        clone = copy.deepcopy(steps[i])
        clone["id"] = next_id(steps)
        steps.insert(i + 1, clone)
        opened.add(clone["id"])
        draw_steps()

    def remove(i: int) -> None:
        opened.discard(steps[i]["id"])
        del steps[i]
        draw_steps()

    def add(type_: str) -> None:
        step = guide.new_step(type_, next_id(steps))
        steps.append(step)
        opened.add(step["id"])
        draw_steps()

    with add_row:
        with ui.button("Add step", icon="add").props("no-caps no-wrap").classes(BTN):
            with ui.menu():
                for t, (label, icon) in guide.STEP_TYPES.items():
                    with ui.menu_item(on_click=lambda t=t: add(t)):
                        with ui.row().classes("items-center gap-2 no-wrap"):
                            ui.icon(icon, size="sm").classes("mut")
                            ui.label(label)

    def current_json() -> str:
        return json.dumps({"name": work["name"], "description": work["description"], "steps": steps}, indent=2, ensure_ascii=False)

    def apply_json() -> bool:
        """Read the JSON view back into the guide. False, with the reason shown, when it cannot be read."""
        try:
            data = json.loads(json_text.value or "")
            if not isinstance(data, dict) or not isinstance(data.get("steps"), list) or not all(isinstance(s, dict) for s in data["steps"]):
                raise GuideError("A guide is a JSON object with a list of steps, and every step is an object.")
            used = {s["id"] for s in data["steps"] if s.get("id")}
            new_steps = []
            for n, s in enumerate(data["steps"], 1):
                sid = s.get("id") or next(f"s{k}" for k in range(n, n + len(used) + len(data["steps"]) + 2) if f"s{k}" not in used)
                used.add(sid)
                try:
                    new_steps.append(guide.complete_step(s, sid))
                except GuideError as e:
                    raise GuideError(f"Step {n}: {e}.") from e
        except ValueError as e:  # json.JSONDecodeError says where
            problems.set_text(f"Not valid JSON: {e}")
            problems.set_visibility(True)
            return False
        except GuideError as e:
            problems.set_text(str(e))
            problems.set_visibility(True)
            return False
        work["name"], work["description"] = str(data.get("name", "")), str(data.get("description", ""))
        steps[:] = new_steps
        opened.intersection_update({s["id"] for s in steps})
        problems.set_visibility(False)
        draw_steps()
        return True

    switching = {"back": False}

    def switch(e) -> None:
        if switching["back"]:
            return
        if e.value == "json":
            json_text.set_value(current_json())
            problems.set_visibility(False)
            form_col.set_visibility(False)
            json_col.set_visibility(True)
        elif apply_json():
            json_col.set_visibility(False)
            form_col.set_visibility(True)
        else:  # the JSON cannot be read yet: stay in the JSON view, the reason is shown
            switching["back"] = True
            mode.set_value("json")
            switching["back"] = False

    def save() -> None:
        if mode.value == "json" and not apply_json():
            return
        if errors := guide.validate(work):
            problems.set_text("\n".join(errors))
            problems.set_visibility(True)
            return
        problems.set_visibility(False)
        try:
            with db.session() as s:
                saved = guides.save(s, work, auth_views.current_username() or "", row_id)
                name, version = saved.name, saved.version
        except GuideError as e:
            problems.set_text(str(e))
            problems.set_visibility(True)
            return
        ui.notify(f"Saved '{name}' (version {version}).", type="positive")
        ui.navigate.to("/guides")

    draw_steps()
