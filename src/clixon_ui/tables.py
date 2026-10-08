"""One table style for every data table: fills the available height, scrolls inside, sticky header,
status rail on the left edge of each row, uniform row height."""

from __future__ import annotations

from nicegui import ui

# Row rail colour (see theme.py): rail-ok / rail-bad / rail-warn, chosen by a JS expression on `row`.
RAIL_DEVICE = "row['conn-state'] == 'OPEN' ? 'rail-ok' : row['conn-state'] == 'CLOSED' ? 'rail-bad' : 'rail-warn'"
RAIL_TRANSACTION = ("row.result == 'SUCCESS' ? 'rail-ok' : (row.result == 'FAILED' || row.result == 'ERROR') ? 'rail-bad' : 'rail-warn'")

RAIL_SERVICE = "row.status == 'Deployed' ? 'rail-ok' : 'rail-warn'"  # has the service script written device config yet?

RAIL_LINK = "row.status == 'one side' ? 'rail-warn' : 'rail-ok'"  # a link only one end reported is less certain
RAIL_RPC = "row.type == 'read' ? 'rail-ok' : 'rail-warn'"  # read-only vs may change the device

FILL_HEIGHT = "calc(100vh - var(--header-h) - 32px)"  # viewport minus header and page padding


def page_column() -> ui.column:
    """Page-height column: the page itself never scrolls, only tables inside it."""
    return ui.column().classes("w-full gap-2 no-wrap").style(f"height:{FILL_HEIGHT}")


def data_table(columns: list[dict], rows: list[dict], row_key: str, rail: str, height: str | None = None, virtual: bool = False, **kwargs) -> ui.table:
    """height=None fills the remaining page height; otherwise a fixed CSS height (for tables inside a longer page)."""
    t = ui.table(columns=columns, rows=rows, row_key=row_key, pagination={"rowsPerPage": 0}, **kwargs)
    # virtual scrolling only for very long tables: it nests the cell slots one component deeper, so
    # slot templates must then emit with $parent.$parent instead of $parent
    t.props("hide-bottom" + (" virtual-scroll" if virtual else ""))
    t.classes("w-full cursor-pointer sticky-head" + ("" if height else " grow")).style(f"min-height:0;height:{height}" if height else "min-height:0")
    t.props(f':table-row-class-fn="row => {rail}"')
    return t
