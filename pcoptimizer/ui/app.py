"""Main window: sidebar navigation, the page container, and Auto Care running in the background."""

from __future__ import annotations

import queue
import threading
import tkinter as tk
import traceback
from pathlib import Path
from tkinter import messagebox
from typing import Callable

from .. import APP_NAME, __version__, autocare
from ..backend import Backend
from . import pages
from . import theme as T
from .widgets import ScrollFrame, Spinner, Toast

NAV = [
    ("dashboard", "Home", "⌂", pages.DashboardPage),
    ("health", "PC Check-up", "✚", pages.HealthPage),
    ("tweaks", "Speed Up", "⚙", pages.TweaksPage),
    ("cleaner", "Clean Up", "♲", pages.CleanerPage),
    ("gaming", "Gaming Mode", "▶", pages.GamingPage),
    ("assistant", "Ask for Help", "✧", pages.AssistantPage),
    ("restore", "Undo Changes", "↺", pages.RestorePage),
    ("screentime", "Screen Time", "◷", pages.ScreenTimePage),
    ("games", "Game Profiles", "✦", pages.GamesPage),
    ("balanced", "Balanced Mode", "◐", pages.BalancedPage),
    ("drivers", "Driver Updates", "⇪", pages.DriversPage),
    ("apps", "Built-in Apps", "▦", pages.AppsPage),
    ("lookfeel", "Look & Feel", "◑", pages.LookFeelPage),
    ("services", "Services", "☰", pages.ServicesPage),
    ("commands", "Command Tools", "❯", pages.CommandsPage),
    ("benchmark", "Speed Test", "⏱", pages.BenchmarkPage),
]

# Everyday pages are always shown; expert tools sit under "More tools", closed by default.
MAIN = ["dashboard", "health", "tweaks", "cleaner", "gaming", "assistant", "restore"]
ADVANCED = ["screentime", "games", "balanced", "drivers", "apps", "lookfeel", "services", "commands", "benchmark"]

# Glyphs from the Windows icon font (Segoe Fluent Icons / MDL2 Assets), one consistent set.
NAV_ICONS = {
    "dashboard": "", "health": "", "assistant": "", "screentime": "",
    "gaming": "", "games": "", "balanced": "", "tweaks": "",
    "apps": "", "lookfeel": "", "services": "", "drivers": "",
    "cleaner": "", "commands": "", "benchmark": "", "restore": "",
}
LOGO_ICON = ""
MORE_ICON = ""
CHEVRON = {True: "", False: ""}  # down when open, right when closed

CARE_EVERY_MS = 30 * 60 * 1000  # re-check while the app is open


