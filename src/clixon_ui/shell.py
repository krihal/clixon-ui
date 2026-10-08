"""The one real page. It owns the header and the menu; the content area is a `ui.sub_pages` container, so
clicking a menu entry swaps only the content (no browser reload, header and menu stay put)."""

from pathlib import Path

from nicegui import app, ui

from . import device_views, inventory_views, network_views, rpc_views, service_views, views

# Must be registered before the catch-all page below, otherwise that page answers /static/... first.
app.add_static_files("/static", Path(__file__).parent / "static")
app.add_static_file(local_file=Path(__file__).parent / "static" / "img" / "favicon.png", url_path="/favicon.ico")

ROUTES = {
    "/": views.devices_page,
    "/devices/{name}": device_views.device_config_page,
    "/groups": inventory_views.groups_page,
    "/profiles": inventory_views.profiles_page,
    "/templates": inventory_views.templates_page,
    "/inventory/{kind}/form": inventory_views.inventory_form_page,
    "/services": service_views.services_overview,
    "/services/{qname}": service_views.service_type_page,
    "/services/{qname}/form": service_views.service_form_page,
    "/network": network_views.network_page,
    "/commit": views.commit_page,
    "/transactions": views.transactions_page,
    "/rpc": rpc_views.rpc_page,
    "/restconf": views.raw_page,  # raw RESTCONF console: reachable by URL, deliberately not in the menu
}


@ui.page("/")
@ui.page("/{_:path}")
async def shell():
    views.frame()
    ui.sub_pages(ROUTES)
