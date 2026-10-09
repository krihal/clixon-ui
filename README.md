# clixon-ui

A web UI for the [Clixon controller](https://clixon-docs.readthedocs.io/), built with [NiceGUI](https://nicegui.io).
It talks to the controller **only over RESTCONF**; there is no other dependency on the controller host.

## What it does

- **Dashboard**: device and service counts, what needs attention (uncommitted changes, closed devices, failed transactions, services not deployed).
- **Devices**: open, close, reconnect and pull (sync) one or many devices in parallel, show diffs, browse a device's configuration, add, edit, duplicate and delete devices.
- **Device groups, profiles, templates, RPC templates**: create, edit, duplicate and delete.
- **Services**: forms generated from the controller's YANG to create, edit, duplicate and delete service instances; "commit diff" (what would change on the devices, nothing pushed) and "commit" with a confirmation that shows the diff.
- **Diff / Commit**: pending changes, candidate vs running, device datastore comparisons.
- **RPC**: run device RPCs (templates or custom), list the RPCs each device offers, send CLI commands.
- **Network**: LLDP neighbour map on demand, with an IS-IS metrics overlay (Juniper).
- **Transactions**: history with details.

All writes go to the controller's candidate first. Nothing is committed or pushed to devices without a confirmation dialog.
The UI follows the system light/dark setting and works offline (fonts and icons are bundled).

## Requirements

- A Clixon controller with RESTCONF enabled (typically behind nginx, e.g. `https://controller:1443`).
- Python 3.14 and [uv](https://docs.astral.sh/uv/), **or** Docker.

## Run

```sh
uv run clixon-ui https://localhost:1443 -k      # -k: accept a self-signed certificate
```

Open <http://127.0.0.1:8080>.

| Option / variable | Default | Meaning |
|---|---|---|
| `url` / `CLIXON_URL` | required | Controller base URL |
| `-k`, `--insecure` / `CLIXON_INSECURE=1` | off | Do not verify the controller's TLS certificate |
| `--host` / `CLIXON_UI_HOST` | `127.0.0.1` | Address to listen on |
| `--port` / `CLIXON_UI_PORT` | `8080` | Port to listen on |
| `--reload` | off | Development: restart and reload the browser when `.py` files change |
| `CLIXON_UI_SECRET` | dev value | Secret that signs the browser session cookie; set your own in production |

## Docker

```sh
docker build -t clixon-ui .
docker run --rm -p 127.0.0.1:8080:8080 \
  -e CLIXON_URL=https://controller.example.net:1443 -e CLIXON_INSECURE=1 \
  -e CLIXON_UI_SECRET="$(openssl rand -hex 16)" clixon-ui
```

or `docker compose up -d --build` (edit `compose.yaml` first). If the controller runs on the Docker host, use
`https://host.docker.internal:1443` (Docker Desktop) or `--network host` on Linux.
The image runs as a non-root user and has a health check on `/`.

## Development

```sh
uv run pytest -q                                   # unit tests
uv run clixon-ui https://localhost:1443 -k --reload
```

`CLAUDE.md` describes the code layout, the RESTCONF behaviour of the controller learned along the way and the UI
conventions. Please read it before adding a page.

## Known limitations

- No authentication yet.
- Config templates cannot be applied to devices from the UI yet (they can be edited).
- Service forms do not offer device-group names for leafref fields.
- The Network map needs LLDP data from Juniper devices; other vendors need a discovery source added in `network.py`.
