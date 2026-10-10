import argparse
import os
import sys
from pathlib import Path

from nicegui import app, background_tasks, ui

from . import accounts, auth_views, connection, db, device_views, network_views, rpc_views, service_views, shell, views  # noqa: F401  (shell registers the page)
from .client import ClixonClient


STATIC = Path(__file__).parent / "static"


def main() -> None:
    p = argparse.ArgumentParser(prog="clixon-ui", description="Web UI for the Clixon controller (RESTCONF)")
    p.add_argument("url", nargs="?", default=os.environ.get("CLIXON_URL"), help="controller URL, e.g. https://localhost:1443")
    p.add_argument("--insecure", "-k", action="store_true", default=os.environ.get("CLIXON_INSECURE", "").lower() in ("1", "true", "yes"),
                   help="do not verify TLS certificate (self-signed); also CLIXON_INSECURE=1")
    p.add_argument("--host", default=os.environ.get("CLIXON_UI_HOST", "127.0.0.1"))
    p.add_argument("--port", type=int, default=int(os.environ.get("CLIXON_UI_PORT", "8080")))
    p.add_argument("--database-url", default=os.environ.get("CLIXON_UI_DATABASE_URL", db.default_url()),
                   help="user database: sqlite:///path/file.db or postgresql://user:pw@host/dbname (needs the 'postgres' extra); "
                        "also CLIXON_UI_DATABASE_URL")
    p.add_argument("--send-user", action="store_true", default=os.environ.get("CLIXON_UI_SEND_USER", "").lower() in ("1", "true", "yes"),
                   help="send the signed-in user name to the controller as X-Forwarded-User and HTTP_AUTHORIZATION (needs nginx: "
                        "fastcgi_param REMOTE_USER $http_x_forwarded_user; transactions otherwise show 'anonymous'); also CLIXON_UI_SEND_USER=1")
    p.add_argument("--demo", action="store_true", default=os.environ.get("CLIXON_UI_DEMO", "").lower() in ("1", "true", "yes"),
                   help="run against a built-in fake controller with invented devices (no controller URL needed); any password signs in "
                        "as 'guest'; the data resets every 30 minutes; also CLIXON_UI_DEMO=1")
    p.add_argument("--reload", action="store_true",
                   help="development mode: restart the server and reload open browser pages when source files change")
    a = p.parse_args()
    if not a.url and not a.demo:
        p.error("controller URL required (argument or CLIXON_URL)")
    if a.reload and Path(sys.argv[0]).name != "_dev.py":
        # NiceGUI's reloader re-imports the main module and only works for a plain script, not an
        # installed entry point or `python -m`, so hand over to the small script next to this file.
        os.execv(sys.executable, [sys.executable, str(Path(__file__).parent / "_dev.py"), *sys.argv[1:]])
    db.init(a.database_url)
    with db.session() as s:
        if not a.demo:
            accounts.seed_admin(s)
        secret = os.environ.get("CLIXON_UI_SECRET") or db.secret_key(s)
    if a.demo:
        from . import demo
        views.client, views.demo_controller = demo.make_client()
        views.DEMO = True
        app.on_startup(lambda: background_tasks.create(demo.reset_loop(views.demo_controller), name="demo reset"))
        a.send_user = True  # transactions then show who acted (guest)
    else:
        views.client = ClixonClient(a.url, verify=not a.insecure)
    views.client.write_guard = auth_views.write_guard
    if a.send_user:
        views.client.user_provider = auth_views.current_username
    connection.install(views.client)
    app.on_startup(service_views.preload)
    ui.run(host=a.host, port=a.port, title="Clixon UI", favicon=STATIC / "img" / "favicon.png", reload=a.reload, show=False,
           **({"uvicorn_reload_dirs": str(Path(__file__).parent), "uvicorn_reload_includes": "*.py"} if a.reload else {}),
           storage_secret=secret)


if __name__ in {"__main__", "__mp_main__"}:
    main()
