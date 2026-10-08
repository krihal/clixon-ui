"""Render an editable NiceGUI form for a YANG node, bound to a plain dict (see formdata)."""

from __future__ import annotations

from collections.abc import Callable

from nicegui import ui

from .formdata import Lookup, _empty, type_error
from .style import BTN
from .schema import Node, YType, data_children

Touch = Callable[[], None]


def _hint(c: Node) -> str:
    h = c.description
    if c.default is not None:
        h = f"{h} (default: {c.default})" if h else f"default: {c.default}"
    return h.replace('"', "'")


def _label(c: Node, required: bool) -> str:
    return f"{c.name} *" if required else c.name


def _hintprops(el, c: Node):
    el.props("outlined dense")
    h = _hint(c)
    if len(h) > 70:  # long text would overflow the hint line: show on hover instead
        el.tooltip(h)
        el.props('hint="hover for details"')
    elif h:
        el.props(f'hint="{h}"')
    return el


def _first_error(t: YType | None, lookup: Lookup, items: list) -> str | None:
    for x in items:
        if (e := type_error(t, x, lookup)):
            return f"{x}: {e}"
    return None


def _set(data: dict, name: str, v, touch: Touch) -> None:
    if _empty(v):
        data.pop(name, None)
    else:
        data[name] = v
    touch()


def _is_required(c: Node, parent: Node) -> bool:
    return c.mandatory or c.name in parent.keys


def render_children(node: Node, data: dict, lookup: Lookup, touch: Touch, locked: set[str] = frozenset()) -> None:
    """Render all direct children of `node` (choices included) for the dict `data`."""
    for c in node.children:
        render_child(c, node, data, lookup, touch, locked)


def render_child(c: Node, parent: Node, data: dict, lookup: Lookup, touch: Touch, locked: set[str] = frozenset()) -> None:
    if c.kind == "leaf":
        render_leaf(c, parent, data, lookup, touch, c.name in locked)
    elif c.kind == "leaf-list":
        render_leaf_list(c, data, lookup, touch)
    elif c.kind == "container":
        render_container(c, data, lookup, touch)
    elif c.kind == "list":
        render_list(c, data, lookup, touch)
    elif c.kind == "choice":
        render_choice(c, data, lookup, touch)


# ------------------------------------------------------------------ leaves
def render_leaf(c: Node, parent: Node, data: dict, lookup: Lookup, touch: Touch, locked: bool = False) -> None:
    t = c.type
    required = _is_required(c, parent)
    label = _label(c, required)
    val = data.get(c.name)
    cls = "w-full max-w-2xl"
    err = lambda v: type_error(t, v, lookup)  # noqa: E731

    if t.base == "boolean":
        initial = val if val is not None else (c.default == "true")
        el = ui.switch(c.name, value=bool(initial), on_change=lambda e: (data.__setitem__(c.name, bool(e.value)), touch()))
        if c.description:
            el.tooltip(c.description)
        return
    if t.base == "empty":
        el = ui.switch(c.name, value=bool(val), on_change=lambda e: _set(data, c.name, True if e.value else None, touch))
        if c.description:
            el.tooltip(c.description)
        return

    options: list | None = None
    if t.base == "enum":
        options = t.enums
    elif t.base == "identityref" and t.identities:
        options = t.identities
    elif t.base == "leafref":
        options = lookup.values(t.path) or None
    if options:
        options = list(options)
        if val is not None and val not in options:
            options.append(val)
        el = ui.select(options, label=label, value=val, with_input=True, clearable=not required,
                       on_change=lambda e: _set(data, c.name, e.value, touch)).classes(cls)
    elif t.base in ("int", "decimal"):
        lo, hi = t.range or (None, None)
        el = ui.number(label, value=val, min=lo, max=hi, precision=0 if t.base == "int" else None,
                       step=1 if t.base == "int" else 0.01, validation=err,
                       on_change=lambda e: _set(data, c.name, None if e.value is None else (int(e.value) if t.base == "int" else e.value), touch)).classes(cls)
    else:
        el = ui.input(label, value="" if val is None else str(val), validation=err,
                      on_change=lambda e: _set(data, c.name, e.value, touch)).classes(cls)
        if c.default is not None:
            el.props(f'placeholder="{c.default}"')
    _hintprops(el, c)
    if locked:
        el.props("readonly")
        el.tooltip("Key of an existing instance cannot be changed")


