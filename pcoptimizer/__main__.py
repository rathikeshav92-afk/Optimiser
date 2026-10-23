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
    parser.add_argument("--selftest", metavar="FILE", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)

    if args.selftest:
        return selftest(args.selftest)

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


def selftest(path: str) -> int:
    """Used by CI on the built .exe: check that every module (including the AI SDK) is bundled."""
    try:
        import importlib

        import anthropic

        for mod in ("ai_assistant", "backend", "benchmark", "games", "sensors", "services", "ui.app", "ui.pages"):
            importlib.import_module(f"pcoptimizer.{mod}")

        anthropic.Anthropic(api_key="sk-ant-selftest")
        result, code = f"ok anthropic {anthropic.__version__}", 0
    except Exception as exc:  # pragma: no cover - only reached when bundling is broken
        result, code = f"FAILED {exc!r}", 1
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(result)
    return code


if __name__ == "__main__":
    sys.exit(main())
