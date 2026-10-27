"""Windows toast notifications (the pop-ups in the bottom-right corner), without extra libraries."""

from __future__ import annotations

import base64
import subprocess
from xml.sax.saxutils import escape

from . import APP_NAME
from .platform_utils import IS_WINDOWS

# Toasts must name a registered app; Windows PowerShell's id is present on every Windows 10/11 PC.
_APP_ID = r"{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\WindowsPowerShell\v1.0\powershell.exe"


def toast_script(title: str, message: str) -> str:
    xml = (f"<toast><visual><binding template='ToastGeneric'><text>{escape(title)}</text>"
           f"<text>{escape(message)}</text><text placement='attribution'>{escape(APP_NAME)}</text>"
           "</binding></visual></toast>").replace("'", "''")
    return (
        "[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] "
        "| Out-Null;"
        "[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime] | Out-Null;"
        f"$x = New-Object Windows.Data.Xml.Dom.XmlDocument; $x.LoadXml('{xml}');"
        f"[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('{_APP_ID}')"
        ".Show([Windows.UI.Notifications.ToastNotification]::new($x))"
    )


def toast(title: str, message: str) -> bool:
    """Show a notification without waiting for it. Returns False where notifications aren't available."""
    if not IS_WINDOWS:
        return False
    encoded = base64.b64encode(toast_script(title, message).encode("utf-16-le")).decode()
    try:
        subprocess.Popen(["powershell.exe", "-NoProfile", "-NonInteractive", "-WindowStyle", "Hidden",
                          "-EncodedCommand", encoded], creationflags=0x08000000,  # CREATE_NO_WINDOW
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True
    except OSError:
        return False


def notify_problems(messages: list[str]) -> bool:
    """One toast for everything that needs the user, so several problems don't flood the screen."""
    if not messages:
        return False
    if len(messages) == 1:
        return toast("Your PC needs attention", messages[0])
    return toast(f"{len(messages)} things need your attention",
                 "; ".join(messages[:3]) + (" ..." if len(messages) > 3 else "") + ". Open PC Optimizer to fix them.")