def render_leaf_list(c: Node, data: dict, lookup: Lookup, touch: Touch) -> None:
    t = c.type
    label = _label(c, c.min_elements > 0)
    cur = list(data.get(c.name, []))
    opts = None
    if t.base == "leafref":
        opts = lookup.values(t.path) or None
    elif t.base == "enum":
        opts = t.enums
    elif t.base == "identityref" and t.identities:
        opts = t.identities

    def conv(items):
        out = []
        for x in items:
            if t.base == "int":
                try:
                    x = int(x)
                except (TypeError, ValueError):
                    pass
            elif t.base == "decimal":
                try:
                    x = float(x)
                except (TypeError, ValueError):
                    pass
            out.append(x)
        return out

    if opts:
        opts = list(dict.fromkeys([*opts, *cur]))
        el = ui.select(opts, label=label, value=cur, multiple=True, with_input=True,
                       on_change=lambda e: _set(data, c.name, list(e.value or []), touch)).props("use-chips outlined dense").classes("w-full max-w-2xl")
    else:
        el = ui.input_chips(label, value=[str(x) for x in cur], new_value_mode="add-unique",
                            validation=lambda items: _first_error(t, lookup, conv(items or [])),
                            on_change=lambda e: _set(data, c.name, conv(e.value or []), touch)).classes("w-full max-w-2xl")
        el.props("outlined dense")
        el.props('hint="type a value and press Enter"' if not c.description else f'hint="{c.description.replace(chr(34), chr(39))} — press Enter to add"')
        return
    _hintprops(el, c)


# ------------------------------------------------------------------ structure
def _has_mandatory(n: Node) -> bool:
    return any(_is_required(c, n) and c.kind == "leaf" or (c.kind == "container" and not c.presence and _has_mandatory(c))
               for c in data_children(n))


def render_container(c: Node, data: dict, lookup: Lookup, touch: Touch) -> None:
    sub = data.get(c.name)
    body: dict = sub if sub is not None else {}
    if not c.presence:
        data[c.name] = body  # empty containers are dropped on serialisation
    title = c.name + ("  (optional)" if c.presence else "")
    with ui.expansion(title, icon="folder_open", value=bool(sub) or (not c.presence and _has_mandatory(c))) \
            .classes("w-full border line rounded-lg").props("dense expand-separator"):
        if c.description:
            ui.label(c.description).classes("text-xs text-gray-500")
        on = ui.switch("enabled", value=sub is not None) if c.presence else None
        inner = ui.column().classes("w-full gap-2 pl-2")
        if on:
            inner.bind_visibility_from(on, "value")

            def toggle(e) -> None:
                if e.value:
                    data[c.name] = body
                else:
                    data.pop(c.name, None)
                touch()

            on.on_value_change(toggle)
        with inner:
            render_children(c, body, lookup, touch)


def _entry_title(c: Node, e: dict) -> str:
    keys = " / ".join(str(e.get(k)) for k in c.keys if e.get(k) not in (None, ""))
    return f"{c.name}: {keys}" if keys else f"{c.name}: (new)"


def render_list(c: Node, data: dict, lookup: Lookup, touch: Touch) -> None:
    entries: list[dict] = data.setdefault(c.name, [])
    with ui.expansion(f"{c.name}  ·  list", icon="list", value=bool(entries)).classes("w-full border line rounded-lg") \
            .props("dense expand-separator"):
        if c.description:
            ui.label(c.description).classes("text-xs text-gray-500")

        @ui.refreshable
        def body() -> None:
            with ui.column().classes("w-full gap-2"):
                for e in entries:
                    entry_card(e)
                ui.button(f"Add {c.name}", icon="add", on_click=add).props("outline dense no-caps no-wrap").classes(BTN)

        def add() -> None:
            entries.append({})
            body.refresh()
            touch()

        def remove(e: dict) -> None:
            entries.remove(e)
            body.refresh()
            touch()

        def entry_card(e: dict) -> None:
            with ui.card().classes("w-full gap-2 p-3"):
                with ui.row().classes("w-full items-center"):
                    title = ui.label(_entry_title(c, e)).classes("font-medium")
                    ui.space()
                    ui.button(icon="delete", on_click=lambda: remove(e)).props("flat dense round color=negative").tooltip("Remove entry")

                def t2() -> None:
                    title.set_text(_entry_title(c, e))
                    touch()

                render_children(c, e, lookup, t2)

        body()


def render_choice(c: Node, data: dict, lookup: Lookup, touch: Touch) -> None:
    cases = [x for x in c.children if x.kind == "case"]
    names = [x.name for x in cases]

    def active() -> str | None:
        for case in cases:
            if any(not _empty(data.get(ch.name)) for ch in data_children(case)):
                return case.name
        return c.default if c.default in names else None

    with ui.column().classes("w-full gap-1 border border-dashed line rounded-lg p-3"):
        sel = ui.toggle(names, value=active()).props("dense no-caps no-wrap") if len(names) <= 4 else \
            ui.select(names, label=c.name, value=active()).classes("w-64")
        ui.label(f"choose: {c.name}").classes("text-xs text-gray-500")

        @ui.refreshable
        def case_body() -> None:
            case = next((x for x in cases if x.name == sel.value), None)
            if case:
                with ui.column().classes("w-full gap-2"):
                    render_children(case, data, lookup, touch)

        def switched(e) -> None:
            for case in cases:
                if case.name != e.value:
                    for ch in data_children(case):
                        data.pop(ch.name, None)
            case_body.refresh()
            touch()

        sel.on_value_change(switched)
        case_body()
