"""Entry script for `clixon-ui --reload` (NiceGUI auto-reload needs a plain script as the main module)."""

from clixon_ui import main

if __name__ in {"__main__", "__mp_main__"}:
    main()
