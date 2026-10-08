"""Visual theme: "Daylight" — cool paper white, ink-blue text, one cobalt accent.

All colours, fonts and radii are CSS variables on :root so a second theme only needs new values.
Semantic helper classes (.mut, .err-tx, .warn-tx, .ok-tx, .line, .err-box, .bg-page) replace
hard-coded colours in the views.
"""

from nicegui import ui

CSS = """
@font-face{font-family:'Manrope';font-style:normal;font-weight:200 800;font-display:swap;src:url(/static/fonts/Manrope-latin.woff2) format('woff2')}
@font-face{font-family:'JetBrains Mono';font-style:normal;font-weight:400 500;font-display:swap;src:url(/static/fonts/JetBrainsMono-latin.woff2) format('woff2')}
:root{
 --bg:#f4f6f8;--panel:#fff;--side:#fff;--line:#e3e8ee;--line-2:#cdd5df;
 --tx:#14233a;--mut:#66758a;--acc:#1f5eff;--on-acc:#fff;
 --nav-tx:#4a5a6e;--nav-on:#eaf1ff;--nav-on-tx:#0f2238;--hover:#f3f6fb;
 --ok:#0b7a4b;--ok-bg:#e3f5ec;--err:#c8321f;--err-bg:#fdeae6;--wa:#9a6700;--wa-bg:#fff3d1;
 --add:#0a6b3d;--add-bg:#e6f6ec;--del:#a52b1b;--del-bg:#fdecea;
 --font:'Manrope',system-ui,sans-serif;--mono:'JetBrains Mono',ui-monospace,Menlo,monospace;
 --r:12px;--r-s:8px;--shadow:0 1px 2px #14233a0d,0 4px 14px #14233a0a}

body,.q-page,.q-layout{background:var(--bg)!important;color:var(--tx);font:15px/1.5 var(--font)}
.q-btn,.q-field,.q-item,.q-table,.q-tab,.q-expansion-item,.q-badge,.q-tooltip,.q-menu,.q-dialog,.q-notification,.q-toggle,.q-chip,.q-btn-toggle{font-family:var(--font)!important}
code,pre,.nicegui-code,.mono{font-family:var(--mono)!important;font-variant-numeric:tabular-nums}
.nicegui-code,.q-card pre{background:#f6f8fa!important;color:var(--tx)!important;border-radius:8px}

/* page structure */
.text-2xl{font-size:26px!important;line-height:1.2!important;font-weight:700!important;letter-spacing:-.02em;color:var(--tx)}
.text-lg{font-weight:600!important;color:var(--tx)}
.mut,.text-gray-300,.text-gray-400,.text-gray-500{color:var(--mut)!important}
.err-tx{color:var(--err)!important}.warn-tx{color:var(--wa)!important}.ok-tx{color:var(--ok)!important}
.line{border-color:var(--line)!important}
.bg-page{background:var(--bg)}
.err-box{background:var(--err-bg);color:var(--err);border-radius:8px;padding:8px 12px}
.q-header{background:var(--panel)!important;color:var(--tx)!important;border-bottom:1px solid var(--line);box-shadow:none}
.q-drawer{background:var(--side)!important;border-right:1px solid var(--line)}
.q-drawer--bordered{border-right:1px solid var(--line)!important}

/* menu */
.q-drawer .q-item{font-size:15px;font-weight:500;color:var(--nav-tx);border-radius:var(--r-s);min-height:38px}
.q-drawer .q-item .q-icon{font-size:22px;color:var(--nav-tx)}
.q-drawer .q-item:hover{background:var(--hover)}
.q-drawer .q-item--active{background:var(--nav-on);color:var(--nav-on-tx)}
.q-drawer .q-item--active .q-icon{color:var(--acc)}

/* buttons */
.q-btn{text-transform:none!important;font-weight:500;border-radius:var(--r-s)!important}
.q-btn.q-btn--outline{background:#fff;color:var(--tx)!important}
.q-btn.q-btn--outline:before{border-color:var(--line-2)!important}
.q-btn.q-btn--outline .q-btn__content,.q-btn.q-btn--outline .q-btn__content .q-icon{color:var(--tx)!important}
.q-btn.q-btn--outline .q-icon{color:var(--nav-tx)!important}
.q-btn.bg-primary{color:var(--on-acc)!important}
.q-btn.q-btn--flat:not(.q-btn--round):hover{background:var(--hover)}
.q-btn-toggle{border:1px solid var(--line-2);border-radius:var(--r-s)!important;overflow:hidden}
.q-btn-toggle .q-btn{background:#fff;color:var(--nav-tx)!important;border-radius:0!important}
.q-btn-toggle .q-btn.bg-primary{background:var(--acc)!important;color:#fff!important}

/* surfaces */
.q-card,.q-table__card{background:var(--panel)!important;color:var(--tx);border:1px solid var(--line);border-radius:var(--r)!important;box-shadow:var(--shadow)!important}
.q-dialog .q-card{box-shadow:0 12px 40px #14233a33!important}
.q-expansion-item{border-radius:var(--r-s)}
.q-expansion-item .q-item{color:var(--tx)}
.q-expansion-item .q-item .q-icon{color:var(--mut)}
.q-separator{background:var(--line)}

/* tables */
.q-table th{color:var(--tx)!important;font-weight:700!important;font-size:13px;border-color:var(--line)!important;background:transparent}
.q-table td{border-color:var(--line)!important;color:var(--tx)}
.q-table tbody tr:hover td{background:var(--hover)!important}
.q-table tbody td.mono{font-size:12.5px;color:var(--mut)}
.q-table tbody td.name{font-weight:600}
.q-table__bottom{color:var(--mut);border-color:var(--line)!important}

/* form fields */
.q-field--outlined .q-field__control{background:#fff;border-radius:var(--r-s)}
.q-field--outlined .q-field__control:before{border-color:var(--line-2)}
.q-field--outlined:not(.q-field--readonly) .q-field__control:hover:before{border-color:var(--mut)}
.q-field__label,.q-field__bottom{color:var(--mut)}
.q-field--readonly .q-field__control{background:#f4f6f8}
.q-field--readonly .q-field__control:before{border-style:dashed}

/* device rows: status rail */
.q-table tbody tr td:first-child{box-shadow:inset 3px 0 0 transparent}
.q-table tbody tr.st-OPEN td:first-child{box-shadow:inset 3px 0 0 var(--acc)}
.q-table tbody tr.st-CLOSED td:first-child{box-shadow:inset 3px 0 0 var(--err)}
.q-table tbody tr.st-CONNECTING td:first-child,.q-table tbody tr[class*="st-"]:not(.st-OPEN):not(.st-CLOSED) td:first-child{box-shadow:inset 3px 0 0 var(--wa)}

/* status pills */
.pill{display:inline-flex;align-items:center;gap:6px;padding:2px 10px 2px 8px;border-radius:99px;font-size:12px;font-weight:600}
.pill:before{content:"";width:6px;height:6px;border-radius:50%;background:currentColor}
.pill-OPEN{background:var(--ok-bg);color:var(--ok)}.pill-CLOSED{background:var(--err-bg);color:var(--err)}.pill-other{background:var(--wa-bg);color:var(--wa)}
"""

