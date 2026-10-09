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
 --tx:#0a0f1a;--mut:#1c2533;--acc:#2f5fd0;--dan:#b0412f;--on-acc:#fff;
 --nav-tx:#0a0f1a;--nav-on:#eaf1ff;--nav-on-tx:#0f2238;--hover:#f3f6fb;
 --ok:#0b7a4b;--ok-bg:#e3f5ec;--err:#c8321f;--err-bg:#fdeae6;--wa:#9a6700;--wa-bg:#fff3d1;
 --add:#0a6b3d;--add-bg:#e6f6ec;--del:#a52b1b;--del-bg:#fdecea;
 --font:'Manrope',system-ui,sans-serif;--mono:'JetBrains Mono',ui-monospace,Menlo,monospace;
 --field-bg:#fff;--code-bg:#f6f8fa;--readonly-bg:#f4f6f8;--mark:#ffe58a;--pop-shadow:#14233a33;
 --header-h:60px;--r:12px;--r-s:8px;--shadow:0 1px 2px #14233a0d,0 4px 14px #14233a0a}

body,.q-page,.q-layout{background:var(--bg)!important;color:var(--tx);font:15px/1.5 var(--font)}
.q-btn,.q-field,.q-item,.q-table,.q-tab,.q-expansion-item,.q-badge,.q-tooltip,.q-menu,.q-dialog,.q-notification,.q-toggle,.q-chip,.q-btn-toggle{font-family:var(--font)!important}
code,pre,.nicegui-code,.mono{font-family:var(--mono)!important;font-variant-numeric:tabular-nums}
.nicegui-code,.q-card pre{background:var(--code-bg)!important;color:var(--tx)!important;border-radius:8px}

/* page structure */
/* the content area (ui.sub_pages) must fill the page width; it otherwise shrinks to its content */
.nicegui-sub-pages{width:100%;min-width:0;display:flex;flex-direction:column;align-items:flex-start;gap:1rem}
.text-2xl{font-size:26px!important;line-height:1.2!important;font-weight:700!important;letter-spacing:-.02em;color:var(--tx)}
.text-lg{font-weight:600!important;color:var(--tx)}
.mut,.text-gray-300,.text-gray-400,.text-gray-500{color:var(--mut)!important}
.err-tx{color:var(--err)!important}.warn-tx{color:var(--wa)!important}.ok-tx{color:var(--ok)!important}
.line{border-color:var(--line)!important}
.alert-warn,.alert-err,.alert-ok{border-radius:10px;border:1px solid}.alert-warn{background:var(--wa-bg);color:var(--wa);border-color:var(--wa)}.alert-err{background:var(--err-bg);color:var(--err);border-color:var(--err)}.alert-ok{background:var(--ok-bg);color:var(--ok);border-color:var(--ok)}
.fill-col{align-self:stretch}.fill-field,.fill-field .q-field__inner,.fill-field .q-field__control{height:100%}.fill-field textarea{height:100%!important;resize:none}
.bg-page{background:var(--bg)}
.err-box{background:var(--err-bg);color:var(--err);border-radius:8px;padding:8px 12px}
.q-header{height:var(--header-h)!important;min-height:var(--header-h)!important;background:var(--panel)!important;color:var(--tx)!important;border:0!important;box-shadow:none!important}
.q-drawer{background:var(--side)!important;border:0!important;box-shadow:none!important}
.q-drawer--bordered,.q-drawer--left.q-drawer--bordered{border:0!important}

/* icons: light outlined style everywhere; the selected menu item uses the filled glyph */
.q-icon.material-icons{font-family:'Material Icons Outlined'!important}
.q-drawer .q-item--active .q-icon.material-icons{font-family:'Material Icons'!important}

/* row action icons: quiet by default, colour on hover */
.row-actions .q-btn{color:var(--tx)!important}
.row-actions .q-btn:hover{color:var(--acc)!important;background:var(--hover)}
.row-actions .q-btn.act-commit:hover{color:var(--err)!important;background:var(--err-bg)}
.row-actions .q-btn.act-del:hover{color:var(--err)!important;background:var(--err-bg)}

/* menu */
.menu-heading{font-size:12px;font-weight:700;color:var(--mut);padding:16px 17px 4px;letter-spacing:.01em}
.menu-divider{margin:8px 12px;background:var(--line)}
.q-drawer .q-item{font-size:15px;font-weight:500;color:var(--nav-tx);min-height:42px;padding-left:17px;padding-right:0}
/* icon column: every icon (menu button included) is centred on x=28, expanded or folded (mini width 56) */
.q-drawer .q-item__section--avatar{min-width:0!important;width:22px;padding-right:0}
.q-drawer .q-item__section--main{margin-left:17px}
.q-drawer--mini .q-item{padding-left:0!important;justify-content:center}
.q-header .nav-burger,.q-header .nav-burger .q-icon{color:var(--tx)!important}
.q-header .nav-burger{margin-left:10px;width:36px;height:36px;min-height:0}
.q-drawer .q-item .q-icon{font-size:22px;color:var(--nav-tx)}
.q-drawer .q-item:hover{background:var(--hover)}
/* selected item is a tab: same colour as the page, full width, merges into the page edge */
.q-drawer .q-item{border-radius:0}
.q-drawer .q-item--active{background:var(--bg)!important;color:var(--nav-on-tx);box-shadow:inset 3px 0 0 var(--acc)}
.q-drawer .q-item--active .q-icon{color:var(--acc)}