class App(tk.Tk):
    def __init__(self, backend: Backend):
        super().__init__()
        self.backend = backend
        self.specs = None
        self.rating = None
        self._q: queue.Queue[Callable[[], None]] = queue.Queue()
        self._busy: list[str] = []
        self._spec_listeners: list[Callable] = []
        self._care_listeners: list[Callable] = []
        self.care_report: autocare.CareReport | None = None
        self.care_running = False

        self.title(f"{APP_NAME}{'  (demo)' if backend.demo else ''}")
        self.geometry("1180x800")
        self.minsize(980, 700)
        self.protocol("WM_DELETE_WINDOW", self._close)
        try:
            self.iconbitmap(default=str(Path(__file__).with_name("app.ico")))
        except tk.TclError:
            pass  # no icon file (or not Windows): keep the default
        T.setup(self)

        self._classes = {key: cls for key, _, _, cls in NAV}
        self.pages: dict[str, pages.Page] = {}
        self._build_sidebar()
        self.container = tk.Frame(self, bg=T.BG)
        self.container.pack(side="left", fill="both", expand=True)
        self.toast = Toast(self.container)
        self.show("dashboard")
        self.bind_all("<Control-Tab>", lambda _: self._cycle(1))
        self.bind_all("<Control-Shift-Tab>", lambda _: self._cycle(-1))
        self.bind_all("<Control-ISO_Left_Tab>", lambda _: self._cycle(-1))
        self.after(40, self._drain)
        self.load_specs()
        # Make sure the background helper (which keeps Auto Care running when the app is closed) is set up.
        threading.Thread(target=self.backend.ensure_helper, daemon=True).start()

    # ------------------------------------------------------------------ layout
    def _build_sidebar(self):
        bar = tk.Frame(self, bg=T.SIDEBAR, width=236)
        bar.pack(side="left", fill="y")
        bar.pack_propagate(False)
        tk.Frame(self, bg=T.BORDER, width=1).pack(side="left", fill="y")
        self._icon_font = T.ICON_FONT
        logo = tk.Frame(bar, bg=T.SIDEBAR)
        logo.pack(fill="x", padx=20, pady=(22, 18))
        tk.Label(logo, text=LOGO_ICON if self._icon_font else "⚡", bg=T.ACCENT, fg="#ffffff",
                 font=(self._icon_font or T.FAMILY, 13), width=2, pady=2).pack(side="left")
        tk.Label(logo, text="PC Optimizer", bg=T.SIDEBAR, fg=T.TEXT, font=(T.FAMILY_SEMI, 13)).pack(side="left", padx=10)
        self._nav_items: dict[str, tuple[tk.Frame, list[tk.Widget]]] = {}
        self._active: str | None = None

        foot = tk.Frame(bar, bg=T.SIDEBAR)
        foot.pack(side="bottom", fill="x", padx=20, pady=(10, 16))
        tk.Frame(bar, bg=T.BORDER, height=1).pack(side="bottom", fill="x", padx=16)
        # The page list scrolls when the window is too short to show every page.
        nav = ScrollFrame(bar, bg=T.SIDEBAR, scrollbar=False)
        nav.pack(fill="both", expand=True)
        entries = {key: (label, glyph) for key, label, glyph, _ in NAV}
        for key in MAIN:
            self._nav_row(nav.inner, key, *entries[key])

        self._adv_open = self.backend.care_settings().get("show_advanced", False)
        more = tk.Frame(nav.inner, bg=T.SIDEBAR, cursor="hand2")
        more.pack(fill="x", padx=10, pady=(14, 1))
        tk.Frame(more, bg=T.SIDEBAR, width=3).pack(side="left", fill="y")
        ic = tk.Label(more, text=MORE_ICON if self._icon_font else "⋯", bg=T.SIDEBAR, fg=T.MUTED,
                      font=(self._icon_font, 11) if self._icon_font else (T.FAMILY, 12), width=2)
        ic.pack(side="left", padx=(9, 6), pady=7)
        tx = tk.Label(more, text="More tools", bg=T.SIDEBAR, fg=T.MUTED, font=T.F_BODY)
        tx.pack(side="left")
        self._chevron = tk.Label(more, bg=T.SIDEBAR, fg=T.MUTED,
                                 font=(self._icon_font, 8) if self._icon_font else (T.FAMILY, 9))
        self._chevron.pack(side="right", padx=10)
        for w in (more, ic, tx, self._chevron):
            w.bind("<Button-1>", lambda _: self.toggle_advanced())
            w.bind("<Enter>", lambda _, ws=(more, ic, tx, self._chevron): [x.configure(bg=T.CARD) for x in ws])
            w.bind("<Leave>", lambda _, ws=(more, ic, tx, self._chevron): [x.configure(bg=T.SIDEBAR) for x in ws])
        self._adv_frame = tk.Frame(nav.inner, bg=T.SIDEBAR)
        for key in ADVANCED:
            self._nav_row(self._adv_frame, key, *entries[key], small=True)
        self._show_advanced(self._adv_open)

        admin = self.backend.is_admin()
        status = tk.Frame(foot, bg=T.SIDEBAR)
        status.pack(anchor="w", fill="x")
        tk.Label(status, text="●", bg=T.SIDEBAR, fg=T.GOOD if admin else T.WARN, font=T.F_SMALL).pack(side="left")
        tk.Label(status, text="Full access" if admin else "Limited access", bg=T.SIDEBAR, fg=T.TEXT_SOFT,
                 font=T.F_SMALL).pack(side="left", padx=(4, 0))
        tk.Label(status, text=f"v{__version__}", bg=T.SIDEBAR, fg=T.MUTED, font=T.F_SMALL).pack(side="right")
        busy = tk.Frame(foot, bg=T.SIDEBAR)
        busy.pack(anchor="w", fill="x", pady=(8, 0))
        self.spinner = Spinner(busy, bg=T.SIDEBAR)
        self.busy_label = tk.Label(busy, text="", bg=T.SIDEBAR, fg=T.ACCENT_FG, font=T.F_SMALL, anchor="w")
        self.busy_label.pack(side="left", padx=(6, 0))

    def _nav_row(self, parent, key, label, glyph, small=False):
        row = tk.Frame(parent, bg=T.SIDEBAR, cursor="hand2")
        row.pack(fill="x", padx=10, pady=1)
        marker = tk.Frame(row, bg=T.SIDEBAR, width=3)
        marker.pack(side="left", fill="y")
        ic = tk.Label(row, text=NAV_ICONS[key] if self._icon_font else glyph, bg=T.SIDEBAR, fg=T.MUTED,
                      font=(self._icon_font, 11) if self._icon_font else (T.FAMILY, 12), width=2)
        ic.pack(side="left", padx=(9, 6), pady=6 if small else 8)
        tx = tk.Label(row, text=label, bg=T.SIDEBAR, fg=T.TEXT_SOFT, font=T.F_BODY)
        tx.pack(side="left")
        for w in (row, ic, tx):
            w.bind("<Button-1>", lambda _, k=key: self.show(k))
            w.bind("<Enter>", lambda _, k=key: self._paint_nav(k, hover=True))
            w.bind("<Leave>", lambda _, k=key: self._paint_nav(k))
        self._nav_items[key] = (marker, [row, ic, tx])

    def _show_advanced(self, show: bool) -> None:
        self._adv_open = show
        if show:
            self._adv_frame.pack(fill="x")
        else:
            self._adv_frame.pack_forget()
        self._chevron.configure(text=CHEVRON[show] if self._icon_font else ("▾" if show else "▸"))

    def toggle_advanced(self) -> None:
        self._show_advanced(not self._adv_open)
        threading.Thread(target=self.backend.set_care_setting, args=("show_advanced", self._adv_open),
                         daemon=True).start()

    def _paint_nav(self, key: str, hover: bool = False) -> None:
        marker, (row, ic, tx) = self._nav_items[key]
        active = key == self._active
        bg = T.CARD_HI if active else T.CARD if hover else T.SIDEBAR
        for w in (row, ic, tx):
            w.configure(bg=bg)
        marker.configure(bg=T.ACCENT_FG if active else bg)
        ic.configure(fg=T.ACCENT_FG if active else T.TEXT_SOFT if hover else T.MUTED)
        tx.configure(fg=T.TEXT if active or hover else T.TEXT_SOFT, font=T.F_LABEL if active else T.F_BODY)

    def _cycle(self, step: int) -> str:
        order = MAIN + (ADVANCED if self._adv_open else [])
        cur = order.index(self._active) if self._active in order else 0
        self.show(order[(cur + step) % len(order)])
        return "break"

    def _close(self):
        try:
            self.backend.shutdown()
        finally:
            self.destroy()

    def page(self, key: str) -> pages.Page:
        """Pages are built the first time they're opened, so the app starts quickly."""
        if key not in self.pages:
            page = self._classes[key](self.container, self)
            page.place(relx=0, rely=0, relwidth=1, relheight=1)
            self.pages[key] = page
        return self.pages[key]

    def show(self, key: str):
        if key in ADVANCED and not self._adv_open:
            self._show_advanced(True)
        self._active = key
        for k in self._nav_items:
            self._paint_nav(k)
        page = self.page(key)
        page.tkraise()
        self.toast.lift_above()
        page.scroll.canvas.yview_moveto(0)
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
        self._busy_start(label)

        def runner():
            try:
                result = work()
            except Exception as exc:
                traceback.print_exc()
                # Bind exc now: Python clears the name when the except block ends.
                self.ui(lambda e=exc: (self._busy_end(label), error(e) if error else None))
                return
            self.ui(lambda: (self._busy_end(label), done(result) if done else None))

        threading.Thread(target=runner, daemon=True).start()

    def _busy_start(self, label: str) -> None:
        self._busy.append(label)
        self._show_busy()

    def _busy_end(self, label: str) -> None:
        if label in self._busy:
            self._busy.remove(label)
        self._show_busy()

    def _show_busy(self) -> None:
        if self._busy:
            self.busy_label.configure(text=f"{self._busy[-1]}...")
            self.spinner.start()
        else:
            self.busy_label.configure(text="")
            self.spinner.stop()

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
            self.run_care()
            self.after(CARE_EVERY_MS, self._care_timer)

        self.bg(work, done, label="Getting to know your PC")

    # ------------------------------------------------------------------ Auto Care
    def on_care(self, fn: Callable[[autocare.CareReport | None], None]) -> None:
        """``fn(report)`` after every check-up; ``fn(None)`` when one starts."""
        self._care_listeners.append(fn)
        if self.care_report is not None:
            fn(self.care_report)

    def _care_timer(self):
        self.run_care()
        self.after(CARE_EVERY_MS, self._care_timer)

    def run_care(self, manual: bool = False) -> None:
        """Check the PC; fix what's safe; tell the user about the rest."""
        if self.specs is None or self.care_running:
            return
        prefs = self.backend.care_settings()
        if not manual and not prefs["auto_care"]:
            return
        self.care_running = True
        for fn in self._care_listeners:
            fn(None)
        specs = self.specs

        def work():
            rep = autocare.run(self.backend, specs, auto_fix=prefs["auto_fix"])
            state = self.backend.care_state()
            state.record(rep)
            return rep, state.to_notify(rep)

        def done(result):
            rep, new = result
            self.care_running = False
            self.care_report = rep
            for fn in self._care_listeners:
                fn(rep)
            if rep.fixed:
                self.toast.show("Fixed automatically", "; ".join(rep.fixed), kind="good")
            if new:
                self.toast.show("Your PC needs attention" if len(new) == 1 else f"{len(new)} things need your attention",
                                "; ".join(new), kind="warn", action=("See what to do", lambda: self.show("dashboard")))
                if not self._focused():
                    from ..notify import notify_problems

                    notify_problems(new)
            elif manual and not rep.fixed:
                self.toast.show("All good", "Your PC is in good shape.", kind="good")

        def error(exc):
            self.care_running = False
            self.toast.show("Check-up didn't finish", str(exc), kind="bad")
            for fn in self._care_listeners:
                fn(self.care_report)

        self.bg(work, done, error, label="Checking your PC")

    def _focused(self) -> bool:
        try:
            return self.state() != "iconic" and self.focus_displayof() is not None
        except (KeyError, tk.TclError):
            return False

    # ------------------------------------------------------------------ actions
    def do_action(self, action: str, done: Callable[[], None] | None = None) -> None:
        """Carry out a problem's one-click action (shared by Home and PC Check-up)."""
        finish = done or (lambda: None)
        if action.startswith("page:"):
            self.show(action[5:])
        elif action.startswith("url:https://"):
            self.backend.open_url(action[4:])
        elif action.startswith("refresh:"):
            device, hz = action[len("refresh:"):].rsplit(":", 1)

            def changed(res):
                ok, msg = res
                self.toast.show(f"Screen set to {hz} Hz", "Everything will look smoother.", kind="good") if ok else \
                    self.toast.show("Couldn't change the screen", msg or "Windows refused the change.", kind="bad")
                finish()

            self.bg(lambda: self.backend.set_refresh_rate(device, int(hz)), changed, label="Changing your screen")
        elif action == "free_ram":
            self.bg(self.backend.trim_ram, lambda _n: (self.toast.show("Memory freed up", "Apps you open next will "
                                                                        "have more room.", kind="good"), finish()),
                    label="Freeing up memory")
        elif action == "clean_junk":
            self.show("cleaner")
        elif action == "bios":
            if messagebox.askyesno("Restart into BIOS", "Your PC will restart straight into the BIOS setup in 5 "
                                   "seconds. Save your work first.\n\nLook for 'XMP', 'EXPO' or 'DOCP' (memory) "
                                   "and 'Re-Size BAR' / 'Above 4G Decoding'. Save and exit with F10.", parent=self):
                if self.backend.restart_to_bios() != 0:
                    self.toast.show("Couldn't open the BIOS", "Restart your PC and press Del or F2 while it starts.",
                                    kind="bad")
        elif action == "restart":
            if messagebox.askyesno("Restart your PC", "Restart in 10 seconds? Save your work first.", parent=self):
                self.backend.restart_pc()
                self.toast.show("Restarting in 10 seconds", "Save anything you're working on.", kind="warn")
