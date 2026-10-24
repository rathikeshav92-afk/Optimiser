"""Main window: sidebar navigation plus the page container."""

from __future__ import annotations

import queue
import threading
import tkinter as tk
import traceback
from typing import Callable

from .. import APP_NAME, __version__
from ..backend import Backend
from . import pages
from . import theme as T

NAV = [
    ("dashboard", "Dashboard", "⌂", pages.DashboardPage),
    ("assistant", "AI Assistant", "✧", pages.AssistantPage),
    ("screentime", "Screen Time", "◷", pages.ScreenTimePage),
    ("gaming", "Gaming Mode", "▶", pages.GamingPage),
    ("games", "Game Profiles", "✦", pages.GamesPage),
    ("balanced", "Balanced Mode", "◐", pages.BalancedPage),
    ("tweaks", "Speed Up PC", "⚙", pages.TweaksPage),
    ("services", "Services", "☰", pages.ServicesPage),
    ("cleaner", "Cleaner & RAM", "♲", pages.CleanerPage),
    ("commands", "CMD Toolkit", "❯", pages.CommandsPage),
    ("benchmark", "Benchmark", "⏱", pages.BenchmarkPage),
    ("restore", "Backup & Restore", "↺", pages.RestorePage),
]


class App(tk.Tk):
    def __init__(self, backend: Backend):
        super().__init__()
        self.backend = backend
        self.specs = None
        self.rating = None
        self._q: queue.Queue[Callable[[], None]] = queue.Queue()
        self._busy = 0
        self._spec_listeners: list[Callable] = []

        self.title(f"{APP_NAME}{'  (demo)' if backend.demo else ''}")
        self.geometry("1180x800")
        self.minsize(980, 700)
        self.protocol("WM_DELETE_WINDOW", self._close)
        T.setup(self)

        self._build_sidebar()
        self.container = tk.Frame(self, bg=T.BG)
        self.container.pack(side="left", fill="both", expand=True)
        self.pages: dict[str, pages.Page] = {}
        for key, _, _, cls in NAV:
            page = cls(self.container, self)
            page.place(relx=0, rely=0, relwidth=1, relheight=1)
            self.pages[key] = page
        self.show("dashboard")
        self.after(40, self._drain)
        self.load_specs()

    # ------------------------------------------------------------------ layout
    def _build_sidebar(self):
        bar = tk.Frame(self, bg=T.SIDEBAR, width=220)
        bar.pack(side="left", fill="y")
        bar.pack_propagate(False)
        logo = tk.Frame(bar, bg=T.SIDEBAR)
        logo.pack(fill="x", padx=18, pady=(20, 18))
        tk.Label(logo, text="⚡", bg=T.SIDEBAR, fg=T.ACCENT, font=(T.FAMILY, 22)).pack(side="left")
        tk.Label(logo, text="PC Optimizer", bg=T.SIDEBAR, fg=T.TEXT, font=(T.FAMILY, 14, "bold")).pack(side="left", padx=6)
        self._nav_items: dict[str, tuple[tk.Frame, list[tk.Widget]]] = {}
        for key, label, icon, _ in NAV:
            row = tk.Frame(bar, bg=T.SIDEBAR, cursor="hand2")
            row.pack(fill="x", padx=10, pady=2)
            ic = tk.Label(row, text=icon, bg=T.SIDEBAR, fg=T.MUTED, font=(T.FAMILY, 12), width=2)
            ic.pack(side="left", padx=(8, 4), pady=7)
            tx = tk.Label(row, text=label, bg=T.SIDEBAR, fg=T.MUTED, font=(T.FAMILY, 10, "bold"))
            tx.pack(side="left")
            for w in (row, ic, tx):
                w.bind("<Button-1>", lambda _, k=key: self.show(k))
            self._nav_items[key] = (row, [row, ic, tx])

        foot = tk.Frame(bar, bg=T.SIDEBAR)
        foot.pack(side="bottom", fill="x", padx=18, pady=16)
        admin = self.backend.is_admin()
        tk.Label(foot, text="● Administrator" if admin else "● Limited (not admin)", bg=T.SIDEBAR,
                 fg=T.GOOD if admin else T.WARN, font=T.F_SMALL).pack(anchor="w")
        tk.Label(foot, text=f"v{__version__}", bg=T.SIDEBAR, fg=T.MUTED, font=T.F_SMALL).pack(anchor="w")
        self.busy_label = tk.Label(foot, text="", bg=T.SIDEBAR, fg=T.ACCENT, font=T.F_SMALL)
        self.busy_label.pack(anchor="w", pady=(6, 0))

    def _close(self):
        try:
            self.backend.shutdown()
        finally:
            self.destroy()

    def show(self, key: str):
        for k, (row, widgets) in self._nav_items.items():
            active = k == key
            for w in widgets:
                w.configure(bg=T.CARD_HI if active else T.SIDEBAR)
            widgets[1].configure(fg=T.ACCENT if active else T.MUTED)
            widgets[2].configure(fg=T.TEXT if active else T.MUTED)
        page = self.pages[key]
        page.tkraise()
        page.on_show()

    # ------------------------------------------------------------------ threading
    def ui(self, fn: Callable[[], None]) -> None:
        """Schedule ``fn`` on the Tk main thread (safe to call from workers)."""
        self._q.put(fn)

    def _drain(self):
        try:
            while True:
                self._q.get_nowait()()
        except queue.Empty:
            pass
        except Exception:
            traceback.print_exc()
        self.after(40, self._drain)

    def bg(self, work: Callable, done: Callable | None = None, error: Callable | None = None, label: str = "Working"):
        """Run ``work`` on a worker thread, then ``done(result)`` back on the UI thread."""
        self._set_busy(+1, label)

        def runner():
            try:
                result = work()
            except Exception as exc:
                traceback.print_exc()
                # Bind exc now: Python clears the name when the except block ends.
                self.ui(lambda e=exc: (self._set_busy(-1), error(e) if error else None))
                return
            self.ui(lambda: (self._set_busy(-1), done(result) if done else None))

        threading.Thread(target=runner, daemon=True).start()

    def _set_busy(self, delta: int, label: str = "Working"):
        self._busy = max(0, self._busy + delta)
        self.busy_label.configure(text=f"⌛ {label}..." if self._busy and delta > 0 else
                                  ("⌛ Working..." if self._busy else ""))

    def logger(self, box) -> Callable[[str], None]:
        return lambda line: self.ui(lambda: box.write(line))

    # ------------------------------------------------------------------ specs
    def on_specs(self, fn: Callable) -> None:
        self._spec_listeners.append(fn)
        if self.specs is not None:
            fn(self.specs, self.rating)

    def load_specs(self):
        from ..rating import rate

        def work():
            specs = self.backend.specs()
            return specs, rate(specs)

        def done(result):
            self.specs, self.rating = result
            for fn in self._spec_listeners:
                fn(self.specs, self.rating)

        self.bg(work, done, label="Reading hardware")
