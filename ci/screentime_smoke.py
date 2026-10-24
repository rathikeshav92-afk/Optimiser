"""CI smoke test for the Windows-only parts of Screen Time. Prints a report and exits non-zero on failure."""

import sys
import time
import traceback

from pcoptimizer import screentime as st

failures = []


def check(name, fn):
    try:
        print(f"{name}: {fn()!r}")
    except Exception:
        failures.append(name)
        print(f"{name}: FAILED")
        traceback.print_exc(file=sys.stdout)


check("file description of python.exe", lambda: st._file_description(sys.executable) or "(empty)")
check("foreground app", st.foreground_app)
check("idle seconds", st.idle_seconds)
check("display kept on", st._display_required)
check("tracker command", st.tracker_command)


def autostart():
    ok, msg = st.enable_autostart()
    if not ok:
        raise RuntimeError(f"enable_autostart failed: {msg}")
    time.sleep(3)
    if not st.autostart_enabled():
        raise RuntimeError("task not found after registering")
    ok, msg = st.disable_autostart()
    if not ok:
        raise RuntimeError(f"disable_autostart failed: {msg}")
    if st.autostart_enabled():
        raise RuntimeError("task still present after unregistering")
    return "register / start / unregister ok"


check("autostart task", autostart)
print("FAILURES:", failures or "none")
sys.exit(1 if failures else 0)