DIFF_CSS = """
.confbody{font:12.5px/1.6 var(--mono);background:#fff;border:1px solid var(--line);border-radius:8px;padding:8px 0;overflow:auto;height:62vh;white-space:pre}
.cl{padding:0 14px 0 0}.cl:hover{background:var(--hover)}
.ln{display:inline-block;width:5ch;margin-right:14px;text-align:right;color:var(--mut);opacity:.7;user-select:none}
.confbody mark{background:#ffe58a;color:inherit;border-radius:2px}
.dl{white-space:pre;padding:0 12px;min-height:1.5em;border-left:3px solid transparent}
.dl-add{background:var(--add-bg);color:var(--add);border-left-color:var(--add)}
.dl-del{background:var(--del-bg);color:var(--del);border-left-color:var(--del)}
.dl-ctx{color:var(--mut)}
.dbody{font:12.5px/1.5 var(--mono);background:#fff;border:1px solid var(--line);border-radius:8px;overflow:auto;max-height:50vh;padding:6px 0}
"""


def apply() -> None:
    """Install the theme on the current page."""
    ui.colors(primary="#1f5eff", positive="#0b7a4b", negative="#c8321f", warning="#9a6700",
              secondary="#4a5a6e", info="#1f5eff", accent="#1f5eff")
    ui.dark_mode(False)
    ui.add_css(CSS)
    ui.add_css(DIFF_CSS)