/* buttons */
.q-btn{text-transform:none!important;font-weight:500;border-radius:var(--r-s)!important}
.q-btn.q-btn--outline{background:var(--field-bg);color:var(--tx)!important}
.q-btn.q-btn--outline:before{border-color:var(--line-2)!important}
.q-btn.q-btn--outline .q-btn__content,.q-btn.q-btn--outline .q-btn__content .q-icon{color:var(--tx)!important}
.q-btn.q-btn--outline .q-icon{color:var(--nav-tx)!important}
/* tonal buttons: inside a layer that precedes Quasar's own !important utilities (unlayered !important rules lose to them) */
@layer overrides{
.q-btn.bg-primary,.q-btn.bg-negative{box-shadow:inset 0 0 0 1px var(--tone-line)!important}
.q-btn.bg-primary{--tone:var(--acc);--tone-line:color-mix(in srgb,var(--acc) 38%,transparent);background:color-mix(in srgb,var(--acc) 15%,var(--panel))!important;color:var(--acc)!important}
.q-btn.bg-negative{--tone:var(--dan);--tone-line:color-mix(in srgb,var(--dan) 42%,transparent);background:color-mix(in srgb,var(--dan) 14%,var(--panel))!important;color:var(--dan)!important}
.q-btn.bg-primary:hover{background:color-mix(in srgb,var(--acc) 24%,var(--panel))!important}
.q-btn.bg-negative:hover{background:color-mix(in srgb,var(--dan) 24%,var(--panel))!important}
.q-btn.bg-primary .q-btn__content,.q-btn.bg-negative .q-btn__content,.q-btn.bg-primary .q-icon,.q-btn.bg-negative .q-icon{color:inherit!important}
}
.q-btn.q-btn--flat:not(.q-btn--round):hover{background:var(--hover)}
.q-btn.q-btn--flat:not(.q-btn--round),.q-btn.q-btn--flat:not(.q-btn--round) .q-btn__content,.q-btn.q-btn--flat:not(.q-btn--round) .q-icon{color:var(--tx)!important}
/* segmented toggles (QBtnToggle = q-btn-group of q-btn; the selected one has bg-primary) */
.q-btn-group{border:1px solid var(--line-2);border-radius:var(--r-s)!important;overflow:hidden;box-shadow:none}
.q-btn-group .q-btn{border-radius:0!important}
.q-btn-group .q-btn:not(.bg-primary),.q-btn-group .q-btn:not(.bg-primary) .q-btn__content{color:var(--nav-tx)!important}
.q-btn-group .q-btn:not(.bg-primary){background:var(--field-bg)}
.q-btn-group .q-btn.bg-primary{box-shadow:none!important;border-radius:0!important}

/* surfaces */
.q-card,.q-table__card{background:var(--panel)!important;color:var(--tx);border:1px solid var(--line);border-radius:var(--r)!important;box-shadow:var(--shadow)!important}
.q-dialog .q-card{box-shadow:0 12px 40px var(--pop-shadow)!important}
.q-expansion-item{border-radius:var(--r-s)}
.q-expansion-item .q-item{color:var(--tx)}
.q-expansion-item .q-item .q-icon{color:var(--mut)}
.q-separator{background:var(--line)}

/* tables */
.q-table th{color:var(--tx)!important;font-weight:700!important;font-size:13px;border-color:var(--line)!important;background:transparent}
.q-table td{border-color:var(--line)!important;color:var(--tx)}
.q-table tbody tr:hover td{background:var(--hover)!important}
.q-table tbody td.mono{font-size:12.5px;color:var(--tx)}
.q-table tbody td.name{font-weight:600}
.q-table__bottom{color:var(--mut);border-color:var(--line)!important}

/* tables that scroll inside a fixed-height card: header stays visible */
.sticky-head.q-table--flat,.sticky-head{display:flex;flex-direction:column}
.sticky-head .q-table__middle{flex:1;min-height:0}
.sticky-head thead tr th{position:sticky;z-index:1;top:0;background:var(--panel)}

/* form fields */
.q-field--outlined .q-field__control{background:var(--field-bg);border-radius:var(--r-s)}
.q-field--outlined .q-field__control:before{border-color:var(--line-2)}
.q-field--outlined:not(.q-field--readonly) .q-field__control:hover:before{border-color:var(--mut)}
.q-field__label,.q-field__bottom{color:var(--mut)}
.q-field__native::placeholder,.q-field__input::placeholder{color:var(--mut)!important;opacity:1!important}
.q-field--outlined .q-field__native,.q-field--outlined .q-field__input{color:var(--tx)}
.q-tab{color:var(--tx)}
.q-table th{color:var(--tx)!important}
.q-field--readonly .q-field__control{background:var(--readonly-bg)}
.q-field--readonly .q-field__control:before{border-style:dashed}

