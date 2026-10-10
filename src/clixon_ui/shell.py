"""The one real page. It owns the header and the menu; the content area is a `ui.sub_pages` container, so
clicking a menu entry swaps only the content (no browser reload, header and menu stay put)."""

from pathlib import Path

from nicegui import app, ui

from . import auth_views, connection, dashboard, device_views, inventory_views, nacm_views, network_views, rpc_views, service_views, views

# Must be registered before the catch-all page below, otherwise that page answers /static/... first.
app.add_static_files("/static", Path(__file__).parent / "static")
app.add_static_file(local_file=Path(__file__).parent / "static" / "img" / "favicon.png", url_path="/favicon.ico")

ROUTES = {
    "/": dashboard.dashboard_page,
    "/devices": views.devices_page,
    "/devices/{name}": device_views.device_config_page,
    "/groups": inventory_views.groups_page,
    "/profiles": inventory_views.profiles_page,
    "/templates": inventory_views.templates_page,
    "/inventory/{kind}/form": inventory_views.inventory_form_page,
    "/services": service_views.services_overview,
    "/services/{qname}": service_views.service_type_page,
    "/services/{qname}/form": service_views.service_form_page,
    "/service-properties/{qname}": service_views.property_form_page,
    "/nacm": nacm_views.nacm_page,
    "/network": network_views.network_page,
    "/commit": views.commit_page,
    "/transactions": views.transactions_page,
    "/rpc": rpc_views.rpc_page,
    "/restconf": views.raw_page,  # raw RESTCONF console: reachable by URL (admins only), deliberately not in the menu
    "/account": auth_views.account_page,
    "/admin": auth_views.admin_page,
}
ROUTES = {path: auth_views.guarded(build) for path, build in ROUTES.items()}


@ui.page("/")
@ui.page("/{_:path}")
async def shell():
    user = auth_views.current_user()
    if user is None:
        ui.navigate.to("/login")
        return
    if user.must_change_password:
        auth_views.forced_password_page(user)
        return
    connection.register(ui.context.client)
    access = auth_views.accounts.access_for(user)
    if access.view_only:
        ui.query("body").classes("view-only")  # theme.py hides everything marked .wr (controls that write)
    views.frame(access)
    ui.sub_pages(ROUTES)
