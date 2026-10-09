# clixon-ui

Web UI for the [Clixon controller](https://clixon-docs.readthedocs.io/) built on NiceGUI. It talks to the
controller **only over RESTCONF** (httpx). The controller URL is a command-line argument.

## Run, test

```
uv run clixon-ui https://localhost:1443 -k          # -k = accept the self-signed certificate
uv run clixon-ui https://localhost:1443 -k --reload # dev: restart + browser reload on .py changes
uv run pytest -q
```

- Defaults: `127.0.0.1:8080`; `--port`, `--host`; URL can come from `CLIXON_URL`. No authentication yet.
- Python 3.14, dependencies via `uv`. `--reload` re-execs into `src/clixon_ui/_dev.py` because NiceGUI's
  reloader needs a plain script (not an entry point or `python -m`).
- `.nicegui/` holds NiceGUI per-browser session storage written at runtime. It is not source; do not stage it.
- Never leave test servers running (port 8080 is the user's). Stop anything you start.

## Layout (`src/clixon_ui/`)

| File | Role |
|---|---|
| `__init__.py` | CLI entry (`main`), startup schema preload |
| `shell.py` | The only real page: header + menu once, `ui.sub_pages(ROUTES)` for the content; the route table lives here |
| `client.py` | `ClixonClient`: all RESTCONF calls. GET retries 502/503; readable errors |
| `dashboard.py` / `stats.py` | Landing page `/` (stat cards, devices/services/transactions panels, inventory buttons pinned at the bottom; refreshes every 10 s only when data changed) / pure numbers for it |
| `views.py` | `frame()` (header + foldable menu), Devices (now `/devices`), Diff/Commit, Transactions pages; grouped `MENU`, `reload_page()` |
| `service_views.py` | Services overview, instance table, create/edit/duplicate form, "commit diff" dialog |
| `device_views.py` | Device configuration viewer (`/devices/<name>`) |
| `rpc_views.py` | RPC page: Run (templates/custom), Available RPCs, CLI tabs |
| `network.py` | Pure: discovery sources (`SOURCES`), `Adjacency`, `build_graph`, `filter_graph`, `group_links`, `radial_positions` |
| `network_views.py` | Network page: user-triggered discovery, ECharts map, links table |
| `schema.py` | Loads the controller's YANG (`get-schema`), parses with pyang into `Node`/`YType` |
| `formdata.py` | Pure: RESTCONF JSON <-> form dict, validation, leafref lookup (RFC 7951 naming) |
| `forms.py` | Renders a form for a YANG node (containers, lists, choices, leaf-lists, leafrefs) |
| `confview.py` | Pure: device config JSON -> Junos-style text, outline tree |
| `rpcschema.py` | Index of RPCs a device offers, parsed from its YANG (`get-device-schema`) |
| `rpcutil.py` | Pure: template variables, read-only checks, CLI command -> RPC request |
| `diffview.py` | Pure parser + renderer for per-device diffs |
| `tables.py` | `data_table()` shared table style; `page_column()`; row-rail expressions |
| `theme.py` | The whole look ("Daylight"): CSS variables + Quasar overrides. `style.py`: button sizes |

Pure modules (`formdata`, `confview`, `rpcutil`, `diffview`, `rpcschema.parse_module`) have unit tests in `tests/`.
Keep logic pure and testable; keep NiceGUI calls in the `*_views.py` / `forms.py` files.

## Controller / RESTCONF facts learned the hard way

- Operations: `POST /restconf/operations/clixon-controller:<rpc>` with `{"clixon-controller:input": {...}}`.
  `ClixonClient.rpc(name, **params)` takes `name` positionally, so an RPC input called `name` needs its own method
  (see `device_schemas`).
- RPCs return a `tid`; poll `/data/clixon-controller:transactions/transaction=<tid>` until `state == DONE`
  (`wait_transaction`). Results of device RPCs come from `device-rpc-result`. Streams/SSE are not usable (406).
- The controller's nginx answers **502 above ~15 parallel requests**. Cap concurrency (we use 4) and keep the retry.
- Candidate datastore is readable/writable at `/restconf/ds/ietf-datastores:candidate/...`. **PUT replaces the whole
  list entry**, so the hidden controller-managed `created` container must be sent back (`preserved` in the form page),
  otherwise the controller loses track of what a service created.
- Listing a whole list path (e.g. `.../services/l2c:l2c`) fails: lists need a key. Read the parent container.
- Service JSON: top-level list key is `module:name`; submodules map to their belongs-to module (`bgp`, not
  `bgp-customer`). Numbers: int64/uint64/decimal64 are strings in RFC 7951 JSON.
- **commit diff** = `controller-commit` with `actions=CHANGE|FORCE, push=NONE`, then `datastore-diff` of device
  `RUNNING` vs `ACTIONS`. `CHANGE` only runs services whose config changed; for an unchanged instance use `FORCE`
  with `service-instance=name[service-name='KEY']` (other syntaxes are rejected). Closed devices give no diff.
- A device's full config is ~32 MB (`get-device-config`, whole-device GET). Browse lazily: `device=X?depth=7&content=config`
  for the outline, then per-section paths **without** `depth` (`.../config/<root>/<module:section>/<list>=<key>`).
  `policy-options` alone is ~35 MB; the viewer refuses nodes over 4 MB and asks for a single entry.
- Network map: `get-lldp-interface-neighbors` with no arguments returns *all* LLDP neighbours of a device
  (`lldp-neighbors-information/lldp-neighbor-information[]`). Remote system names are FQDNs (`ptx-ac-2.sunet.se`):
  match managed devices on the first label. A link is seen from both ends; `build_graph` merges them
  ("confirmed") or keeps a "one side" link. Neighbours may announce a wrong/duplicate system name or only a
  chassis MAC, and lab loops show up as device-to-itself links, so loops and management ports are hidden by default.
- To support another vendor or RPC for the map, add a `DiscoverySource` to `network.SOURCES` (RPC body + parser to
  `Adjacency`); the page and graph code do not change. Discovery must stay user-triggered (never poll).
- Protocol data on the map is an *overlay* (`network.OVERLAYS`): a set of RPC bodies sent to every device plus a parser
  to `PortMetric`; `apply_overlay` attaches it to both ends of each LLDP link (a LAG member is matched through its
  aggregate, `Link.a_parent/b_parent`). IS-IS (Juniper): `get-isis-interface-information` (`metric-one` = level 1,
  `metric-two` = level 2; use the level the interface runs / the adjacency uses) + `get-isis-adjacency-information`
  (a single adjacency arrives as a dict, not a list). Link status: up / differs (the two ends disagree on the metric)
  / down / **unknown** (an adjacency query failed: never report that as "down") / none. To add another protocol, add an
  `Overlay`; the colouring in `network_views.graph_option` keys off the status names.
- Never run two discoveries at once while testing: overlapping RPCs on the same device make the controller answer
  errors, which looks like a bug in the overlay.
- NiceGUI `ui.echart(on_point_click=...)` raises `KeyError: 'value'` unless every node and edge in the series has a
  `value`. Theme colours for chart text are JS expressions (`':color'` keys) so they follow light/dark.
- Device RPC templates: `device-template-apply` with `type=RPC` and either `template` or `inline: {config: {...}}`.
  Junos CLI works as an inline RPC `{"command": "show version"}`; reply is structured data.
- Device RPC list: `get-device-schema` (detail=true per module). Junos has ~170 `junos-rpc-*` modules (~15 MB,
  ~6,400 RPCs). `get-*` RPCs are read-only, everything else asks for confirmation.

## Single-page structure (read before adding a page)

- Navigation is client-side: `shell.py` builds the header/menu once and a `ui.sub_pages` container swaps only the
  content. A menu click must never reload the browser page (test it: set `window.__marker` in JS, click, read it back).
- A page is a plain builder function (sync or async) registered in `shell.ROUTES`; **no** `@ui.page`, **no** `frame()`.
  Arguments are filled **by name** from path parameters (`/services/{qname}`) and query parameters (`?key=`).
  Path parameters arrive URL-encoded (`l2c%3Al2c`): `unquote()` them. Query parameters are already decoded.
- To add a menu entry add it to `views.MENU` (grouped by purpose) and to `shell.ROUTES`; `views.menu_route()` maps a
  URL to the highlighted entry.
- Use `ui.navigate.to("/path")` (client-side here). Never `ui.navigate.reload()`: use `views.reload_page()`, which
  rebuilds only the content. Background tasks that touch the UI need `context=client`.
- Elements created by a page (including `ui.timer`) are deleted when you leave it, so polling stops (verified).
- `app.add_static_files` must run **before** the catch-all page is registered (it is in `shell.py`), otherwise
  `/static/...` is answered by the page and returns 404.
- The sub-pages container is made full width in `theme.py` (`.nicegui-sub-pages`); keep it, or pages shrink to content.

## Service properties

- `services/properties` is an empty container in the controller YANG; service modules augment it (`bgp-peer`, `customer` list, ...). pyang does not always copy those augments into it (unprefixed `properties` step), so `Schema.properties()` reads the `augment` statements directly.
- Pages: cards under "Properties" on `/services`, form at `/service-properties/{qname}` (`service_views.property_form_page`). Container property = one PUT; list property = one PUT/DELETE per entry (`.../properties/mod:list=key`). Save goes to the candidate only; no commit button (services pick the values up on the next commit). Verified against a fake controller only.

## Access control (NACM, `nacm_views.py`)

- `/nacm` (menu "Access control"): one form for the whole `ietf-netconf-acm:nacm` container (`Schema.nacm()`). Save = PUT of the whole container to the candidate (`client.put_nacm`); Review/Commit reuse the inventory flows (`diff_datastores`, local commit, confirm dialog with a lock-out warning). Verified against the fake controller only.
- `formdata.validate` only checks mandatory leaves of the *selected* choice case (NACM `rule` has `path` mandatory in one case).

## Service commit / delete flows (`service_views.py`)

- **Commit** = `controller-commit source=candidate push=COMMIT` with `actions=FORCE` + `service-instance` for one
  instance, or `actions=CHANGE` for "all changed services". The controller commits the *whole candidate* afterwards, so
  `commit_flow` first runs the dry run (same as Commit diff), shows the per-device diff, and warns about other
  uncommitted service edits (`servicechanges.changed_instances`, candidate vs running). Nothing is sent without the
  user's confirmation; a pending form edit is applied to the candidate only for the dry run and reverted, and saved
  for real only after confirming.
- **Delete** offers "from candidate" (staged) or "Delete & commit" (`actions=DELETE` removes the service **and** its
  device configuration; asks a second time). There is **no undeploy** in the controller YANG: DELETE removes both the
  service and its device data, nothing removes device data while keeping the service committed.
- Handlers that await dialogs must capture `client = ui.context.client` first and call `views.reload_page(client)` /
  `views.navigate_to(path, client)` afterwards: their own slot may belong to a deleted dialog by then.
- Flows `await` their result dialog (so the page is not rebuilt under it) and `_dispose()` closed dialogs shortly after;
  a closed dialog left in the page can keep a click-blocking backdrop.
- Testing these needs a fake controller (never the real one): record `controller-commit` calls and assert that Cancel
  sends nothing and a confirmed commit sends exactly one with the right `actions`/`service-instance`.

## UI conventions (the user cares about these)

- **Buttons shown together have the same size.** Use `BTN` / `BTN_TOOLBAR` / `BTN_SM` from `style.py` via `.classes(...)`
  and `no-caps no-wrap`. Never hard-code widths. Icon-only buttons are exempt.
- **Cards in a grid are the same size** (fixed height, clamp long text).
- **All data tables look the same**: build them with `tables.data_table()` inside `page_column()`. They fill the page
  height, only the body scrolls, header sticky, 52 px rows, coloured rail on the left edge, rows updated in place.
- Pages that poll (`POLL_SECONDS` = 5) must update table rows in place and only when data changed. Never rebuild the
  table (scroll position must survive).
- No hard-coded colours in views: use the CSS variables / helper classes from `theme.py` (`mut`, `err-tx`, `warn-tx`,
  `err-box`, `line`, `bg-page`, pills). Icons are the outlined Material set (filled only for the selected menu item).
- Fonts are bundled (`static/fonts`): Manrope for UI, JetBrains Mono for data. The app must work offline.
- Slot templates inside tables emit with `$parent.$emit(...)`. With `virtual=True` tables (only the RPC list) the slot
  is one level deeper: `$parent.$parent.$emit(...)`. Avoid native event names (`copy`, `click`) as custom event names.
- Header height is one variable, `--header-h` in `theme.py`. Anything sized to the viewport must use
  `calc(100vh - var(--header-h) - ...)` (see `tables.FILL_HEIGHT`), never a hard-coded pixel offset.
- Menu icons/hamburger sit on one centre line (x = 28 px) expanded and folded; keep that when touching the drawer CSS.
- **Light and dark follow the system** (`prefers-color-scheme`): `theme.py` has one `:root` token set and a dark
  override block; `ui.dark_mode(None)` lets Quasar components follow too. Never hard-code a colour: add a token
  to both sets. Quasar utility classes with `!important` (`bg-white`, `text-dark`) beat our own `!important`
  rules, so avoid them (e.g. toggles use `color=transparent`) instead of fighting them.
- When editing the CSS string in `theme.py`, replace unique, complete rules, never slices found with `index()` on a
  selector fragment (that once deleted half the stylesheet). After editing, check `{` and `}` counts are equal and
  look at screenshots of both schemes.
- Primary/negative buttons are *tonal* (pale tint + coloured text, tokens `--acc`/`--dan`). Their rules live in `@layer overrides{}` in `theme.py`: NiceGUI puts Quasar's `!important` utilities in a later layer, and unlayered `!important` rules lose to them (that is why plain overrides silently did nothing).
- Do not add numbered step markers, all-caps labels or decorative gradients.

## Working agreements

- **Do not change live controller state without being asked.** Reading is fine. Writing the candidate, pulling,
  opening devices, committing/pushing and running non-`get-` RPCs/CLI all need the user's go-ahead. If a test must
  write, restore the exact previous content and verify (compare JSON before/after).
- Verify UI changes with screenshots: headless Chrome via Playwright (`uv run --with playwright python ...`,
  `channel="chrome"`), not by reading code. A small fake RESTCONF server (FastAPI) is handy when the real controller
  is down; serve `/restconf/data/clixon-controller:devices`, `...:transactions`, the YANG library and the candidate
  services snapshot.
- The controller is shared with other people: candidate/transactions may change while you work, and unrelated
  transactions or `ssh-users` edits are not yours.
- Scratch files go to the session scratchpad, not the repo.

## Not done / ideas

- Authentication (basic or client certificate) towards the controller and for the UI itself.
- Config templates (`device-template-apply` type CONFIG, the `deploy-*` templates) are not exposed.
- Service forms don't offer device-group names for leafrefs (the lookup tree only has devices), so those are free
  text. `ClixonClient.device_groups()` already reads them (`devices?content=config&depth=3`); wire it into
  `_load_lookup` in `service_views.py`.
- A README for end users (the file is empty).

## Inventory (devices, groups, profiles, templates, RPC templates)
- `inventory_views.py`: list pages (`/groups`, `/profiles`, `/templates?tab=rpc`), shared YANG form at `/inventory/{kind}/form?key=|copy=`, `delete_flow`. Devices page has Add device + row menu Edit/Duplicate/Delete.
- Writes go to the candidate (`client.inventory_put/delete`); take effect via `client.local_commit()` (`ietf-netconf:commit`, nothing pushed to devices). Always confirm with `diff_datastores()` first.
- Never PUT an existing device (wipes mounted `config`): `client.device_update` PUTs/DELETEs one top-level setting at a time. Read device entries with `depth=3`.
- After a device commit the UI offers Open/Reconnect.
- Verified only against the fake controller; not yet against a live controller.
