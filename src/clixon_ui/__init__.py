import argparse
import os
import sys
from pathlib import Path

from nicegui import app, ui

from . import device_views, network_views, rpc_views, service_views, views
from .client import ClixonClient


STATIC = Path(__file__).parent / "static"


def main() -> None:
    p = argparse.ArgumentParser(prog="clixon-ui", description="Web UI for the Clixon controller (RESTCONF)")
    p.add_argument("url", nargs="?", default=os.environ.get("CLIXON_URL"), help="controller URL, e.g. https://localhost:1443")
    p.add_argument("--insecure", "-k", action="store_true", help="do not verify TLS certificate (self-signed)")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8080)
    p.add_argument("--reload", action="store_true",
                   help="development mode: restart the server and reload open browser pages when source files change")
    a = p.parse_args()
    if not a.url:
        p.error("controller URL required (argument or CLIXON_URL)")
    if a.reload and Path(sys.argv[0]).name != "_dev.py":
        # NiceGUI's reloader re-imports the main module and only works for a plain script, not an
        # installed entry point or `python -m`, so hand over to the small script next to this file.
        os.execv(sys.executable, [sys.executable, str(Path(__file__).parent / "_dev.py"), *sys.argv[1:]])
    views.client = ClixonClient(a.url, verify=not a.insecure)
    app.add_static_files("/static", STATIC)
    app.on_startup(service_views.preload)
    ui.run(host=a.host, port=a.port, title="Clixon UI", favicon=STATIC / "img" / "favicon.png", reload=a.reload, show=False,
           **({"uvicorn_reload_dirs": str(Path(__file__).parent), "uvicorn_reload_includes": "*.py"} if a.reload else {}),
           storage_secret=os.environ.get("CLIXON_UI_SECRET", "clixon-ui-dev"))


if __name__ in {"__main__", "__mp_main__"}:
    main()
