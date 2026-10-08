"""One table style for every data table: fills the available height, scrolls inside, sticky header,
status rail on the left edge of each row, uniform row height."""

from __future__ import annotations

from nicegui import ui

# Row rail colour (see theme.py): rail-ok / rail-bad / rail-warn, chosen by a JS expression on `row`.
RAIL_DEVICE = "row['conn-state'] == 'OPEN' ? 'rail-ok' : row['conn-state'] == 'CLOSED' ? 'rail-bad' : 'rail-warn'"
RAIL_TRANSACTION = ("row.result == 'SUCCESS' ? 'rail-ok' : (row.result == 'FAILED' || row.result == 'ERROR') ? 'rail-bad' : 'rail-warn'")

RAIL_SERVICE = "row.status == 'Deployed' ? 'rail-ok' : 'rail-warn'"  # has the service script written device config yet?

FILL_HEIGHT = "calc(100vh - 80px)"  # viewport minus header and page padding


def page_column() -> ui.column:
    """Page-height column: the page itself never scrolls, only tables inside it."""
    return ui.column().classes("w-full gap-2 no-wrap").style(f"height:{FILL_HEIGHT}")


def data_table(columns: list[dict], rows: list[dict], row_key: str, rail: str, **kwargs) -> ui.table:
    t = ui.table(columns=columns, rows=rows, row_key=row_key, pagination={"rowsPerPage": 0}, **kwargs)
    t.props("virtual-scroll hide-bottom")
    t.classes("w-full cursor-pointer sticky-head grow").style("min-height:0")
    t.props(f':table-row-class-fn="row => {rail}"')
    return t
