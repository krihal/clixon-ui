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
| `__init__.py` | CLI entry (`main`), static files, startup schema preload |
| `client.py` | `ClixonClient`: all RESTCONF calls. GET retries 502/503; readable errors |
| `views.py` | `frame()` (header + foldable menu), Devices, Diff/Commit, Transactions pages; `MENU` |
| `service_views.py` | Services overview, instance table, create/edit/duplicate form, "commit diff" dialog |
| `device_views.py` | Device configuration viewer (`/devices/<name>`) |
| `rpc_views.py` | RPC page: Run (templates/custom), Available RPCs, CLI tabs |
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
- Device RPC templates: `device-template-apply` with `type=RPC` and either `template` or `inline: {config: {...}}`.
  Junos CLI works as an inline RPC `{"command": "show version"}`; reply is structured data.
- Device RPC list: `get-device-schema` (detail=true per module). Junos has ~170 `junos-rpc-*` modules (~15 MB,
  ~6,400 RPCs). `get-*` RPCs are read-only, everything else asks for confirmation.

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
