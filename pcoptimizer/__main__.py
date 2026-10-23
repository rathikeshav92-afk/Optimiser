"""Entry point: ``python -m pcoptimizer [--demo]``."""

from __future__ import annotations

import argparse
import sys

from . import APP_NAME
from .platform_utils import IS_WINDOWS, is_admin, relaunch_as_admin


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="pcoptimizer", description=f"{APP_NAME} - speed up your Windows PC")
    parser.add_argument("--demo", action="store_true", help="preview the UI with sample data (changes nothing)")
    parser.add_argument("--no-elevate", action="store_true", help="don't ask for administrator rights")
    args = parser.parse_args(argv)

    if not args.demo:
        if not IS_WINDOWS:
            print(f"{APP_NAME} only changes Windows PCs. Use --demo to preview the interface.")
            return 1
        if not args.no_elevate and not is_admin() and relaunch_as_admin():
            return 0  # the elevated copy takes over

    if IS_WINDOWS:
        import ctypes

        try:  # crisp text on high-DPI screens
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except (AttributeError, OSError):
            pass

    from .backend import Backend, DemoBackend
    from .ui.app import App

    app = App(DemoBackend() if args.demo else Backend())
    app.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