/* status rail on the left edge of every data-table row + uniform row height */
.q-table tbody td{height:52px;padding-top:0;padding-bottom:0}
.q-table tbody tr td:first-child{box-shadow:inset 3px 0 0 transparent}
.q-table tbody tr.rail-ok td:first-child{box-shadow:inset 3px 0 0 var(--acc)}
.q-table tbody tr.rail-bad td:first-child{box-shadow:inset 3px 0 0 var(--err)}
.q-table tbody tr.rail-off td:first-child{box-shadow:inset 3px 0 0 var(--line-2)}
.q-table tbody tr.rail-warn td:first-child{box-shadow:inset 3px 0 0 var(--wa)}

.q-tree__node-header-content,.q-tree__node-header-content *{color:var(--tx)!important}
.q-tree__node--selected>.q-tree__node-header{background:var(--nav-on)!important}
.q-tree__node--selected>.q-tree__node-header .q-tree__node-header-content{font-weight:600}
.q-tree__node-header.q-tree__node--disabled,.q-tree__node--disabled{opacity:1!important}

/* mandatory field that is still empty: red border (removed from the element once it has a value) */
.q-field--outlined.req-empty .q-field__control:before,
.q-field--outlined.req-empty:not(.q-field--readonly) .q-field__control:hover:before{border-color:var(--err)!important}
.q-field--outlined.req-empty.q-field--focused .q-field__control:after{border-color:var(--err)!important}

.q-menu,.q-select__dialog,.q-dialog .q-card{background:var(--panel)!important;color:var(--tx)!important}
.q-menu .q-item{color:var(--tx)}
.q-menu .q-item:hover,.q-menu .q-item--active{background:var(--hover)}
body{--q-primary:var(--acc);--q-positive:var(--ok);--q-negative:var(--dan);--q-warning:var(--wa)}

/* status pills */
.pill{display:inline-flex;align-items:center;gap:6px;padding:2px 10px 2px 8px;border-radius:99px;font-size:12px;font-weight:600}
.pill:before{content:"";width:6px;height:6px;border-radius:50%;background:currentColor}
.pill-OPEN{background:var(--ok-bg);color:var(--ok)}.pill-CLOSED{background:var(--err-bg);color:var(--err)}.pill-DISABLED{background:var(--line);color:var(--mut)}.pill-other{background:var(--wa-bg);color:var(--wa)}
/* Dark mode: follows the operating system / browser setting */
@media (prefers-color-scheme: dark){
:root{
 --bg:#0f1a22;--panel:#15232e;--side:#0b141b;--line:#243645;--line-2:#35495a;
 --tx:#eef3f7;--mut:#d3dde5;--acc:#9db7ff;--dan:#ffa797;--on-acc:#06122e;
 --nav-tx:#eef3f7;--nav-on:#1d3447;--nav-on-tx:#ffffff;--hover:#1a2c3a;
 --ok:#5fd3a0;--ok-bg:#123a2c;--err:#ff8a7a;--err-bg:#43211d;--wa:#f5c05a;--wa-bg:#3d3012;
 --add:#7ee0a8;--add-bg:#0f3022;--del:#ff9d90;--del-bg:#3b1b19;
 --field-bg:#101c26;--code-bg:#0d1821;--readonly-bg:#162531;--mark:#6b5a10;--pop-shadow:#00000099;
 --shadow:0 1px 2px #00000040,0 4px 14px #00000033}
}
"""

DIFF_CSS = """
.confbody{font:12.5px/1.6 var(--mono);background:var(--field-bg);border:1px solid var(--line);border-radius:8px;padding:8px 0;overflow:auto;height:62vh;white-space:pre}
.cl{padding:0 14px 0 0}.cl:hover{background:var(--hover)}
.ln{display:inline-block;width:5ch;margin-right:14px;text-align:right;color:var(--mut);user-select:none}
.confbody mark{background:var(--mark);color:inherit;border-radius:2px}
.dl{white-space:pre;padding:0 12px;min-height:1.5em;border-left:3px solid transparent}
.dl-add{background:var(--add-bg);color:var(--add);border-left-color:var(--add)}
.dl-del{background:var(--del-bg);color:var(--del);border-left-color:var(--del)}
.dl-ctx{color:var(--mut)}
.dbody{font:12.5px/1.5 var(--mono);background:var(--field-bg);border:1px solid var(--line);border-radius:8px;overflow:auto;max-height:50vh;padding:6px 0}
"""


def apply() -> None:
    """Install the theme on the current page."""
    ui.colors(primary="#2f5fd0", positive="#0b7a4b", negative="#b0412f", warning="#9a6700",
              secondary="#4a5a6e", info="#2f5fd0", accent="#2f5fd0")
    ui.dark_mode(None)  # follow the system light/dark setting (Quasar components); our own tokens use prefers-color-scheme
    ui.add_css(CSS)
    ui.add_css(DIFF_CSS)
