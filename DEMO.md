# Online demo

`clixon-ui --demo` runs the UI against a fake controller built into the process: three invented Juniper-style
routers (`demo-mx-1`, `demo-mx-2`, `demo-ptx-1`), a few services, LLDP/IS-IS data for the network map and canned RPC
replies. Nothing is contacted, so it is safe to put on the internet.

```
uv run clixon-ui --demo                 # http://127.0.0.1:8080, any user name and password signs in as "guest"
uv run clixon-ui --demo --port 8099
```

- No controller URL, no accounts: the login page accepts anything and signs in as `guest` (not an admin, may change
  things). There is no admin account in demo mode, so `/admin` and the raw `/restconf` console are unreachable.
- Everything lives in memory and is shared by all visitors. It resets to the initial data every 30 minutes, and
  anybody can press "Reset now" in the header.
- Service scripts do not run. "Commit diff" shows an invented interface block per device the service touches.
- The YANG in `src/clixon_ui/demo/yang` is the controller's own (get-schema), so the service forms are the real ones.
  Device YANG for the RPC list is a small hand-written subset in `device_yang/`.

## On a VPS

The repository's `Dockerfile` is the demo image too; demo mode is switched on by an environment variable:

```
docker build -t clixon-ui .
docker run -d --name clixon-demo --restart unless-stopped -e CLIXON_UI_DEMO=1 -p 127.0.0.1:8080:8080 clixon-ui
```

Put a TLS reverse proxy in front. NiceGUI needs WebSockets, which Caddy passes through without configuration:

```
demo.example.org {
    reverse_proxy 127.0.0.1:8080
}
```

With nginx add `proxy_http_version 1.1; proxy_set_header Upgrade $http_upgrade; proxy_set_header Connection "upgrade";`
and a long `proxy_read_timeout`.

## Where things are

| File | Role |
|---|---|
| `demo/controller.py` | the fake RESTCONF controller (FastAPI app served through `httpx.ASGITransport`) |
| `demo/netconf.py` | pure: edit-config XML -> edits on the JSON datastore (inverse of `netconfxml`) |
| `demo/seed.py` | all invented data and canned device replies |
| `tests/test_demo.py` | the fake controller exercised through the real `ClixonClient` |

To add a device, add it to `seed.DEVICES` and its links to `seed.LINKS`.
