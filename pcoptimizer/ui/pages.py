"""All application pages."""

from __future__ import annotations

import os
import re
import time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from .. import benchmark, boost, profiles
from ..cleaner import human
from ..commands import COMMANDS
from ..drivers import OLD_DRIVER_YEARS
from ..network import PROVIDERS, current_provider
from ..system_info import gb
from . import theme as T
from .widgets import Bar, Button, Card, LogBox, ScoreGauge, ScrollFrame, ToggleRow

GIB = 1024 ** 3


class Page(tk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent, bg=T.BG)
        self.app = app
        self.backend = app.backend
        self.scroll = ScrollFrame(self)
        self.scroll.pack(fill="both", expand=True)
        self.body = tk.Frame(self.scroll.inner, bg=T.BG)
        self.body.pack(fill="both", expand=True, padx=28, pady=24)
        self.build()

    def build(self):
        raise NotImplementedError

    def on_show(self):
        pass

    def header(self, title, subtitle, color=T.ACCENT):
        f = tk.Frame(self.body, bg=T.BG)
        f.pack(fill="x", pady=(0, 18))
        tk.Frame(f, bg=color, width=4).pack(side="left", fill="y", padx=(0, 12))
        box = tk.Frame(f, bg=T.BG)
        box.pack(side="left", fill="x", expand=True)
        tk.Label(box, text=title, bg=T.BG, fg=T.TEXT, font=T.F_TITLE).pack(anchor="w")
        tk.Label(box, text=subtitle, bg=T.BG, fg=T.MUTED, font=T.F_BODY, justify="left", wraplength=820).pack(anchor="w")
        return f

    def card(self, title=None, subtitle=None, **pack):
        c = Card(self.body, title, subtitle)
        c.pack(fill="x", pady=(0, 16), **pack)
        return c

    def log_card(self, title="Activity log", height=9):
        c = self.card(title)
        box = LogBox(c.body, height=height)
        box.pack(fill="both", expand=True)
        return box

    def confirm(self, title, message) -> bool:
        return messagebox.askyesno(title, message, parent=self)


class TweakList:
    """Checkbox list of tweaks with live 'Applied' status."""

    def __init__(self, page: Page, parent, tweaks, applied_text="✔ Applied", not_applied_text="Not applied"):
        self.page, self.tweaks = page, tweaks
        self.applied_text, self.not_applied_text = applied_text, not_applied_text
        self.vars = {t.id: tk.BooleanVar(value=t.recommended) for t in tweaks}
        self.rows: dict[str, ToggleRow] = {}
        for i, t in enumerate(tweaks):
            if i:
                tk.Frame(parent, bg=T.BORDER, height=1).pack(fill="x", pady=8)
            badges = []
            if t.needs_restart:
                badges.append(("RESTART", T.WARN))
            if not t.recommended:
                badges.append(("OPTIONAL", T.MUTED))
            desc = t.description + (f"\n⚠ {t.warning}" if t.warning else "")
            row = ToggleRow(parent, t.title, desc, self.vars[t.id], "…", badges=badges)
            row.pack(fill="x")
            self.rows[t.id] = row

    def selected(self) -> list[str]:
        return [i for i, v in self.vars.items() if v.get()]

    def refresh(self):
        mgr = self.page.backend.tweaks

        def work():
            return {t.id: (t.is_available(mgr), mgr.is_applied(t.id)) for t in self.tweaks}

        def done(status):
            for tid, (available, applied) in status.items():
                if not available:
                    self.vars[tid].set(False)
                    self.rows[tid].set_status("Not installed", T.MUTED)
                else:
                    self.rows[tid].set_status(self.applied_text if applied else self.not_applied_text,
                                              T.GOOD if applied else T.MUTED)

        self.page.app.bg(work, done, label="Checking settings")


# =============================================================================== Dashboard


class DashboardPage(Page):
    def build(self):
        self.header("Your PC at a glance", "Hardware specs, a performance rating out of 100 and live usage.")

        top = tk.Frame(self.body, bg=T.BG)
        top.pack(fill="x", pady=(0, 16))
        top.columnconfigure(0, weight=3, uniform="top")
        top.columnconfigure(1, weight=2, uniform="top")

        score = Card(top, "Performance rating")
        score.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        inner = tk.Frame(score.body, bg=T.CARD)
        inner.pack(fill="x")
        self.gauge = ScoreGauge(inner, size=170)
        self.gauge.pack(side="left", padx=(0, 18))
        right = tk.Frame(inner, bg=T.CARD)
        right.pack(side="left", fill="both", expand=True)
        self.verdict = tk.Label(right, text="Analysing your hardware...", bg=T.CARD, fg=T.TEXT,
                                font=(T.FAMILY, 11, "bold"), justify="left", wraplength=250, anchor="w")
        self.verdict.pack(fill="x", pady=(4, 10))
        self.comp_frame = tk.Frame(right, bg=T.CARD)
        self.comp_frame.pack(fill="x")

        live = Card(top, "Live usage")
        live.grid(row=0, column=1, sticky="nsew", padx=(8, 0))
        self.live = {}
        for key, label in (("cpu", "CPU"), ("ram", "Memory"), ("disk", "System drive")):
            tk.Label(live.body, text=label, bg=T.CARD, fg=T.MUTED, font=T.F_SMALL).pack(anchor="w")
            row = tk.Frame(live.body, bg=T.CARD)
            row.pack(fill="x", pady=(2, 12))
            bar = Bar(row, width=160)
            bar.pack(side="left", fill="x", expand=True)
            val = tk.Label(row, text="-", bg=T.CARD, fg=T.TEXT, font=(T.FAMILY, 10, "bold"), width=16, anchor="e")
            val.pack(side="left")
            self.live[key] = (bar, val)
        temps = tk.Frame(live.body, bg=T.CARD)
        temps.pack(fill="x", pady=(0, 10))
        self.temp_lbls = {}
        for col, (key, label) in enumerate((("cpu_temp", "CPU temp"), ("gpu_temp", "GPU temp"), ("gpu_load", "GPU load"))):
            temps.columnconfigure(col, weight=1)
            tk.Label(temps, text=label, bg=T.CARD, fg=T.MUTED, font=T.F_SMALL).grid(row=0, column=col, sticky="w")
            lbl = tk.Label(temps, text="-", bg=T.CARD, fg=T.TEXT, font=(T.FAMILY, 13, "bold"))
            lbl.grid(row=1, column=col, sticky="w")
            self.temp_lbls[key] = lbl
        self.profile_lbl = tk.Label(live.body, text="Mode: -", bg=T.CARD, fg=T.MUTED, font=T.F_SMALL)
        self.profile_lbl.pack(anchor="w")

        actions = self.card("Quick actions")
        row = tk.Frame(actions.body, bg=T.CARD)
        row.pack(fill="x")
        self.boost_btn = Button(row, "⚡  One-Click Boost", self.one_click_boost)
        self.boost_btn.pack(side="left", padx=(0, 10))
        Button(row, "▶  Gaming Mode", lambda: self.app.show("gaming"), color=T.GAMING).pack(side="left", padx=(0, 10))
        Button(row, "\u271A  Health Check", lambda: self.app.show("health"), color=T.CARD_HI).pack(side="left",
                                                                                                   padx=(0, 10))
        Button(row, "♲  Clean junk", lambda: self.app.show("cleaner"), color=T.CARD_HI).pack(side="left", padx=(0, 10))
        self.ram_btn = Button(row, "Free up RAM", self.free_ram, color=T.CARD_HI)
        self.ram_btn.pack(side="left")
        tk.Label(actions.body, text="One-Click Boost creates a restore point, applies the recommended speed and "
                                    "privacy settings, turns off unneeded services, cleans junk, frees RAM and "
                                    "flushes DNS. You choose the steps, and everything can be undone.",
                 bg=T.CARD, fg=T.MUTED, font=T.F_SMALL, wraplength=820, justify="left").pack(anchor="w", pady=(10, 0))

        self.specs_card = self.card("System specifications")
        tk.Label(self.specs_card.body, text="Reading hardware information...", bg=T.CARD, fg=T.MUTED).pack(anchor="w")

        self.log = self.log_card(height=7)
        self.app.on_specs(self.populate)
        self.tick()

    # ---------------------------------------------------------------- live
    def tick(self):
        try:
            cpu, total, avail = self.backend.usage()
            used = total - avail
            self._set_live("cpu", cpu, f"{cpu:.0f}%")
            if total:
                self._set_live("ram", 100 * used / total, f"{gb(used)} / {gb(total, 0)}")
            specs = self.app.specs
            if specs and specs.sys_drive_size:
                free = self.backend.disk_free()
                self._set_live("disk", 100 * (1 - free / specs.sys_drive_size), f"{gb(free, 0)} free")
            self._set_temps(self.backend.temps())
        finally:
            self.after(1500, self.tick)

    def _set_temps(self, r):
        for key, value, warn, bad, unit in (("cpu_temp", r.cpu_temp, 75, 90, "°C"),
                                            ("gpu_temp", r.gpu_temp, 78, 87, "°C"),
                                            ("gpu_load", r.gpu_load, 101, 101, "%")):
            if value is None:
                self.temp_lbls[key].configure(text="n/a", fg=T.MUTED)
            else:
                color = T.TEXT if unit == "%" else T.GOOD if value < warn else T.WARN if value < bad else T.BAD
                self.temp_lbls[key].configure(text=f"{value:.0f}{unit}", fg=color)

    def _set_live(self, key, pct, text):
        bar, lbl = self.live[key]
        bar.set(pct, T.GOOD if pct < 60 else T.WARN if pct < 85 else T.BAD)
        lbl.configure(text=text)

    def on_show(self):
        mgr = self.backend.tweaks
        self.app.bg(lambda: profiles.current_profile(mgr),
                    lambda p: self.profile_lbl.configure(text=f"Mode: {p}",
                                                         fg={"Gaming": T.GAMING, "Balanced": T.BALANCED}.get(p, T.MUTED)),
                    label="Checking mode")

    # ---------------------------------------------------------------- specs
    def populate(self, specs, rating):
        self.gauge.set(rating.total, rating.grade)
        self.verdict.configure(text=f"{rating.grade} PC\nWeakest part: {rating.bottleneck}")
        for w in self.comp_frame.winfo_children():
            w.destroy()
        for comp in rating.components:
            row = tk.Frame(self.comp_frame, bg=T.CARD)
            row.pack(fill="x", pady=3)
            tk.Label(row, text=comp.name, bg=T.CARD, fg=T.TEXT, font=T.F_SMALL, width=12, anchor="w").pack(side="left")
            bar = Bar(row, width=60)
            bar.pack(side="left", fill="x", expand=True, padx=6)
            bar.set(comp.percent, T.score_color(comp.percent))
            tk.Label(row, text=f"{comp.score:.0f}/{comp.max_score:.0f}", bg=T.CARD, fg=T.MUTED, font=T.F_SMALL,
                     width=6, anchor="e").pack(side="left")

        body = self.specs_card.body
        for w in body.winfo_children():
            w.destroy()
        rows = [("Processor", f"{specs.cpu_name}\n{specs.cores} cores / {specs.threads} threads"
                 + (f" @ {specs.max_clock_mhz / 1000:.2f} GHz base" if specs.max_clock_mhz else ""))]
        for g in specs.gpus or []:
            extra = [gb(g.vram_bytes, 0) + " VRAM" if g.vram_bytes else "", f"driver {g.driver}" if g.driver else ""]
            rows.append(("Graphics", g.name + ("\n" + " • ".join(x for x in extra if x) if any(extra) else "")))
        mem = gb(specs.total_ram_bytes, 0)
        if specs.ram_speed_mhz:
            mem += f" @ {specs.ram_speed_mhz} MHz"
        if specs.ram_sticks:
            mem += f"  ({specs.ram_sticks} stick{'s' if specs.ram_sticks > 1 else ''})"
        rows.append(("Memory", mem))
        for d in specs.disks:
            rows.append(("Storage", f"{d.name}\n{d.kind} • {d.size_bytes / 1000 ** 3:.0f} GB"
                         + (f" • {d.health}" if d.health else "") + ("  (system drive)" if d.is_system else "")))
        if specs.motherboard:
            rows.append(("Motherboard", specs.motherboard))
        if specs.manufacturer or specs.model:
            rows.append(("System", f"{specs.manufacturer} {specs.model}".strip()))
        gpu = specs.primary_gpu
        if gpu and gpu.resolution:
            rows.append(("Display", f"{gpu.resolution} @ {gpu.refresh_hz} Hz"))
        rows.append(("Windows", f"{specs.os_name}  (build {specs.os_build or specs.os_version})"))
        if specs.last_boot:
            rows.append(("Last restart", specs.last_boot))

        grid = tk.Frame(body, bg=T.CARD)
        grid.pack(fill="x")
        grid.columnconfigure(1, weight=1, uniform="s")
        grid.columnconfigure(3, weight=1, uniform="s")
        for i, (k, v) in enumerate(rows):
            r, c = divmod(i, 2)
            tk.Label(grid, text=k.upper(), bg=T.CARD, fg=T.MUTED, font=(T.FAMILY, 8, "bold"), anchor="nw",
                     width=12).grid(row=r, column=c * 2, sticky="nw", pady=7)
            tk.Label(grid, text=v, bg=T.CARD, fg=T.TEXT, font=T.F_BODY, justify="left", anchor="nw",
                     wraplength=300).grid(row=r, column=c * 2 + 1, sticky="nw", pady=7, padx=(0, 16))

    # ---------------------------------------------------------------- actions
    def free_ram(self):
        self.ram_btn.set_enabled(False)
        before = self.backend.usage()[2]

        def work():
            n = self.backend.trim_ram()
            time.sleep(0.5)
            return n, self.backend.usage()[2]

        def done(res):
            n, after = res
            self.ram_btn.set_enabled(True)
            self.log.write(f"[OK] Trimmed {n} processes - {human(max(0, after - before))} of RAM released")

        self.app.bg(work, done, label="Freeing RAM")

    def one_click_boost(self):
        dlg = BoostDialog(self.winfo_toplevel())
        self.wait_window(dlg)
        if not dlg.result:
            return
        steps = dlg.result
        self.boost_btn.set_enabled(False)
        log = self.app.logger(self.log)
        self.log.write("> One-Click Boost started")

        def done(res):
            self.boost_btn.set_enabled(True)
            self.log.write(f"> Boost complete - {boost.summary(res)}. Undo anything in Backup & Restore.")
            if res.needs_restart and self.confirm("Restart now?", "Some changes take effect after a restart.\n\n"
                                                  "Save your work, then click Yes to restart now (or No to "
                                                  "restart later yourself)."):
                self.backend.run_command("shutdown /r /t 10 /c \"PC Optimizer: restarting to finish the boost\"",
                                         lambda _l: None)
                self.log.write("> Restarting in 10 seconds... (run 'shutdown /a' to cancel)")

        self.app.bg(lambda: boost.run(self.backend, steps, log), done,
                    error=lambda e: (self.boost_btn.set_enabled(True), self.log.write(f"ERROR: {e}")),
                    label="Boosting")


class BoostDialog(tk.Toplevel):
    """Choose which One-Click Boost steps to run."""

    def __init__(self, parent):
        super().__init__(parent)
        self.result = None
        self.title("One-Click Boost")
        self.configure(bg=T.CARD)
        self.transient(parent)
        self.resizable(False, False)
        body = tk.Frame(self, bg=T.CARD, padx=22, pady=18)
        body.pack(fill="both", expand=True)
        tk.Label(body, text="\u26A1 One-Click Boost", bg=T.CARD, fg=T.TEXT, font=T.F_H2).pack(anchor="w")
        tk.Label(body, text="Untick anything you want to skip. Every change can be undone in Backup & Restore.",
                 bg=T.CARD, fg=T.MUTED, font=T.F_SMALL).pack(anchor="w", pady=(2, 12))
        self.vars = {}
        for step in boost.STEPS:
            self.vars[step.id] = tk.BooleanVar(value=step.default)
            ToggleRow(body, step.title, step.description, self.vars[step.id]).pack(fill="x", pady=4)
        row = tk.Frame(body, bg=T.CARD)
        row.pack(fill="x", pady=(14, 0))
        Button(row, "\u26A1  Boost now", self._ok).pack(side="right")
        Button(row, "Cancel", self.destroy, color=T.CARD_HI).pack(side="right", padx=8)
        self.bind("<Escape>", lambda _: self.destroy())
        self.update_idletasks()
        x = parent.winfo_rootx() + (parent.winfo_width() - self.winfo_width()) // 2
        y = parent.winfo_rooty() + (parent.winfo_height() - self.winfo_height()) // 3
        self.geometry(f"+{max(0, x)}+{max(0, y)}")
        self.grab_set()

    def _ok(self):
        chosen = {k for k, v in self.vars.items() if v.get()}
        if chosen:
            self.result = chosen
        self.grab_release()
        self.destroy()


# =============================================================================== Gaming


def gaming_tips(specs) -> list[str]:
    tips = []
    gpu = specs.primary_gpu
    name = (gpu.name if gpu else "").lower()
    if "nvidia" in name or "geforce" in name or "rtx" in name:
        tips += [
            "NVIDIA Control Panel → Manage 3D settings → Power management mode: Prefer maximum performance.",
            "Set Low Latency Mode to On (Ultra if you're GPU-limited) and Texture filtering quality to High performance.",
            "Turn on NVIDIA Reflex and DLSS (Quality/Balanced) in games that support them.",
        ]
    elif "radeon" in name or "amd" in name:
        tips += [
            "AMD Adrenalin → Gaming → Graphics: enable Radeon Anti-Lag, disable Radeon Chill.",
            "Turn on FSR (Quality) in supported games and enable Smart Access Memory in BIOS if available.",
        ]
    elif "arc" in name:
        tips.append("Keep Intel Arc drivers up to date and use XeSS in supported games.")
    elif gpu:
        tips.append("You're on integrated graphics: play at 720p-900p with FSR/XeSS upscaling and Low presets.")
    if gpu and gpu.refresh_hz and gpu.refresh_hz <= 60:
        tips.append(f"Your display runs at {gpu.refresh_hz} Hz. If your monitor supports more, raise it in "
                    "Settings → Display → Advanced display → Refresh rate.")
    if specs.ram_speed_mhz and specs.ram_speed_mhz <= 2666:
        tips.append(f"Your RAM runs at {specs.ram_speed_mhz} MHz. If it's rated faster, enable XMP/EXPO in BIOS "
                    "for a free 5-15% FPS boost in CPU-heavy games.")
    if specs.ram_sticks == 1:
        tips.append("You have a single RAM stick (single-channel). Adding a matching second stick can boost FPS a lot.")
    if specs.total_ram_bytes and specs.total_ram_bytes < 15 * GIB:
        tips.append("Under 16 GB RAM: close browsers and launchers before playing (use the list above).")
    tips += [
        "In games: use Fullscreen mode, turn off V-Sync (use G-SYNC/FreeSync), cap FPS ~3 below your refresh rate.",
        "Lower Shadows, Volumetrics and Reflections first - they cost the most FPS for the least visual difference.",
        "Keep GPU drivers updated and laptops plugged in while gaming.",
    ]
    return tips


class GamingPage(Page):
    def build(self):
        self.header("Gaming Mode", "Maximum performance settings for the lowest latency and highest FPS. "
                                   "Your original settings are backed up and Balanced Mode switches back.", T.GAMING)
        hero = self.card()
        row = tk.Frame(hero.body, bg=T.CARD)
        row.pack(fill="x")
        self.status = tk.Label(row, text="Status: checking...", bg=T.CARD, fg=T.MUTED, font=T.F_H2)
        self.status.pack(side="left")
        self.btn = Button(row, "▶  Activate Gaming Mode", self.activate, color=T.GAMING)
        self.btn.pack(side="right")

        c = self.card("Gaming optimisations", "Untick anything you don't want. Restart after activating for "
                                              "everything to take effect.")
        self.list = TweakList(self, c.body, self.backend.tweaks.in_category("gaming"))

        pc = self.card("Close background apps", "Apps eating RAM and CPU while you play. Select and end them "
                                                "(Windows system processes are hidden and protected).")
        cols = ("app", "info", "count", "mem")
        self.tree = ttk.Treeview(pc.body, columns=cols, show="headings", height=8, selectmode="extended")
        for col, title, w, anchor in (("app", "Process", 220, "w"), ("info", "What it is", 240, "w"),
                                      ("count", "Instances", 80, "center"), ("mem", "Memory", 110, "e")):
            self.tree.heading(col, text=title)
            self.tree.column(col, width=w, anchor=anchor)
        self.tree.pack(fill="x")
        brow = tk.Frame(pc.body, bg=T.CARD)
        brow.pack(fill="x", pady=(10, 0))
        Button(brow, "Refresh", self.load_processes, color=T.CARD_HI, small=True).pack(side="left")
        Button(brow, "End selected", self.end_selected, color=T.BAD, small=True).pack(side="left", padx=8)

        self.tips_card = self.card("Recommended in-game & driver settings")
        self.tips_body = self.tips_card.body
        self.log = self.log_card()
        self.app.on_specs(self.show_tips)

    def on_show(self):
        self.list.refresh()
        self.load_processes()
        self.app.bg(lambda: profiles.current_profile(self.backend.tweaks), self._set_status, label="Checking mode")

    def _set_status(self, profile):
        on = profile == "Gaming"
        self.status.configure(text="Status: ● Gaming Mode ACTIVE" if on else f"Status: {profile} mode",
                              fg=T.GAMING if on else T.MUTED)

    def show_tips(self, specs, _rating):
        for w in self.tips_body.winfo_children():
            w.destroy()
        for tip in gaming_tips(specs):
            tk.Label(self.tips_body, text="•  " + tip, bg=T.CARD, fg=T.TEXT, font=T.F_BODY, justify="left",
                     wraplength=820, anchor="w").pack(anchor="w", pady=3)

    def activate(self):
        ids = self.list.selected()
        if not ids:
            return
        self.btn.set_enabled(False)
        mgr, log = self.backend.tweaks, self.app.logger(self.log)

        def done(results):
            self.btn.set_enabled(True)
            ok = sum(r.ok for r in results)
            self.log.write(f"> Gaming Mode: {ok}/{len(results)} optimisations applied. Restart for best results.")
            self.on_show()

        self.app.bg(lambda: profiles.apply_gaming(mgr, ids, log), done, label="Applying Gaming Mode")

    def load_processes(self):
        def done(procs):
            self.tree.delete(*self.tree.get_children())
            for p in procs[:40]:
                self.tree.insert("", "end", iid=p.name, values=(p.name, p.label, p.count, f"{p.memory_kb / 1024:,.0f} MB"))

        self.app.bg(self.backend.processes, done, label="Listing apps")

    def end_selected(self):
        names = list(self.tree.selection())
        if not names or not self.confirm("End apps", "Close these apps? Unsaved work in them will be lost.\n\n"
                                         + "\n".join(names)):
            return
        log = self.app.logger(self.log)

        def work():
            for n in names:
                ok, msg = self.backend.end_process(n)
                log(f"[OK] Ended {n}" if ok else f"[FAILED] {n}: {msg}")

        self.app.bg(work, lambda _: self.load_processes(), label="Ending apps")


# =============================================================================== Balanced


class BalancedPage(Page):
    def build(self):
        self.header("Balanced Mode", "For everyday work, browsing and laptops on battery: quiet, cool and efficient "
                                     "while keeping Windows responsive.", T.BALANCED)
        hero = self.card()
        row = tk.Frame(hero.body, bg=T.CARD)
        row.pack(fill="x")
        self.status = tk.Label(row, text="Status: checking...", bg=T.CARD, fg=T.MUTED, font=T.F_H2)
        self.status.pack(side="left")
        self.btn = Button(row, "◐  Activate Balanced Mode", self.activate, color=T.BALANCED)
        self.btn.pack(side="right")

        what = self.card("What Balanced Mode does")
        for line in (
            "Switches to the Windows Balanced power plan - the CPU boosts when you need it and idles when you don't.",
            "Restores your original settings for the power-hungry Gaming Mode tweaks (power throttling, scheduler "
            "priority, GPU scheduling, mouse acceleration).",
            "Keeps Game Mode on and keeps every 'Speed Up PC' tweak you applied - those help in all modes.",
            "Best for: office work, studying, streaming video, browsing and battery life.",
        ):
            tk.Label(what.body, text="•  " + line, bg=T.CARD, fg=T.TEXT, justify="left", wraplength=820,
                     anchor="w").pack(anchor="w", pady=3)

        tips = self.card("Tips for a fast everyday PC")
        for line in (
            "Restart at least once a week - 'Shut down' with Fast Startup doesn't clear memory leaks.",
            "Keep at least 15-20% of your system drive free (see Cleaner & RAM).",
            "Disable startup apps you don't need straight after boot (see Speed Up PC).",
            "Too many browser tabs are the #1 RAM user - enable Memory Saver / Sleeping Tabs in Chrome or Edge.",
        ):
            tk.Label(tips.body, text="•  " + line, bg=T.CARD, fg=T.TEXT, justify="left", wraplength=820,
                     anchor="w").pack(anchor="w", pady=3)
        self.log = self.log_card()

    def on_show(self):
        def done(p):
            on = p == "Balanced"
            self.status.configure(text="Status: ● Balanced Mode ACTIVE" if on else f"Status: {p} mode",
                                  fg=T.BALANCED if on else T.MUTED)

        self.app.bg(lambda: profiles.current_profile(self.backend.tweaks), done, label="Checking mode")

    def activate(self):
        self.btn.set_enabled(False)
        mgr, log = self.backend.tweaks, self.app.logger(self.log)

        def done(results):
            self.btn.set_enabled(True)
            self.log.write(f"> Balanced Mode active ({sum(r.ok for r in results)}/{len(results)} steps OK).")
            self.on_show()

        self.app.bg(lambda: profiles.apply_balanced(mgr, log), done, label="Applying Balanced Mode")


# =============================================================================== Tweaks


class TweaksPage(Page):
    def build(self):
        self.header("Speed Up PC", "Windows settings and registry tweaks that make your PC faster and snappier. "
                                   "Your original values are backed up before anything changes.")
        c = self.card("Performance tweaks")
        brow = tk.Frame(c.body, bg=T.CARD)
        brow.pack(fill="x", pady=(0, 14))
        self.apply_btn = Button(brow, "Apply selected", self.apply)
        self.apply_btn.pack(side="left")
        Button(brow, "Undo selected", self.undo, color=T.CARD_HI).pack(side="left", padx=8)
        Button(brow, "Select recommended", self.recommended, color=T.CARD_HI, small=True).pack(side="right")
        self.list = TweakList(self, c.body, self.backend.tweaks.in_category("speed"))

        pc = self.card("Privacy & bloat", "Registry settings that stop ads, tracking and background extras. "
                                          "Also part of One-Click Boost.")
        prow = tk.Frame(pc.body, bg=T.CARD)
        prow.pack(fill="x", pady=(0, 14))
        Button(prow, "Apply selected", lambda: self.apply(self.privacy)).pack(side="left")
        Button(prow, "Undo selected", lambda: self.undo(self.privacy), color=T.CARD_HI).pack(side="left", padx=8)
        self.privacy = TweakList(self, pc.body, self.backend.tweaks.in_category("privacy"))

        sc = self.card("Optional security trade-offs", "More FPS in exchange for less protection. Never applied by "
                                                       "One-Click Boost - only if you choose to here.")
        srow = tk.Frame(sc.body, bg=T.CARD)
        srow.pack(fill="x", pady=(0, 14))
        Button(srow, "Apply selected", lambda: self.apply(self.security), color=T.WARN).pack(side="left")
        Button(srow, "Undo selected", lambda: self.undo(self.security), color=T.CARD_HI).pack(side="left", padx=8)
        self.security = TweakList(self, sc.body, self.backend.tweaks.in_category("security"))
        tk.Label(sc.body, text="Antivirus exclusions for game folders are set per game on the Game Profiles page.",
                 bg=T.CARD, fg=T.MUTED, font=T.F_SMALL).pack(anchor="w", pady=(10, 0))

        dns = self.card("Fast DNS", "DNS turns website names into addresses. A faster DNS makes websites and "
                                    "launchers start loading sooner (it doesn't change in-game ping).")
        self.dns_status = tk.Label(dns.body, text="Checking...", bg=T.CARD, fg=T.TEXT, font=(T.FAMILY, 10, "bold"))
        self.dns_status.pack(anchor="w")
        self.dns_choice = tk.StringVar(value="cloudflare")
        self.dns_rows = {}
        for key, (label, servers) in PROVIDERS.items():
            r = tk.Frame(dns.body, bg=T.CARD)
            r.pack(fill="x", pady=2)
            ttk.Radiobutton(r, text=f"  {label}" + (f"   ({', '.join(servers)})" if servers else ""),
                            value=key, variable=self.dns_choice).pack(side="left")
            ms = tk.Label(r, text="", bg=T.CARD, fg=T.MUTED, font=T.F_SMALL)
            ms.pack(side="right")
            self.dns_rows[key] = ms
        drow = tk.Frame(dns.body, bg=T.CARD)
        drow.pack(fill="x", pady=(10, 0))
        Button(drow, "Use selected DNS", self.apply_dns, small=True).pack(side="left")
        self.dns_test_btn = Button(drow, "Test speed", self.test_dns, color=T.CARD_HI, small=True)
        self.dns_test_btn.pack(side="left", padx=8)
        self._adapters = []

        s = self.card("Startup apps", "Apps that launch with Windows slow down boot. Disabling works exactly like "
                                      "Task Manager - nothing is deleted.")
        self.st_tree = ttk.Treeview(s.body, columns=("name", "status", "cmd"), show="headings", height=7)
        for col, title, w in (("name", "App", 180), ("status", "Status", 90), ("cmd", "Command", 480)):
            self.st_tree.heading(col, text=title)
            self.st_tree.column(col, width=w, anchor="w")
        self.st_tree.pack(fill="x")
        row = tk.Frame(s.body, bg=T.CARD)
        row.pack(fill="x", pady=(10, 0))
        Button(row, "Disable selected", lambda: self.set_startup(False), color=T.BAD, small=True).pack(side="left")
        Button(row, "Enable selected", lambda: self.set_startup(True), color=T.CARD_HI, small=True).pack(side="left", padx=8)
        self.log = self.log_card()
        self._apps = {}

    def on_show(self):
        self.list.refresh()
        self.privacy.refresh()
        self.security.refresh()
        self.load_startup()
        self.load_dns()

    # ------------------------------------------------------------------ DNS
    def load_dns(self):
        def done(adapters):
            self._adapters = adapters
            if not adapters:
                self.dns_status.configure(text="No connected network adapter found.", fg=T.MUTED)
                return
            cur = current_provider(adapters)
            name = PROVIDERS[cur][0] if cur in PROVIDERS else "Custom servers"
            self.dns_status.configure(text=f"Now using: {name}  \u2022  {', '.join(a.name for a in adapters)}",
                                      fg=T.TEXT)
            if cur in PROVIDERS:
                self.dns_choice.set(cur)

        self.app.bg(self.backend.dns_adapters, done, label="Reading network settings")

    def apply_dns(self):
        choice = self.dns_choice.get()
        if not self._adapters:
            return
        log = self.app.logger(self.log)

        def work():
            for line in self.backend.set_dns(choice, self._adapters):
                log(line)

        self.app.bg(work, lambda _: self.load_dns(), label="Changing DNS")

    def test_dns(self):
        self.dns_test_btn.set_enabled(False)
        for lbl in self.dns_rows.values():
            lbl.configure(text="testing...", fg=T.MUTED)

        def work():
            results = {}
            for key, (_, servers) in PROVIDERS.items():
                results[key] = self.backend.measure_dns(servers)
                self.app.ui(lambda k=key, v=results[key]: self.dns_rows[k].configure(
                    text=f"{v:.0f} ms" if v is not None else "no answer", fg=T.TEXT))
            return results

        def done(results):
            self.dns_test_btn.set_enabled(True)
            valid = {k: v for k, v in results.items() if v is not None}
            if valid:
                best = min(valid, key=valid.get)
                self.dns_rows[best].configure(text=f"{valid[best]:.0f} ms  \u2714 fastest", fg=T.GOOD)
                self.dns_choice.set(best)

        self.app.bg(work, done, label="Testing DNS speed")

    def recommended(self):
        for t in self.list.tweaks:
            self.list.vars[t.id].set(t.recommended)

    def apply(self, lst=None):
        lst = lst or self.list
        ids = lst.selected()
        if not ids:
            return
        self.apply_btn.set_enabled(False)
        mgr, log = self.backend.tweaks, self.app.logger(self.log)

        def work():
            for i in ids:
                try:
                    mgr.apply(i)
                    log(f"[OK] {mgr.by_id[i].title}")
                except Exception as exc:
                    log(f"[FAILED] {mgr.by_id[i].title}: {exc}")

        def done(_):
            self.apply_btn.set_enabled(True)
            if any(mgr.by_id[i].needs_restart for i in ids):
                self.log.write("> Some tweaks need a restart (or sign out) to take effect.")
            lst.refresh()

        self.app.bg(work, done, label="Applying tweaks")

    def undo(self, lst=None):
        lst = lst or self.list
        ids = lst.selected()
        mgr, log = self.backend.tweaks, self.app.logger(self.log)

        def work():
            for i in ids:
                if mgr.revert(i):
                    log(f"[OK] Restored original: {mgr.by_id[i].title}")

        self.app.bg(work, lambda _: lst.refresh(), label="Restoring")

    def load_startup(self):
        def done(apps):
            self._apps = {f"{a.hive}|{a.run_key}|{a.name}": a for a in apps}
            self.st_tree.delete(*self.st_tree.get_children())
            for iid, a in self._apps.items():
                self.st_tree.insert("", "end", iid=iid, values=(a.name, "Enabled" if a.enabled else "Disabled", a.command))

        self.app.bg(self.backend.startup_apps, done, label="Reading startup apps")

    def set_startup(self, enabled):
        log = self.app.logger(self.log)
        for iid in self.st_tree.selection():
            app = self._apps[iid]
            try:
                self.backend.set_startup(app, enabled)
                log(f"[OK] {'Enabled' if enabled else 'Disabled'} startup app: {app.name}")
            except OSError as exc:
                log(f"[FAILED] {app.name}: {exc} (run as administrator for system-wide apps)")
        self.load_startup()


# =============================================================================== Cleaner


class CleanerPage(Page):
    def build(self):
        self.header("Cleaner & RAM", "Delete temporary and junk files to free disk space, and release memory "
                                     "that background apps are holding on to.")
        ram = self.card("Memory (RAM)")
        row = tk.Frame(ram.body, bg=T.CARD)
        row.pack(fill="x")
        self.ram_bar = Bar(row, width=300, height=12)
        self.ram_bar.pack(side="left", fill="x", expand=True)
        self.ram_lbl = tk.Label(row, text="-", bg=T.CARD, fg=T.TEXT, font=(T.FAMILY, 10, "bold"), width=22, anchor="e")
        self.ram_lbl.pack(side="left")
        brow = tk.Frame(ram.body, bg=T.CARD)
        brow.pack(fill="x", pady=(12, 0))
        self.ram_btn = Button(brow, "Free up RAM", self.free_ram)
        self.ram_btn.pack(side="left")
        Button(brow, "Flush DNS cache", self.flush_dns, color=T.CARD_HI).pack(side="left", padx=8)
        tk.Label(ram.body, text="Trims unused memory from every running app so it's available to whatever you open "
                                "next - great right before launching a game.", bg=T.CARD, fg=T.MUTED, font=T.F_SMALL,
                 wraplength=820, justify="left").pack(anchor="w", pady=(10, 0))

        junk = self.card("Junk files")
        top = tk.Frame(junk.body, bg=T.CARD)
        top.pack(fill="x", pady=(0, 12))
        self.scan_btn = Button(top, "Scan", self.scan, color=T.CARD_HI)
        self.scan_btn.pack(side="left")
        self.clean_btn = Button(top, "Clean selected", self.clean)
        self.clean_btn.pack(side="left", padx=8)
        self.total_lbl = tk.Label(top, text="Press Scan to see how much space you can free.", bg=T.CARD, fg=T.MUTED)
        self.total_lbl.pack(side="right")
        self.progress = ttk.Progressbar(junk.body, mode="determinate")
        self.progress.pack(fill="x", pady=(0, 12))

        self.vars, self.rows, self.sizes = {}, {}, {}
        for i, t in enumerate(self.backend.clean_targets):
            if i:
                tk.Frame(junk.body, bg=T.BORDER, height=1).pack(fill="x", pady=7)
            self.vars[t.id] = tk.BooleanVar(value=t.recommended)
            self.vars[t.id].trace_add("write", lambda *_: self._update_total())
            badges = [] if t.recommended else [("OPTIONAL", T.MUTED)]
            r = ToggleRow(junk.body, t.title, t.description, self.vars[t.id], "—", badges=badges)
            r.pack(fill="x")
            self.rows[t.id] = r
        self.log = self.log_card()
        self.tick()

    def tick(self):
        if self.winfo_ismapped():
            _, total, avail = self.backend.usage()
            if total:
                pct = 100 * (total - avail) / total
                self.ram_bar.set(pct, T.GOOD if pct < 60 else T.WARN if pct < 85 else T.BAD)
                self.ram_lbl.configure(text=f"{gb(total - avail)} used of {gb(total, 0)}")
        self.after(1500, self.tick)

    def _update_total(self):
        if self.sizes:
            total = sum(self.sizes.get(i, 0) for i, v in self.vars.items() if v.get())
            self.total_lbl.configure(text=f"Selected: {human(total)}", fg=T.TEXT)

    def on_show(self):
        if not self.sizes:
            self.scan()

    def scan(self):
        self.scan_btn.set_enabled(False)
        targets = self.backend.clean_targets
        self.progress.configure(maximum=len(targets), value=0)

        def work():
            for t in targets:
                size, count = self.backend.scan(t)
                self.app.ui(lambda t=t, s=size, c=count: self._scanned(t, s, c))

        def done(_):
            self.scan_btn.set_enabled(True)
            self._update_total()

        self.app.bg(work, done, label="Scanning")

    def _scanned(self, t, size, count):
        self.sizes[t.id] = size
        self.progress.step(1)
        self.rows[t.id].set_status(human(size), T.WARN if size > 500 * 1024 ** 2 else T.TEXT)

    def clean(self):
        targets = [t for t in self.backend.clean_targets if self.vars[t.id].get()]
        if not targets:
            return
        if any(t.id == "recycle_bin" for t in targets) and not self.confirm(
                "Empty Recycle Bin", "Items in the Recycle Bin will be permanently deleted. Continue?"):
            return
        self.clean_btn.set_enabled(False)
        self.progress.configure(maximum=len(targets), value=0)
        log = self.app.logger(self.log)

        def work():
            freed = 0
            for t in targets:
                try:
                    freed += self.backend.clean(t, log)
                except Exception as exc:
                    log(f"[FAILED] {t.title}: {exc}")
                self.app.ui(lambda: self.progress.step(1))
            return freed

        def done(freed):
            self.clean_btn.set_enabled(True)
            self.log.write(f"[OK] Done - freed {human(freed)} in total.")
            self.sizes.clear()
            self.scan()

        self.app.bg(work, done, label="Cleaning")

    def free_ram(self):
        self.ram_btn.set_enabled(False)
        before = self.backend.usage()[2]

        def work():
            n = self.backend.trim_ram()
            time.sleep(0.5)
            return n, self.backend.usage()[2]

        def done(res):
            n, after = res
            self.ram_btn.set_enabled(True)
            self.log.write(f"[OK] Trimmed {n} processes - {human(max(0, after - before))} of RAM released")

        self.app.bg(work, done, label="Freeing RAM")

    def flush_dns(self):
        self.app.bg(self.backend.flush_dns,
                    lambda ok: self.log.write("[OK] DNS cache flushed" if ok else "[FAILED] Could not flush DNS"),
                    label="Flushing DNS")


# =============================================================================== Commands


class CommandsPage(Page):
    def build(self):
        self.header("CMD Toolkit", "Proven Windows maintenance commands - repair, clean, optimise and fix network "
                                   "issues. Output appears live below.")
        self.buttons = []
        groups: dict[str, list] = {}
        for c in COMMANDS:
            groups.setdefault(c.group, []).append(c)
        for group, cmds in groups.items():
            card = self.card(group)
            for i, c in enumerate(cmds):
                if i:
                    tk.Frame(card.body, bg=T.BORDER, height=1).pack(fill="x", pady=8)
                row = tk.Frame(card.body, bg=T.CARD)
                row.pack(fill="x")
                btn = Button(row, "Run", lambda c=c: self.run_preset(c), small=True)
                btn.pack(side="right", padx=(12, 0))
                self.buttons.append(btn)
                txt = tk.Frame(row, bg=T.CARD)
                txt.pack(side="left", fill="x", expand=True)
                head = tk.Frame(txt, bg=T.CARD)
                head.pack(anchor="w")
                tk.Label(head, text=c.title, bg=T.CARD, fg=T.TEXT, font=(T.FAMILY, 10, "bold")).pack(side="left")
                if c.duration:
                    tk.Label(head, text=f" {c.duration} ", bg=T.CARD_HI, fg=T.MUTED, font=(T.FAMILY, 8)).pack(side="left", padx=6)
                if c.needs_restart:
                    tk.Label(head, text=" RESTART ", bg=T.CARD_HI, fg=T.WARN, font=(T.FAMILY, 8, "bold")).pack(side="left")
                tk.Label(txt, text=c.description, bg=T.CARD, fg=T.MUTED, font=T.F_SMALL, justify="left",
                         wraplength=700).pack(anchor="w")
                if c.warning:
                    tk.Label(txt, text="\u26A0 " + c.warning, bg=T.CARD, fg=T.WARN, font=T.F_SMALL, justify="left",
                             wraplength=700).pack(anchor="w")
                lines = c.shown.splitlines()
                shown = c.shown if len(lines) == 1 else f"PowerShell script, {len(lines)} lines - click to view"
                code = tk.Label(txt, text=shown, bg=T.CARD, fg=T.ACCENT, font=T.F_MONO, justify="left",
                                wraplength=700)
                code.pack(anchor="w", pady=(2, 0))
                if shown != c.shown:
                    code.configure(cursor="hand2")
                    code.bind("<Button-1>", lambda _e, lbl=code, full=c.shown: lbl.configure(text=full))

        custom = self.card("Run your own command", "Runs in cmd.exe with administrator rights. Only run commands "
                                                   "you trust.")
        row = tk.Frame(custom.body, bg=T.CARD)
        row.pack(fill="x")
        self.entry = tk.Entry(row, bg="#0b0d12", fg=T.TEXT, insertbackground=T.TEXT, relief="flat", font=T.F_MONO)
        self.entry.pack(side="left", fill="x", expand=True, ipady=7, padx=(0, 10))
        self.entry.bind("<Return>", lambda _: self.run_custom())
        btn = Button(row, "Run", self.run_custom, small=True)
        btn.pack(side="left")
        self.buttons.append(btn)
        self.log = self.log_card("Output", height=14)

    def run_preset(self, c):
        if c.warning and not self.confirm(c.title, c.warning + "\n\nContinue?"):
            return
        self.run(c.command, c.title, c.needs_restart)

    def run_custom(self):
        cmd = self.entry.get().strip()
        if cmd:
            self.run(cmd, cmd, False)

    def run(self, cmd, title, needs_restart):
        for b in self.buttons:
            b.set_enabled(False)
        self.log.write(f"> {title}")
        log = self.app.logger(self.log)
        started = time.time()

        def done(code):
            for b in self.buttons:
                b.set_enabled(True)
            mins, secs = divmod(int(time.time() - started), 60)
            self.log.write(("[OK]" if code == 0 else "[FAILED]") + f" Finished with exit code {code} in {mins}m {secs}s")
            if needs_restart and code == 0:
                self.log.write("> Restart your PC to complete this change.")

        self.app.bg(lambda: self.backend.run_command(cmd, log), done, label="Running command")


# =============================================================================== Restore


class RestorePage(Page):
    def build(self):
        self.header("Backup & Restore", "Every change PC Optimizer makes is recorded so you can undo it at any time.",
                    T.WARN)
        c = self.card("Safety net")
        row = tk.Frame(c.body, bg=T.CARD)
        row.pack(fill="x")
        Button(row, "Create restore point", self.restore_point).pack(side="left")
        Button(row, "Undo ALL changes", self.undo_all, color=T.BAD).pack(side="left", padx=8)
        Button(row, "Open System Restore", lambda: self.backend.run_command("start rstrui.exe", lambda _: None),
               color=T.CARD_HI).pack(side="left")
        tk.Label(c.body, text=f"Backup file: {self.backend.tweaks.backup_path}", bg=T.CARD, fg=T.MUTED,
                 font=T.F_SMALL).pack(anchor="w", pady=(12, 0))

        self.changes = self.card("Changes currently applied")
        self.log = self.log_card()

    def on_show(self):
        mgr = self.backend.tweaks
        body = self.changes.body
        for w in body.winfo_children():
            w.destroy()
        changed = [t for t in mgr.tweaks if mgr.has_backup(t.id)]
        if not changed:
            tk.Label(body, text="No changes yet - your PC is on its original settings.", bg=T.CARD, fg=T.MUTED).pack(anchor="w")
        for t in changed:
            row = tk.Frame(body, bg=T.CARD)
            row.pack(fill="x", pady=3)
            tk.Label(row, text="✔  " + t.title, bg=T.CARD, fg=T.TEXT).pack(side="left")
            Button(row, "Undo", lambda i=t.id: self.undo(i), color=T.CARD_HI, small=True).pack(side="right")

    def undo(self, tweak_id):
        mgr, log = self.backend.tweaks, self.app.logger(self.log)

        def work():
            if mgr.revert(tweak_id):
                log(f"[OK] Restored original: {mgr.by_id[tweak_id].title}")

        self.app.bg(work, lambda _: self.on_show(), label="Restoring")

    def undo_all(self):
        if not self.confirm("Undo all changes", "Restore every setting PC Optimizer changed to its original value?"):
            return
        mgr, log = self.backend.tweaks, self.app.logger(self.log)

        def work():
            for tid in mgr.revert_all():
                log(f"[OK] Restored original: {mgr.by_id[tid].title if tid in mgr.by_id else tid}")
            log("> All changes undone. Restart your PC to finish.")

        self.app.bg(work, lambda _: self.on_show(), label="Restoring")

    def restore_point(self):
        cmd = next(c for c in COMMANDS if c.id == "restore_point")
        log = self.app.logger(self.log)
        self.log.write("> Creating restore point...")
        self.app.bg(lambda: self.backend.run_command(cmd.command, log),
                    lambda code: self.log.write("[OK] Restore point created" if code == 0 else
                                                "[FAILED] Could not create restore point - turn on System Protection "
                                                "for your C: drive in 'Create a restore point' settings."),
                    label="Creating restore point")


# =============================================================================== Game profiles


class GamesPage(Page):
    OPTIONS = [
        ("high_priority", "High CPU priority",
         "Windows always starts the game with High priority, so background tasks can't steal CPU time."),
        ("high_perf_gpu", "High-performance GPU",
         "Forces the game onto your dedicated graphics card (Settings → Display → Graphics)."),
        ("disable_fso", "Disable fullscreen optimizations",
         "Uses true exclusive fullscreen. Can lower input lag in older DirectX 9-11 games, but test it per game."),
        ("defender_exclusion", "Skip Defender scans (optional)",
         "Excludes the game's folder from real-time antivirus scanning so loading and shader compiling stutter "
         "less. A small security trade-off - only use it for games from Steam, Epic, Xbox or other trusted stores."),
    ]

    def build(self):
        self.header("Game Profiles", "Add the games you play. Each one gets its own performance settings, and "
                                     "Boost & Launch frees RAM and switches to Gaming Mode before starting it.",
                    T.GAMING)
        top = self.card()
        row = tk.Frame(top.body, bg=T.CARD)
        row.pack(fill="x")
        Button(row, "+  Add game (.exe)", self.add_game, color=T.GAMING).pack(side="left")
        self.auto_gaming = tk.BooleanVar(value=True)
        ttk.Checkbutton(row, text="  Boost & Launch switches to Gaming Mode", variable=self.auto_gaming).pack(side="right")

        auto = self.card("Auto Gaming Mode", "Switches to Gaming Mode by itself whenever one of your games is running "
                                             "- however you start it (Steam, desktop shortcut...) - and puts your "
                                             "settings back about 30 seconds after you close it. Runs in the "
                                             "background helper, even when this app is closed.")
        arow = tk.Frame(auto.body, bg=T.CARD)
        arow.pack(fill="x")
        self.auto_status = tk.Label(arow, text="Checking...", bg=T.CARD, fg=T.MUTED, font=(T.FAMILY, 10, "bold"))
        self.auto_status.pack(side="left")
        self.auto_btn = Button(arow, "Turn on", self.toggle_auto, color=T.GAMING, small=True)
        self.auto_btn.pack(side="right")
        self._auto_on = False

        self.list_card = self.card("Your games")
        legend = self.card("What the options do")
        for _, title, desc in self.OPTIONS:
            tk.Label(legend.body, text=f"•  {title}: {desc}", bg=T.CARD, fg=T.TEXT, justify="left", wraplength=820,
                     anchor="w").pack(anchor="w", pady=2)
        self.log = self.log_card()
        self.render()

    def render(self):
        body = self.list_card.body
        for w in body.winfo_children():
            w.destroy()
        games = self.backend.games.games
        if not games:
            tk.Label(body, text="No games yet. Click 'Add game' and pick the game's .exe file "
                                "(e.g. in Steam\\steamapps\\common).", bg=T.CARD, fg=T.MUTED).pack(anchor="w")
        for i, game in enumerate(games):
            if i:
                tk.Frame(body, bg=T.BORDER, height=1).pack(fill="x", pady=10)
            row = tk.Frame(body, bg=T.CARD)
            row.pack(fill="x")
            info = tk.Frame(row, bg=T.CARD)
            info.pack(side="left", fill="x", expand=True)
            tk.Label(info, text=game.name, bg=T.CARD, fg=T.TEXT, font=(T.FAMILY, 11, "bold")).pack(anchor="w")
            tk.Label(info, text=game.path, bg=T.CARD, fg=T.MUTED, font=T.F_SMALL, wraplength=560,
                     justify="left").pack(anchor="w")
            opts = tk.Frame(info, bg=T.CARD)
            opts.pack(anchor="w", pady=(6, 0))
            for n, (key, title, _) in enumerate(self.OPTIONS):
                var = tk.BooleanVar(value=getattr(game, key))
                ttk.Checkbutton(opts, text=" " + title, variable=var,
                                command=lambda g=game, k=key, v=var: self.set_option(g, k, v)).grid(
                    row=n // 2, column=n % 2, sticky="w", padx=(0, 18), pady=1)
            Button(row, "Remove", lambda g=game: self.remove(g), color=T.CARD_HI, small=True).pack(side="right", padx=(8, 0))
            Button(row, "⚡ Boost & Launch", lambda g=game: self.launch(g), color=T.GAMING, small=True).pack(side="right")

    def add_game(self):
        initial = next((d for d in (r"C:\Program Files (x86)\Steam\steamapps\common", r"C:\Program Files\Epic Games",
                                    r"C:\XboxGames", r"C:\Program Files") if os.path.isdir(d)), None)
        path = filedialog.askopenfilename(parent=self, title="Choose the game's .exe", initialdir=initial,
                                          filetypes=[("Programs", "*.exe")])
        if not path:
            return
        path = os.path.normpath(path)
        log = self.app.logger(self.log)

        def work():
            game = self.backend.games.add(path)
            log(f"[OK] Added {game.name} - high priority and high-performance GPU set")

        self.app.bg(work, lambda _: self.render(), error=lambda e: self.log.write(f"[FAILED] {e}"),
                    label="Adding game")

    def on_show(self):
        def done(on):
            self._auto_on = on
            self.auto_status.configure(text="\u25CF On - watching for your games" if on else "\u25CB Off",
                                       fg=T.GAMING if on else T.MUTED)
            self.auto_btn.configure(text="Turn off" if on else "Turn on")

        self.app.bg(self.backend.auto_gaming_enabled, done, label="Checking Auto Gaming Mode")

    def toggle_auto(self):
        enable = not self._auto_on
        if enable and not self.backend.games.games:
            self.log.write("[FAILED] Add at least one game first - Auto Gaming Mode watches for your games.")
            return

        def done(res):
            ok, msg = res
            self.log.write(f"[OK] Auto Gaming Mode {'on' if enable else 'off'}" if ok else f"[FAILED] {msg}")
            self.on_show()

        self.app.bg(lambda: self.backend.set_auto_gaming(enable), done, label="Updating Auto Gaming Mode")

    def set_option(self, game, key, var):
        value = var.get()
        if key == "defender_exclusion" and value and not self.confirm(
                "Skip Defender scans", f"Exclude this folder from Windows Defender's real-time scanning?\n\n"
                f"{game.folder}\n\nOnly do this for games from trusted stores. Untick it (or remove the game) to "
                "turn scanning back on."):
            var.set(False)
            return
        log = self.app.logger(self.log)

        def work():
            self.backend.games.update(game, **{key: value})
            log(f"[OK] {game.name}: {dict((k, t) for k, t, _ in self.OPTIONS)[key]} {'on' if value else 'off'}")

        self.app.bg(work, error=lambda e: self.log.write(f"[FAILED] {e}"), label="Updating game")

    def remove(self, game):
        if not self.confirm("Remove game", f"Remove {game.name} and restore its original settings?"):
            return
        log = self.app.logger(self.log)

        def work():
            self.backend.games.remove(game)
            log(f"[OK] Removed {game.name}; its settings were restored")

        self.app.bg(work, lambda _: self.render(), label="Removing game")

    def launch(self, game):
        if not self.backend.demo and not os.path.exists(game.path):
            self.log.write(f"[FAILED] {game.path} no longer exists - remove it and add it again.")
            return
        log, mgr, b = self.app.logger(self.log), self.backend.tweaks, self.backend
        switch = self.auto_gaming.get()

        def work():
            log(f"> Boosting for {game.name}...")
            if switch and profiles.current_profile(mgr) != "Gaming":
                profiles.apply_gaming(mgr, log=log)
            log(f"[OK] Freed memory from {b.trim_ram()} background processes")
            b.flush_dns()
            b.launch_game(game.path)
            log(f"[OK] Launched {game.name}. Have fun!")

        self.app.bg(work, label="Launching")


# =============================================================================== Services


class ServicesPage(Page):
    def build(self):
        self.header("Services", "Turn off Windows services you don't use to free RAM and CPU. "
                                "Undo puts back each service's original startup type.")
        c = self.card("Optional services")
        brow = tk.Frame(c.body, bg=T.CARD)
        brow.pack(fill="x", pady=(0, 6))
        self.apply_btn = Button(brow, "Disable selected", self.apply)
        self.apply_btn.pack(side="left")
        Button(brow, "Undo selected", self.undo, color=T.CARD_HI).pack(side="left", padx=8)
        Button(brow, "Select recommended", self.recommended, color=T.CARD_HI, small=True).pack(side="right")
        tk.Label(c.body, text="Ticked by default: services almost nobody needs. Read the warning before "
                              "ticking anything marked OPTIONAL.", bg=T.CARD, fg=T.MUTED, font=T.F_SMALL).pack(anchor="w", pady=(0, 12))
        self.list = TweakList(self, c.body, self.backend.tweaks.in_category("services"),
                              applied_text="✔ Disabled", not_applied_text="Enabled")

        run = self.card("Running services", "Every service running right now. Stopping one frees its RAM until "
                                            "the next restart (or until Windows needs it again). Services Windows "
                                            "can't live without are marked PROTECTED and can't be stopped here.")
        self.svc_tree = ttk.Treeview(run.body, columns=("name", "display", "start", "state"), show="headings",
                                     height=12, selectmode="extended")
        for col, title, w in (("display", "Service", 360), ("name", "Name", 180), ("start", "Starts", 110),
                              ("state", "", 110)):
            self.svc_tree.heading(col, text=title)
            self.svc_tree.column(col, width=w, anchor="w")
        self.svc_tree.tag_configure("protected", foreground=T.MUTED)
        self.svc_tree.pack(fill="x")
        row = tk.Frame(run.body, bg=T.CARD)
        row.pack(fill="x", pady=(10, 0))
        Button(row, "Stop selected", lambda: self.set_running(False), color=T.BAD, small=True).pack(side="left")
        Button(row, "Start selected", lambda: self.set_running(True), color=T.CARD_HI, small=True).pack(side="left",
                                                                                                     padx=8)
        Button(row, "Refresh", self.load_running, color=T.CARD_HI, small=True).pack(side="left")
        self._running = {}
        self.log = self.log_card()

    def on_show(self):
        self.list.refresh()
        self.load_running()

    def load_running(self):
        def done(svcs):
            self._running = {s.name: s for s in svcs}
            self.svc_tree.delete(*self.svc_tree.get_children())
            for s in sorted(svcs, key=lambda s: (s.protected, s.display_name.lower())):
                self.svc_tree.insert("", "end", iid=s.name, tags=("protected",) if s.protected else (),
                                     values=(s.name, s.display_name, s.start_type,
                                             "PROTECTED" if s.protected else "Running"))

        self.app.bg(self.backend.running_services, done, label="Listing services")

    def set_running(self, run):
        names = list(self.svc_tree.selection())
        if not names:
            return
        if not run:
            blocked = [n for n in names if self._running.get(n) and self._running[n].protected]
            names = [n for n in names if n not in blocked]
            for n in blocked:
                self.log.write(f"[FAILED] {n} is protected - Windows needs it")
            if not names or not self.confirm("Stop services", "Stop these services until the next restart?\n\n"
                                             + "\n".join(self._running[n].display_name for n in names
                                                         if n in self._running)
                                             + "\n\nTo keep one off for good, disable it in the list above."):
                return
        log = self.app.logger(self.log)

        def work():
            for n in names:
                ok, msg = self.backend.set_service_running(n, run)
                log(f"[OK] {'Started' if run else 'Stopped'} {n}" if ok else f"[FAILED] {n}: {msg}")

        self.app.bg(work, lambda _: self.load_running(), label="Updating services")

    def recommended(self):
        for t in self.list.tweaks:
            self.list.vars[t.id].set(t.recommended)
        self.list.refresh()

    def apply(self):
        ids = self.list.selected()
        if not ids:
            return
        self.apply_btn.set_enabled(False)
        mgr, log = self.backend.tweaks, self.app.logger(self.log)

        def work():
            for i in ids:
                try:
                    mgr.apply(i)
                    log(f"[OK] Disabled {mgr.by_id[i].title}")
                except Exception as exc:
                    log(f"[FAILED] {mgr.by_id[i].title}: {exc}")

        def done(_):
            self.apply_btn.set_enabled(True)
            self.list.refresh()

        self.app.bg(work, done, label="Disabling services")

    def undo(self):
        ids = self.list.selected()
        mgr, log = self.backend.tweaks, self.app.logger(self.log)

        def work():
            for i in ids:
                if mgr.revert(i):
                    log(f"[OK] Restored {mgr.by_id[i].title}")

        self.app.bg(work, lambda _: self.list.refresh(), label="Restoring services")


# =============================================================================== Benchmark


class BenchmarkPage(Page):
    def build(self):
        self.header("Benchmark", "Measure your PC before and after optimising. Run it once now, apply the "
                                 "optimisations, restart, then run it again to see the difference.")
        c = self.card()
        row = tk.Frame(c.body, bg=T.CARD)
        row.pack(fill="x")
        self.run_btn = Button(row, "⏱  Run benchmark (~20 s)", self.run)
        self.run_btn.pack(side="left")
        Button(row, "Clear history", self.clear, color=T.CARD_HI, small=True).pack(side="right")
        Button(row, "Use latest as new baseline", self.rebase, color=T.CARD_HI, small=True).pack(side="right", padx=8)
        self.progress = ttk.Progressbar(c.body, mode="determinate", maximum=1.0)
        self.progress.pack(fill="x", pady=(14, 4))
        self.status = tk.Label(c.body, text="Close other apps for the most accurate result.", bg=T.CARD,
                               fg=T.MUTED, font=T.F_SMALL)
        self.status.pack(anchor="w")
        self.results = self.card("Results")
        tk.Label(self.body, text="Results can vary by about ±5% between runs. Optimising mostly reduces background "
                                 "load and frees RAM and disk space, so expect the biggest changes there.",
                 bg=T.BG, fg=T.MUTED, font=T.F_SMALL, wraplength=840, justify="left").pack(anchor="w")
        self.render()

    def render(self):
        body = self.results.body
        for w in body.winfo_children():
            w.destroy()
        hist = self.backend.bench_history
        base, latest = hist.baseline, hist.latest
        if not base:
            tk.Label(body, text="No results yet. Run the benchmark to set your 'before' score.", bg=T.CARD,
                     fg=T.MUTED).pack(anchor="w")
            return
        score_row = tk.Frame(body, bg=T.CARD)
        score_row.pack(fill="x", pady=(0, 14))
        self._score_box(score_row, "BEFORE", base.score, base.timestamp, T.MUTED)
        if latest:
            pct = 100 * (latest.score - base.score) / base.score if base.score else 0
            self._score_box(score_row, "AFTER", latest.score, latest.timestamp, T.ACCENT)
            tk.Label(score_row, text=f"{pct:+.1f}%", bg=T.CARD, fg=T.GOOD if pct >= 0 else T.BAD,
                     font=(T.FAMILY, 26, "bold")).pack(side="left", padx=20)

        grid = tk.Frame(body, bg=T.CARD)
        grid.pack(fill="x")
        heads = ["Test", "Before", "After", "Change"] if latest else ["Test", "Result"]
        for col, h in enumerate(heads):
            grid.columnconfigure(col, weight=2 if col == 0 else 1)
            tk.Label(grid, text=h.upper(), bg=T.CARD, fg=T.MUTED, font=(T.FAMILY, 8, "bold"), anchor="w").grid(
                row=0, column=col, sticky="w", pady=(0, 6))
        for r, (key, label, unit, _hb, _ref) in enumerate(benchmark.METRICS, start=1):
            b = base.values.get(key, 0)
            cells = [label, self._fmt(b, unit)]
            colors = [T.TEXT, T.TEXT]
            if latest:
                a = latest.values.get(key, 0)
                ch = benchmark.change(key, b, a)
                suffix = " pts" if key == "bg_cpu" else "%"
                cells += [self._fmt(a, unit), f"{ch:+.1f}{suffix}"]
                colors += [T.TEXT, T.GOOD if ch > 1 else T.BAD if ch < -1 else T.MUTED]
            for col, (text, color) in enumerate(zip(cells, colors)):
                tk.Label(grid, text=text, bg=T.CARD, fg=color, font=T.F_BODY, anchor="w").grid(
                    row=r, column=col, sticky="w", pady=3)

    def _score_box(self, parent, title, score, when, color):
        box = tk.Frame(parent, bg=T.CARD_HI, padx=18, pady=10)
        box.pack(side="left", padx=(0, 12))
        tk.Label(box, text=title, bg=T.CARD_HI, fg=color, font=(T.FAMILY, 8, "bold")).pack(anchor="w")
        tk.Label(box, text=str(score), bg=T.CARD_HI, fg=T.TEXT, font=(T.FAMILY, 26, "bold")).pack(anchor="w")
        tk.Label(box, text=when, bg=T.CARD_HI, fg=T.MUTED, font=T.F_SMALL).pack(anchor="w")

    @staticmethod
    def _fmt(value, unit):
        if unit in ("GB/s", "GB"):
            return f"{value:.1f} {unit}"
        if unit == "%":
            return f"{value:.1f}%"
        return f"{value:,.0f} {unit}"

    def run(self):
        self.run_btn.set_enabled(False)
        self.progress.configure(value=0)

        def progress(msg, frac):
            self.app.ui(lambda: (self.status.configure(text=msg, fg=T.TEXT), self.progress.configure(value=frac)))

        def done(result):
            self.backend.bench_history.add(result)
            self.run_btn.set_enabled(True)
            self.status.configure(text=f"Finished - score {result.score}", fg=T.GOOD)
            self.render()

        def error(exc):
            self.run_btn.set_enabled(True)
            self.status.configure(text=f"Benchmark failed: {exc}", fg=T.BAD)

        self.app.bg(lambda: self.backend.run_benchmark(progress), done, error, label="Benchmarking")

    def rebase(self):
        self.backend.bench_history.set_baseline_to_latest()
        self.render()

    def clear(self):
        if self.confirm("Clear history", "Delete all benchmark results?"):
            self.backend.bench_history.clear()
            self.render()


# =============================================================================== AI assistant


QUICK_PROMPTS = [
    "Why is my PC slow?",
    "Optimise my PC for gaming",
    "My internet is slow or keeps dropping",
    "Set my monitor to its highest refresh rate",
    "What's using all my RAM?",
]


class ApprovalDialog(tk.Toplevel):
    """Modal Allow / Don't allow prompt for an action the AI wants to take."""

    def __init__(self, parent, title, details, warning):
        super().__init__(parent)
        self.result = False
        self.title("AI wants to make a change")
        self.configure(bg=T.CARD)
        self.transient(parent)
        self.resizable(False, False)
        body = tk.Frame(self, bg=T.CARD, padx=22, pady=18)
        body.pack(fill="both", expand=True)
        tk.Label(body, text="⚡ The AI assistant wants to:", bg=T.CARD, fg=T.MUTED, font=T.F_SMALL).pack(anchor="w")
        tk.Label(body, text=title, bg=T.CARD, fg=T.TEXT, font=T.F_H2, wraplength=520, justify="left").pack(anchor="w", pady=(2, 10))
        box = tk.Text(body, height=min(12, max(3, details.count("\n") + 2)), width=70, bg="#0b0d12", fg="#c9d1e0",
                      font=T.F_MONO, relief="flat", wrap="word", padx=10, pady=8)
        box.insert("1.0", details)
        box.configure(state="disabled")
        box.pack(fill="x")
        if warning:
            tk.Label(body, text="⚠ " + warning, bg=T.CARD, fg=T.WARN, font=T.F_SMALL, wraplength=520,
                     justify="left").pack(anchor="w", pady=(10, 0))
        row = tk.Frame(body, bg=T.CARD)
        row.pack(fill="x", pady=(16, 0))
        Button(row, "Allow", lambda: self._close(True)).pack(side="right")
        Button(row, "Don't allow", lambda: self._close(False), color=T.CARD_HI).pack(side="right", padx=8)
        self.protocol("WM_DELETE_WINDOW", lambda: self._close(False))
        self.bind("<Escape>", lambda _: self._close(False))
        self.update_idletasks()
        x = parent.winfo_rootx() + (parent.winfo_width() - self.winfo_width()) // 2
        y = parent.winfo_rooty() + (parent.winfo_height() - self.winfo_height()) // 3
        self.geometry(f"+{max(0, x)}+{max(0, y)}")
        self.grab_set()
        self.focus_set()

    def _close(self, result):
        self.result = result
        self.grab_release()
        self.destroy()


class AssistantPage(Page):
    def build(self):
        from .. import ai_settings

        self.header("AI Assistant", "Describe a problem or a setting you can't find, in your own words. The AI "
                                    "looks into your PC and fixes it for you, and asks your permission before "
                                    "changing anything.", "#a78bfa")
        self.setup = self.card("Connect the AI", "The assistant uses Claude by Anthropic. Create an API key at "
                                                 "console.anthropic.com (Settings → API keys) and paste it "
                                                 "below. Anthropic bills usage on your account. The key is "
                                                 "stored encrypted on this PC.")
        row = tk.Frame(self.setup.body, bg=T.CARD)
        row.pack(fill="x")
        self.key_entry = tk.Entry(row, show="•", bg="#0b0d12", fg=T.TEXT, insertbackground=T.TEXT,
                                  relief="flat", font=T.F_MONO)
        self.key_entry.pack(side="left", fill="x", expand=True, ipady=7, padx=(0, 10))
        self.key_entry.bind("<Return>", lambda _: self.save_key())
        Button(row, "Save key", self.save_key, color="#7c5cff").pack(side="left")

        chat = self.card()
        self.chat_card = chat
        self.transcript = tk.Text(chat.body, height=17, bg="#0b0d12", fg=T.TEXT, font=T.F_BODY, relief="flat",
                                  wrap="word", padx=14, pady=12, state="disabled", spacing1=2, spacing3=4)
        self.transcript.pack(fill="both", expand=True)
        for tag, opts in {"you": {"foreground": T.ACCENT, "font": (T.FAMILY, 10, "bold")},
                          "ai": {"foreground": "#a78bfa", "font": (T.FAMILY, 10, "bold")},
                          "msg": {"foreground": T.TEXT, "lmargin1": 12, "lmargin2": 12},
                          "step": {"foreground": T.MUTED, "font": T.F_SMALL, "lmargin1": 12},
                          "err": {"foreground": T.BAD, "lmargin1": 12}}.items():
            self.transcript.tag_configure(tag, **opts)

        chips = tk.Frame(chat.body, bg=T.CARD)
        chips.pack(fill="x", pady=(10, 6))
        for i, p in enumerate(QUICK_PROMPTS):
            if i % 3 == 0:
                line = tk.Frame(chips, bg=T.CARD)
                line.pack(fill="x", pady=(0, 6))
            Button(line, p, lambda p=p: self.send(p), color=T.CARD_HI, small=True).pack(side="left", padx=(0, 6))

        inp = tk.Frame(chat.body, bg=T.CARD)
        inp.pack(fill="x", pady=(6, 0))
        self.input = tk.Text(inp, height=3, bg="#0b0d12", fg=T.TEXT, insertbackground=T.TEXT, relief="flat",
                             font=T.F_BODY, wrap="word", padx=10, pady=8)
        self.input.pack(side="left", fill="x", expand=True, padx=(0, 10))
        self.input.bind("<Return>", self._enter)
        btns = tk.Frame(inp, bg=T.CARD)
        btns.pack(side="left", fill="y")
        self.send_btn = Button(btns, "Send", lambda: self.send(), color="#7c5cff")
        self.send_btn.pack(fill="x")
        self.stop_btn = Button(btns, "Stop", self.stop, color=T.CARD_HI, small=True)
        self.stop_btn.pack(fill="x", pady=(6, 0))
        foot = tk.Frame(chat.body, bg=T.CARD)
        foot.pack(fill="x", pady=(8, 0))
        Button(foot, "New chat", self.new_chat, color=T.CARD_HI, small=True).pack(side="left")
        Button(foot, "Change API key", self.change_key, color=T.CARD_HI, small=True).pack(side="left", padx=6)
        tk.Label(foot, text="Enter to send • Shift+Enter for a new line", bg=T.CARD, fg=T.MUTED,
                 font=T.F_SMALL).pack(side="right")
        self.stop_btn.set_enabled(False)

        self.assistant = None
        self.busy = False
        has_key = self.backend.demo or bool(ai_settings.load_api_key())
        if has_key:
            self.setup.pack_forget()
        self._say("ai", "Hi! I'm your PC assistant. Tell me what's wrong or what you'd like to change, for "
                        "example \"my games stutter\" or \"how do I turn on night light?\". I'll check your PC "
                        "and fix it, and I'll always ask before changing anything.")

    # ------------------------------------------------------------------ key setup
    def save_key(self):
        from .. import ai_settings

        key = self.key_entry.get().strip()
        if not key.startswith("sk-ant-"):
            messagebox.showerror("API key", "That doesn't look like an Anthropic API key (it should start with "
                                            "'sk-ant-').", parent=self)
            return
        ai_settings.save_api_key(key)
        self.key_entry.delete(0, "end")
        self.assistant = None
        self.setup.pack_forget()

    def change_key(self):
        self.setup.pack(fill="x", pady=(0, 16), before=self.chat_card)
        self.key_entry.focus_set()

    # ------------------------------------------------------------------ chat
    def _say(self, who, text, kind="msg"):
        t = self.transcript
        t.configure(state="normal")
        if who:
            t.insert("end", ("You" if who == "you" else "AI assistant") + "\n", who)
        t.insert("end", text.strip() + "\n\n" if kind != "step" else "→ " + text + "\n", kind)
        t.see("end")
        t.configure(state="disabled")

    def _enter(self, event):
        if event.state & 0x1:  # Shift held: newline
            return None
        self.send()
        return "break"

    def _ensure_assistant(self):
        if self.assistant is None:
            from ..ai_assistant import Assistant

            client = self.backend.ai_client() if hasattr(self.backend, "ai_client") else None
            self.assistant = Assistant(self.backend, client=client, approve=self._approve,
                                       notify=lambda m: self.app.ui(lambda: self._say(None, m, "step")),
                                       on_text=lambda m: self.app.ui(lambda: self._say("ai", m)))
        return self.assistant

    def _approve(self, title, details, warning) -> bool:
        """Called on the worker thread: show the dialog on the UI thread and wait for the answer."""
        import threading

        answer, done = [False], threading.Event()

        def ask():
            dlg = ApprovalDialog(self.winfo_toplevel(), title, details, warning)
            self.wait_window(dlg)
            answer[0] = dlg.result
            done.set()

        self.app.ui(ask)
        done.wait()
        return answer[0]

    def send(self, text=None):
        if self.busy:
            return
        text = (text or self.input.get("1.0", "end")).strip()
        if not text:
            return
        self.input.delete("1.0", "end")
        self._say("you", text)
        self.busy = True
        self.send_btn.set_enabled(False)
        self.stop_btn.set_enabled(True)
        assistant = self._ensure_assistant()
        self._say(None, "Thinking...", "step")

        def done(reply):
            self._finish()
            self._say("ai", reply or "(no reply)")

        def error(exc):
            self._finish()
            self._say(None, self._explain(exc), "err")

        self.app.bg(lambda: assistant.send(text), done, error, label="AI is working")

    def _finish(self):
        self.busy = False
        self.send_btn.set_enabled(True)
        self.stop_btn.set_enabled(False)

    @staticmethod
    def _explain(exc) -> str:
        from ..ai_assistant import Cancelled

        if isinstance(exc, Cancelled):
            return "Stopped."
        try:
            import anthropic
        except ImportError:
            return "The AI library isn't installed. Run: pip install anthropic"
        if isinstance(exc, anthropic.AuthenticationError):
            return "Your API key was rejected. Click 'Change API key' and paste a valid key."
        if isinstance(exc, anthropic.PermissionDeniedError):
            return "Your API key isn't allowed to use this model. Check your Anthropic account."
        if isinstance(exc, anthropic.RateLimitError):
            return "Too many requests or no credit left. Wait a minute or check billing in the Anthropic console."
        if isinstance(exc, anthropic.APIConnectionError):
            return "Couldn't reach the AI. Check your internet connection."
        if isinstance(exc, anthropic.APIStatusError):
            return f"The AI service returned an error ({exc.status_code}). Please try again."
        if "api_key" in str(exc).lower():
            return "No API key yet. Paste your key in the box above."
        return f"Something went wrong: {exc}"

    def stop(self):
        if self.assistant:
            self.assistant.cancel_event.set()
            self._say(None, "Stopping after the current step...", "step")

    def new_chat(self):
        if self.busy:
            return
        if self.assistant:
            self.assistant.reset()
        self.transcript.configure(state="normal")
        self.transcript.delete("1.0", "end")
        self.transcript.configure(state="disabled")
        self._say("ai", "New chat started. What can I help with?")


# =============================================================================== Screen time


class UsageChart(tk.Canvas):
    """Single-series bar chart of screen time with a hover tooltip."""

    PAD_L, PAD_R, PAD_T, PAD_B = 58, 12, 12, 26

    def __init__(self, parent, height=220):
        super().__init__(parent, height=height, bg=T.CARD, highlightthickness=0)
        self.series: list[tuple[str, int]] = []
        self.tip_labels: list[str] = []
        self._bars: list[tuple[float, float, int]] = []
        self.bind("<Configure>", lambda _: self.draw())
        self.bind("<Motion>", self._hover)
        self.bind("<Leave>", lambda _: self.delete("tip"))

    def set(self, series, tip_labels):
        self.series, self.tip_labels = series, tip_labels
        self.draw()

    @staticmethod
    def _nice_step(max_seconds):
        for step in (300, 600, 900, 1800, 3600, 7200, 10800, 14400, 21600):
            if max_seconds / step <= 4:
                return step
        return 43200

    def draw(self):
        from ..screentime import fmt_duration

        self.delete("all")
        w, h = self.winfo_width(), self.winfo_height()
        if w < 50 or not self.series:
            return
        top = max([v for _, v in self.series] + [1])
        step = self._nice_step(top)
        ymax = step * max(1, -(-top // step))
        x0, x1, y0, y1 = self.PAD_L, w - self.PAD_R, self.PAD_T, h - self.PAD_B
        for v in range(0, ymax + 1, step):  # recessive grid
            y = y1 - (y1 - y0) * v / ymax
            self.create_line(x0, y, x1, y, fill=T.BORDER)
            tick = "0" if not v else f"{v // 3600}h" if v % 3600 == 0 else fmt_duration(v)
            self.create_text(x0 - 8, y, text=tick, anchor="e", fill=T.MUTED, font=T.F_SMALL)
        n = len(self.series)
        slot = (x1 - x0) / n
        bw = max(3.0, min(36.0, slot * 0.72, slot - 2))  # always a visible surface gap between bars
        label_every = 1 if n <= 12 else 3 if n <= 24 else 5
        self._bars = []
        r = 3
        for i, (label, v) in enumerate(self.series):
            cx = x0 + slot * (i + 0.5)
            bx0, bx1 = cx - bw / 2, cx + bw / 2
            self._bars.append((x0 + slot * i, x0 + slot * (i + 1), i))
            if v > 0:
                by = y1 - max(2, (y1 - y0) * v / ymax)
                if y1 - by > 2 * r and bw > 2 * r:  # rounded data-end, square at the baseline
                    self.create_rectangle(bx0, by + r, bx1, y1, fill=T.ACCENT, width=0)
                    self.create_rectangle(bx0 + r, by, bx1 - r, by + r, fill=T.ACCENT, width=0)
                    self.create_oval(bx0, by, bx0 + 2 * r, by + 2 * r, fill=T.ACCENT, width=0)
                    self.create_oval(bx1 - 2 * r, by, bx1, by + 2 * r, fill=T.ACCENT, width=0)
                else:
                    self.create_rectangle(bx0, by, bx1, y1, fill=T.ACCENT, width=0)
            if i % label_every == 0 or n <= 12:
                self.create_text(cx, y1 + 13, text=label, fill=T.MUTED, font=T.F_SMALL)
        self.create_line(x0, y1, x1, y1, fill=T.MUTED)

    def _hover(self, event):
        from ..screentime import fmt_duration

        self.delete("tip")
        for lo, hi, i in self._bars:
            if lo <= event.x < hi:
                label, v = self.tip_labels[i], self.series[i][1]
                text = f"{label}\n{fmt_duration(v)}"
                x = min(max(event.x, 70), self.winfo_width() - 70)
                t = self.create_text(x, 16, text=text, fill=T.TEXT, font=T.F_SMALL, justify="center", tags="tip",
                                     anchor="n")
                bx0, by0, bx1, by1 = self.bbox(t)
                bg = self.create_rectangle(bx0 - 8, by0 - 5, bx1 + 8, by1 + 5, fill=T.CARD_HI, outline=T.BORDER,
                                           tags="tip")
                self.tag_lower(bg, t)
                cx = lo + (hi - lo) / 2
                hl = self.create_line(cx, by1 + 6, cx, self.winfo_height() - self.PAD_B, fill=T.MUTED, dash=(2, 3),
                                      tags="tip")
                self.tag_lower(hl)
                return


class ScreenTimePage(Page):
    PERIOD_NAMES = {"day": "Daily", "week": "Weekly", "month": "Monthly"}
    PREV_WORD = {"day": "the day before", "week": "last week", "month": "last month"}

    def build(self):
        import datetime as dt

        self.header("Screen Time", "How long you use each app. Pick Daily, Weekly or Monthly and use the arrows "
                                   "to look back in time.", T.BALANCED)
        self.period, self.anchor = "day", dt.date.today()

        st = self.card()
        row = tk.Frame(st.body, bg=T.CARD)
        row.pack(fill="x")
        self.status = tk.Label(row, text="Checking tracker...", bg=T.CARD, fg=T.MUTED, font=(T.FAMILY, 10, "bold"))
        self.status.pack(side="left")
        self.toggle_btn = Button(row, "Turn on", self.toggle, small=True)
        self.toggle_btn.pack(side="right")
        Button(row, "Clear history", self.clear, color=T.CARD_HI, small=True).pack(side="right", padx=8)
        tk.Label(st.body, text="Private: only app names and minutes are saved, on this PC only. Window titles, "
                               "websites and typing are never recorded. Idle time (5+ min with no input, unless a "
                               "video or game keeps the screen on) and locked time don't count.",
                 bg=T.CARD, fg=T.MUTED, font=T.F_SMALL, wraplength=840, justify="left").pack(anchor="w", pady=(8, 0))

        nav = tk.Frame(self.body, bg=T.BG)
        nav.pack(fill="x", pady=(0, 14))
        self.tabs = {}
        seg = tk.Frame(nav, bg=T.CARD_HI, padx=3, pady=3)
        seg.pack(side="left")
        for key, name in self.PERIOD_NAMES.items():
            lbl = tk.Label(seg, text=name, bg=T.CARD_HI, fg=T.MUTED, font=(T.FAMILY, 10, "bold"), padx=18, pady=6,
                           cursor="hand2")
            lbl.pack(side="left")
            lbl.bind("<Button-1>", lambda _, k=key: self.set_period(k))
            self.tabs[key] = lbl
        self.next_btn = Button(nav, "›", lambda: self.move(1), color=T.CARD_HI)
        self.next_btn.pack(side="right")
        self.range_lbl = tk.Label(nav, text="", bg=T.BG, fg=T.TEXT, font=(T.FAMILY, 11, "bold"), width=24)
        self.range_lbl.pack(side="right", padx=6)
        Button(nav, "‹", lambda: self.move(-1), color=T.CARD_HI).pack(side="right")

        summary = tk.Frame(self.body, bg=T.BG)
        summary.pack(fill="x", pady=(0, 16))
        self.tiles = {}
        for col, (key, title) in enumerate((("total", "Total screen time"), ("avg", "Daily average"),
                                            ("change", "Compared with before"), ("top", "Most used app"))):
            summary.columnconfigure(col, weight=1, uniform="tile")
            tile = tk.Frame(summary, bg=T.CARD, highlightthickness=1, highlightbackground=T.BORDER, padx=16, pady=12)
            tile.grid(row=0, column=col, sticky="nsew", padx=(0 if col == 0 else 6, 0 if col == 3 else 6))
            tk.Label(tile, text=title.upper(), bg=T.CARD, fg=T.MUTED, font=(T.FAMILY, 8, "bold")).pack(anchor="w")
            val = tk.Label(tile, text="-", bg=T.CARD, fg=T.TEXT, font=(T.FAMILY, 15 if key == "top" else 20, "bold"),
                           anchor="w")
            val.pack(anchor="w", fill="x")
            sub = tk.Label(tile, text="", bg=T.CARD, fg=T.MUTED, font=T.F_SMALL, anchor="w")
            sub.pack(anchor="w", fill="x")
            self.tiles[key] = (val, sub)

        self.chart_card = self.card()
        self.chart_title = tk.Label(self.chart_card.body, text="", bg=T.CARD, fg=T.TEXT, font=T.F_H2)
        self.chart_title.pack(anchor="w", pady=(0, 10))
        self.chart = UsageChart(self.chart_card.body)
        self.chart.pack(fill="x")
        self.apps_card = self.card("Apps")
        self.set_period("day")

    # ------------------------------------------------------------------ tracker on/off
    def on_show(self):
        self.refresh()

        def done(on):
            self._tracking = on
            self.status.configure(text="● Tracking is on - starts automatically when you sign in" if on else
                                  "○ Tracking is off - turn it on to start recording screen time",
                                  fg=T.GOOD if on else T.WARN)
            self.toggle_btn.configure(text="Turn off" if on else "Turn on")
            if on and not self.backend.demo and not getattr(self, "_refreshed_task", False):
                # Re-register once per launch so the task follows the app if it was moved or updated.
                self._refreshed_task = True
                self.app.bg(lambda: self.backend.set_screentime(True), label="Starting tracker")

        self.app.bg(self.backend.screentime_enabled, done, label="Checking tracker")

    def toggle(self):
        enable = not getattr(self, "_tracking", False)
        if not enable and not self.confirm("Turn off screen time", "Stop recording screen time? Your history "
                                                                  "is kept."):
            return

        def done(res):
            ok, msg = res
            if not ok:
                messagebox.showerror("Screen time", f"Couldn't {'start' if enable else 'stop'} the tracker:\n{msg}",
                                     parent=self)
            self.on_show()

        self.app.bg(lambda: self.backend.set_screentime(enable), done, label="Updating tracker")

    def clear(self):
        if self.confirm("Clear history", "Delete all recorded screen time? This can't be undone."):
            self.app.bg(self.backend.clear_screentime, lambda _: self.refresh(), label="Clearing")

    # ------------------------------------------------------------------ navigation
    def set_period(self, period):
        import datetime as dt

        self.period, self.anchor = period, dt.date.today()
        for key, lbl in self.tabs.items():
            on = key == period
            lbl.configure(bg=T.ACCENT if on else T.CARD_HI, fg="#ffffff" if on else T.MUTED)
        self.refresh()

    def move(self, steps):
        import datetime as dt

        from ..screentime import shift

        new = shift(self.period, self.anchor, steps)
        if new > dt.date.today():
            return
        self.anchor = new
        self.refresh()

    def refresh(self):
        period, anchor = self.period, self.anchor
        self.app.bg(lambda: self.backend.screentime_report(period, anchor), self.render, label="Loading screen time")

    # ------------------------------------------------------------------ rendering
    def render(self, rep):
        import datetime as dt

        from ..screentime import fmt_duration

        if rep.period != self.period:
            return  # a newer request is on its way
        self.range_lbl.configure(text=rep.label)
        self.next_btn.set_enabled(rep.end < dt.date.today())

        val, sub = self.tiles["total"]
        val.configure(text=fmt_duration(rep.total))
        sub.configure(text=f"{rep.start.strftime('%d %b')} – {rep.end.strftime('%d %b')}" if rep.period != "day"
                      else rep.start.strftime("%A"))
        val, sub = self.tiles["avg"]
        if rep.period == "day":
            val.configure(text="-")
            sub.configure(text="see Weekly or Monthly")
        else:
            val.configure(text=fmt_duration(rep.daily_average))
            sub.configure(text=f"over {rep.days_with_data} day{'s' if rep.days_with_data != 1 else ''} with use")
        val, sub = self.tiles["change"]
        if rep.previous_total and rep.total:
            pct = 100 * (rep.total - rep.previous_total) / rep.previous_total
            val.configure(text=f"{'▲' if pct >= 0 else '▼'} {abs(pct):.0f}%")
            sub.configure(text=f"{'more' if pct >= 0 else 'less'} than {self.PREV_WORD[rep.period]}")
        else:
            val.configure(text="-")
            sub.configure(text=f"no data for {self.PREV_WORD[rep.period]}")
        val, sub = self.tiles["top"]
        if rep.apps:
            top = rep.apps[0]
            val.configure(text=top.name if len(top.name) <= 18 else top.name[:17] + "…")
            sub.configure(text=fmt_duration(top.seconds))
        else:
            val.configure(text="-")
            sub.configure(text="")

        if rep.period == "day":
            tips = [f"{h}:00 – {h}:59" for h in range(24)]
            title = "Usage by hour"
        else:
            days = [rep.start + dt.timedelta(n) for n in range(len(rep.series))]
            tips = [d.strftime("%A %d %B") for d in days]
            title = "Usage by day"
        self.chart_title.configure(text=title)
        self.chart.set(rep.series, tips)

        body = self.apps_card.body
        for w in body.winfo_children():
            w.destroy()
        if not rep.apps:
            tk.Label(body, text="No screen time recorded for this period yet." +
                     ("" if getattr(self, "_tracking", True) else " Turn tracking on above."),
                     bg=T.CARD, fg=T.MUTED).pack(anchor="w")
            return
        shown = rep.apps[:15]
        other = sum(a.seconds for a in rep.apps[15:])
        top_s = shown[0].seconds
        grid = tk.Frame(body, bg=T.CARD)
        grid.pack(fill="x")
        grid.columnconfigure(1, weight=1)
        rows = [(a.name, a.seconds) for a in shown] + ([("Other apps", other)] if other else [])
        for r, (name, secs) in enumerate(rows):
            tk.Label(grid, text=name, bg=T.CARD, fg=T.TEXT, font=T.F_BODY, anchor="w", width=26).grid(
                row=r, column=0, sticky="w", pady=4)
            bar = Bar(grid, width=200, height=10)
            bar.grid(row=r, column=1, sticky="ew", padx=12)
            bar.set(100 * secs / top_s, T.ACCENT if name != "Other apps" else T.MUTED)
            tk.Label(grid, text=fmt_duration(secs), bg=T.CARD, fg=T.TEXT, font=(T.FAMILY, 10, "bold"), width=8,
                     anchor="e").grid(row=r, column=2, sticky="e")
            tk.Label(grid, text=f"{100 * secs / rep.total:.0f}%", bg=T.CARD, fg=T.MUTED, font=T.F_SMALL, width=5,
                     anchor="e").grid(row=r, column=3, sticky="e")


# =============================================================================== Driver updates


class DriversPage(Page):
    def build(self):
        self.header("Driver Updates", "Keep your hardware running fast and stable. Updates come from Windows Update, "
                                      "so every driver is signed and approved by Microsoft for your PC.")
        up = self.card("Available driver updates")
        row = tk.Frame(up.body, bg=T.CARD)
        row.pack(fill="x")
        self.check_btn = Button(row, "↻  Check for driver updates", self.check)
        self.check_btn.pack(side="left")
        self.install_btn = Button(row, "Install selected", self.install, color=T.GOOD)
        self.install_btn.pack(side="left", padx=8)
        self.install_btn.set_enabled(False)
        self.up_status = tk.Label(row, text="Not checked yet", bg=T.CARD, fg=T.MUTED, font=T.F_SMALL)
        self.up_status.pack(side="right")
        self.progress = ttk.Progressbar(up.body, mode="indeterminate")
        self.updates_box = tk.Frame(up.body, bg=T.CARD)
        self.updates_box.pack(fill="x", pady=(12, 0))
        tk.Label(up.body, text="A restore point is created before installing, so you can go back if a driver causes "
                               "trouble. You can also roll back a single driver in Device Manager → the device "
                               "→ Properties → Driver → Roll Back Driver.",
                 bg=T.CARD, fg=T.MUTED, font=T.F_SMALL, wraplength=840, justify="left").pack(anchor="w", pady=(12, 0))
        self.update_vars: dict[str, tk.BooleanVar] = {}
        self.updates = []

        self.gpu_card = self.card("Graphics driver")
        tk.Label(self.gpu_card.body, text="Reading...", bg=T.CARD, fg=T.MUTED).pack(anchor="w")

        self.prob_card = self.card("Devices with problems")
        self.drivers_card = self.card("Installed drivers",
                                      "Drivers from hardware makers (Microsoft's built-in drivers are hidden), oldest "
                                      f"first. Drivers older than {OLD_DRIVER_YEARS} years are highlighted - check the maker's site "
                                      "for a newer one.")
        cols = ("device", "provider", "version", "date")
        self.tree = ttk.Treeview(self.drivers_card.body, columns=cols, show="headings", height=10)
        for col, title, w in (("device", "Device", 300), ("provider", "Maker", 170), ("version", "Version", 120),
                              ("date", "Driver date", 190)):
            self.tree.heading(col, text=title)
            self.tree.column(col, width=w, anchor="w")
        self.tree.tag_configure("old", foreground=T.WARN)
        self.tree.pack(fill="x")
        brow = tk.Frame(self.drivers_card.body, bg=T.CARD)
        brow.pack(fill="x", pady=(10, 0))
        self.backup_btn = Button(brow, "Back up all drivers", self.backup, color=T.CARD_HI, small=True)
        self.backup_btn.pack(side="left")
        Button(brow, "Open Device Manager", lambda: self.backend.run_command("start devmgmt.msc", lambda _l: None),
               color=T.CARD_HI, small=True).pack(side="left", padx=8)
        self.log = self.log_card()
        self._loaded = False
        self.app.on_specs(self._show_gpu)

    def on_show(self):
        if not self._loaded:
            self._loaded = True
            self.load_inventory()

    # ------------------------------------------------------------------ inventory
    def load_inventory(self):
        def work():
            return self.backend.installed_drivers(), self.backend.problem_devices()

        def done(res):
            installed, problems = res
            self._installed = installed
            self.tree.delete(*self.tree.get_children())
            for i, d in enumerate(installed):
                age = d.age_years()
                old = age is not None and age >= OLD_DRIVER_YEARS
                self.tree.insert("", "end", iid=str(i), tags=("old",) if old else (),
                                 values=(d.device, d.provider, d.version,
                                         d.date + (f"  ({age:.0f} years old)" if old else "")))
            self._render_problems(problems)
            if self.app.specs:
                self._show_gpu(self.app.specs, None)

        self.app.bg(work, done, error=lambda e: self.log.write(f"[FAILED] Couldn't read drivers: {e}"),
                    label="Reading drivers")

    def _render_problems(self, problems):
        body = self.prob_card.body
        for w in body.winfo_children():
            w.destroy()
        real = [p for p in problems if p.severity == "problem"]
        if not problems:
            tk.Label(body, text="✔  All devices are working properly.", bg=T.CARD, fg=T.GOOD).pack(anchor="w")
            return
        for p in sorted(problems, key=lambda p: p.severity != "problem"):
            row = tk.Frame(body, bg=T.CARD)
            row.pack(fill="x", pady=3)
            icon, color = ("⚠", T.WARN) if p.severity == "problem" else ("○", T.MUTED)
            tk.Label(row, text=f"{icon}  {p.name}", bg=T.CARD, fg=T.TEXT, font=(T.FAMILY, 10, "bold")).pack(side="left")
            tk.Label(row, text=p.problem, bg=T.CARD, fg=color, font=T.F_SMALL).pack(side="right")
        if real:
            tk.Label(body, text="Tip: run 'Check for driver updates' above - Windows Update often has the missing "
                                "driver. Or ask the AI Assistant to help fix these devices.",
                     bg=T.CARD, fg=T.MUTED, font=T.F_SMALL, wraplength=840, justify="left").pack(anchor="w", pady=(8, 0))

    def _show_gpu(self, specs, _rating):
        from ..drivers import gpu_download

        body = self.gpu_card.body
        for w in body.winfo_children():
            w.destroy()
        gpu = specs.primary_gpu
        if not gpu:
            tk.Label(body, text="No graphics card detected.", bg=T.CARD, fg=T.MUTED).pack(anchor="w")
            return
        installed = next((d for d in getattr(self, "_installed", []) if d.device == gpu.name), None)
        row = tk.Frame(body, bg=T.CARD)
        row.pack(fill="x")
        info = tk.Frame(row, bg=T.CARD)
        info.pack(side="left", fill="x", expand=True)
        tk.Label(info, text=gpu.name, bg=T.CARD, fg=T.TEXT, font=(T.FAMILY, 11, "bold")).pack(anchor="w")
        detail = f"Driver {gpu.driver}" if gpu.driver else "Driver version unknown"
        age = installed.age_years() if installed else None
        if installed and installed.date:
            detail += f"  •  released {installed.date}"
        tk.Label(info, text=detail, bg=T.CARD, fg=T.MUTED, font=T.F_SMALL).pack(anchor="w")
        if age is not None:
            months = int(age * 12)
            fresh = months < 4
            tk.Label(info, text=("✔ Recent driver" if fresh else f"⚠ About {months} months old - "
                                 "a newer driver likely adds FPS and fixes for recent games"),
                     bg=T.CARD, fg=T.GOOD if fresh else T.WARN, font=T.F_SMALL).pack(anchor="w", pady=(2, 0))
        link = gpu_download(gpu.name)
        if link:
            vendor, url = link
            Button(row, f"Get latest {vendor} driver", lambda: self.backend.open_url(url), small=True).pack(side="right")
        tk.Label(body, text="Graphics drivers on Windows Update are often months behind. For gaming, install the "
                            "latest one from your graphics card maker (the button opens their official page).",
                 bg=T.CARD, fg=T.MUTED, font=T.F_SMALL, wraplength=840, justify="left").pack(anchor="w", pady=(10, 0))

    # ------------------------------------------------------------------ updates
    def check(self):
        self.check_btn.set_enabled(False)
        self.install_btn.set_enabled(False)
        self.up_status.configure(text="Checking Windows Update - this can take a few minutes...", fg=T.TEXT)
        self.progress.pack(fill="x", pady=(12, 0), before=self.updates_box)
        self.progress.start(12)

        def done(updates):
            self._stop_progress()
            self.updates = updates
            self._render_updates()

        def error(exc):
            self._stop_progress()
            self.up_status.configure(text="Check failed", fg=T.BAD)
            self.log.write(f"[FAILED] {exc}")

        self.app.bg(self.backend.search_driver_updates, done, error, label="Checking for drivers")

    def _stop_progress(self):
        self.progress.stop()
        self.progress.pack_forget()
        self.check_btn.set_enabled(True)

    def _render_updates(self):
        for w in self.updates_box.winfo_children():
            w.destroy()
        self.update_vars = {}
        if not self.updates:
            self.up_status.configure(text=f"Checked {time.strftime('%H:%M')}", fg=T.MUTED)
            tk.Label(self.updates_box, text="✔  Your drivers are up to date - Windows Update has nothing new.",
                     bg=T.CARD, fg=T.GOOD).pack(anchor="w")
            return
        self.up_status.configure(text=f"{len(self.updates)} update{'s' if len(self.updates) > 1 else ''} available",
                                 fg=T.WARN)
        for i, u in enumerate(self.updates):
            if i:
                tk.Frame(self.updates_box, bg=T.BORDER, height=1).pack(fill="x", pady=7)
            var = tk.BooleanVar(value=True)
            var.trace_add("write", lambda *_: self.install_btn.set_enabled(any(v.get() for v in self.update_vars.values())))
            self.update_vars[u.id] = var
            title = u.model or u.title
            desc = " • ".join(x for x in (u.manufacturer, u.device_class.title(), f"driver date {u.date}" if u.date
                                               else "", human(u.size_bytes) if u.size_bytes else "") if x)
            ToggleRow(self.updates_box, title, f"{u.title}\n{desc}", var).pack(fill="x")
        self.install_btn.set_enabled(True)

    def install(self):
        ids = [i for i, v in self.update_vars.items() if v.get()]
        if not ids or not self.confirm("Install drivers", f"Install {len(ids)} driver update(s)?\n\nA restore point "
                                       "is created first. Your screen or network may blink while drivers install, "
                                       "and a restart may be needed."):
            return
        self.install_btn.set_enabled(False)
        self.check_btn.set_enabled(False)
        log = self.app.logger(self.log)
        b = self.backend

        def work():
            restore = next(c for c in COMMANDS if c.id == "restore_point")
            log("> Creating a restore point...")
            if b.run_command(restore.command, lambda _l: None) != 0:
                log("[FAILED] Couldn't create a restore point (System Protection may be off) - continuing")
            else:
                log("[OK] Restore point created")
            log("> Installing driver updates from Windows Update...")
            return b.install_driver_updates(ids, log)

        def done(res):
            self.check_btn.set_enabled(True)
            if res.missing:
                self.log.write("[FAILED] Those updates are no longer offered - check again.")
            for t in res.installed:
                self.log.write(f"[OK] Installed {t}")
            for t in res.failed:
                self.log.write(f"[FAILED] {t}")
            if res.reboot:
                self.log.write("> Restart your PC to finish installing the drivers.")
            self.load_inventory()
            self.check()

        def error(exc):
            self.check_btn.set_enabled(True)
            self.install_btn.set_enabled(True)
            self.log.write(f"[FAILED] {exc}")

        self.app.bg(work, done, error, label="Installing drivers")

    def backup(self):
        self.backup_btn.set_enabled(False)
        log = self.app.logger(self.log)
        self.log.write("> Backing up your drivers...")

        def done(res):
            code, folder = res
            self.backup_btn.set_enabled(True)
            self.log.write(f"[OK] Drivers backed up to {folder}" if code == 0 else
                           f"[FAILED] Backup finished with errors (code {code})")

        self.app.bg(lambda: self.backend.backup_drivers(log), done,
                    error=lambda e: (self.backup_btn.set_enabled(True), self.log.write(f"[FAILED] {e}")),
                    label="Backing up drivers")


# =============================================================================== Registry editor


class ValueDialog(tk.Toplevel):
    """Create or edit one registry value."""

    def __init__(self, parent, name="", kind="SZ", text="", new=True):
        from ..registry import KINDS

        super().__init__(parent)
        self.result = None
        self.title("New value" if new else "Edit value")
        self.configure(bg=T.CARD)
        self.transient(parent)
        body = tk.Frame(self, bg=T.CARD, padx=20, pady=16)
        body.pack(fill="both", expand=True)
        tk.Label(body, text="Name", bg=T.CARD, fg=T.MUTED, font=T.F_SMALL).grid(row=0, column=0, sticky="w")
        self.name = tk.Entry(body, bg="#0b0d12", fg=T.TEXT, insertbackground=T.TEXT, relief="flat", font=T.F_MONO,
                             width=52)
        self.name.insert(0, name)
        if not new:
            self.name.configure(state="readonly", readonlybackground="#0b0d12")
        self.name.grid(row=1, column=0, columnspan=2, sticky="ew", ipady=5, pady=(2, 10))
        tk.Label(body, text="Type", bg=T.CARD, fg=T.MUTED, font=T.F_SMALL).grid(row=2, column=0, sticky="w")
        self.kind = ttk.Combobox(body, values=KINDS, state="readonly", width=14)
        self.kind.set(kind if kind in KINDS else "SZ")
        self.kind.grid(row=3, column=0, sticky="w", pady=(2, 10))
        self.hint = tk.Label(body, text="", bg=T.CARD, fg=T.MUTED, font=T.F_SMALL)
        self.hint.grid(row=3, column=1, sticky="w", padx=10)
        tk.Label(body, text="Data", bg=T.CARD, fg=T.MUTED, font=T.F_SMALL).grid(row=4, column=0, sticky="w")
        self.data = tk.Text(body, height=5, width=52, bg="#0b0d12", fg=T.TEXT, insertbackground=T.TEXT,
                            relief="flat", font=T.F_MONO, wrap="word")
        self.data.insert("1.0", text)
        self.data.grid(row=5, column=0, columnspan=2, sticky="ew", pady=(2, 0))
        self.err = tk.Label(body, text="", bg=T.CARD, fg=T.BAD, font=T.F_SMALL)
        self.err.grid(row=6, column=0, columnspan=2, sticky="w", pady=(6, 0))
        row = tk.Frame(body, bg=T.CARD)
        row.grid(row=7, column=0, columnspan=2, sticky="e", pady=(10, 0))
        Button(row, "Save", self._save).pack(side="right")
        Button(row, "Cancel", self.destroy, color=T.CARD_HI).pack(side="right", padx=8)
        self.kind.bind("<<ComboboxSelected>>", lambda _: self._hint())
        self._hint()
        self.bind("<Escape>", lambda _: self.destroy())
        self.grab_set()
        (self.name if new else self.data).focus_set()

    def _hint(self):
        self.hint.configure(text={"DWORD": "number, e.g. 1 or 0x10", "QWORD": "number, e.g. 1 or 0x10",
                                  "BINARY": "hex bytes, e.g. 90 12 03 80", "MULTI_SZ": "one string per line",
                                  "EXPAND_SZ": "text, %VARIABLES% allowed"}.get(self.kind.get(), "text"))

    def _save(self):
        from ..regedit import parse_value

        kind, text = self.kind.get(), self.data.get("1.0", "end").rstrip("\n")
        try:
            parse_value(kind, text)
        except ValueError as exc:
            self.err.configure(text=str(exc))
            return
        self.result = (self.name.get(), kind, text)
        self.grab_release()
        self.destroy()


class RegistryPage(Page):
    def build(self):
        from ..regedit import FAVORITES
        from ..registry import HIVES

        self.header("Registry", "View and change Windows registry settings. Every change is backed up first and "
                                "can be undone in Backup & Restore.", T.WARN)
        nav = self.card()
        row = tk.Frame(nav.body, bg=T.CARD)
        row.pack(fill="x")
        Button(row, "↑", self.up, color=T.CARD_HI, small=True).pack(side="left", padx=(0, 6))
        self.hive = ttk.Combobox(row, values=HIVES, state="readonly", width=6)
        self.hive.set("HKCU")
        self.hive.pack(side="left")
        self.key = tk.Entry(row, bg="#0b0d12", fg=T.TEXT, insertbackground=T.TEXT, relief="flat", font=T.F_MONO)
        self.key.pack(side="left", fill="x", expand=True, ipady=6, padx=8)
        self.key.bind("<Return>", lambda _: self.go())
        Button(row, "Go", self.go, small=True).pack(side="left")
        fav = tk.Frame(nav.body, bg=T.CARD)
        fav.pack(fill="x", pady=(10, 0))
        tk.Label(fav, text="Jump to:", bg=T.CARD, fg=T.MUTED, font=T.F_SMALL).pack(side="left", padx=(0, 6))
        for i, (label, hive, key) in enumerate(FAVORITES):
            if i == 4:
                fav = tk.Frame(nav.body, bg=T.CARD)
                fav.pack(fill="x", pady=(6, 0))
                tk.Label(fav, text="", bg=T.CARD, width=8).pack(side="left")
            Button(fav, label, lambda h=hive, k=key: self.open(h, k), color=T.CARD_HI, small=True).pack(
                side="left", padx=(0, 6))
        self.danger = tk.Label(nav.body, text="", bg=T.CARD, fg=T.BAD, font=(T.FAMILY, 9, "bold"), wraplength=840,
                               justify="left")
        self.danger.pack(anchor="w", pady=(8, 0))

        panes = tk.Frame(self.body, bg=T.BG)
        panes.pack(fill="x", pady=(0, 16))
        panes.columnconfigure(0, weight=1, uniform="p")
        panes.columnconfigure(1, weight=2, uniform="p")
        left = Card(panes, "Subkeys", "Double-click to open")
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        self.subkeys = ttk.Treeview(left.body, columns=("k",), show="", height=14, selectmode="browse")
        self.subkeys.column("k", anchor="w")
        self.subkeys.pack(fill="both", expand=True)
        self.subkeys.bind("<Double-1>", lambda _: self._enter_subkey())
        self.subkeys.bind("<Return>", lambda _: self._enter_subkey())
        right = Card(panes, "Values", "Double-click a value to edit it")
        right.grid(row=0, column=1, sticky="nsew", padx=(8, 0))
        self.values = ttk.Treeview(right.body, columns=("name", "type", "data"), show="headings", height=12)
        for col, title, w in (("name", "Name", 180), ("type", "Type", 90), ("data", "Data", 300)):
            self.values.heading(col, text=title)
            self.values.column(col, width=w, anchor="w")
        self.values.pack(fill="both", expand=True)
        self.values.bind("<Double-1>", lambda _: self.edit())
        btns = tk.Frame(right.body, bg=T.CARD)
        btns.pack(fill="x", pady=(10, 0))
        Button(btns, "New value", self.new, small=True).pack(side="left")
        Button(btns, "Edit", self.edit, color=T.CARD_HI, small=True).pack(side="left", padx=6)
        Button(btns, "Delete", self.delete, color=T.BAD, small=True).pack(side="left")
        Button(btns, "Open in Registry Editor", lambda: self.backend.open_in_regedit(*self._here()),
               color=T.CARD_HI, small=True).pack(side="right")
        self.log = self.log_card(height=6)
        self._vals = {}
        self.open("HKCU", r"Control Panel\Desktop")

    # ------------------------------------------------------------------ navigation
    def _here(self):
        return self.hive.get(), self.key.get().strip().strip("\\")

    def open(self, hive, key):
        from ..regedit import is_dangerous

        self.hive.set(hive)
        self.key.delete(0, "end")
        self.key.insert(0, key)
        self.danger.configure(text="⚠ Careful: wrong values here can stop Windows from starting. Change things "
                                   "only if you know exactly what they do." if is_dangerous(key) else "")
        reg = self.backend.registry

        def done(res):
            subkeys, values = res
            self.subkeys.delete(*self.subkeys.get_children())
            for sk in subkeys:
                self.subkeys.insert("", "end", iid=sk, values=("\u25B8 " + sk,))
            self._render_values(values)

        self.app.bg(lambda: (reg.subkeys(hive, key), reg.values(hive, key)), done, label="Reading registry")

    def _render_values(self, values):
        from ..regedit import format_value

        self._vals = values
        self.values.delete(*self.values.get_children())
        for name in sorted(values, key=str.lower):
            value, kind = values[name]
            self.values.insert("", "end", iid="v:" + name, values=(name or "(Default)", kind,
                                                                   format_value(value, kind)))

    def go(self):
        from ..regedit import parse_path

        text = self.key.get().strip()
        try:
            hive, key = parse_path(text) if re.match(r"^(computer\\)?hk", text, re.I) else (self.hive.get(), text)
        except ValueError as exc:
            self.log.write(f"[FAILED] {exc}")
            return
        self.open(hive, key.strip("\\"))

    def up(self):
        hive, key = self._here()
        self.open(hive, key.rsplit("\\", 1)[0] if "\\" in key else "")

    def _enter_subkey(self):
        sel = self.subkeys.selection()
        if sel:
            hive, key = self._here()
            self.open(hive, f"{key}\\{sel[0]}" if key else sel[0])

    # ------------------------------------------------------------------ editing
    def _selected(self):
        sel = self.values.selection()
        return sel[0][2:] if sel else None

    def _apply(self, action, *args, summary):
        from ..regedit import is_dangerous

        hive, key = self._here()
        warn = "\n\n⚠ This key is critical to Windows. A wrong value can stop your PC from starting." \
            if is_dangerous(key) else ""
        if not self.confirm("Change registry", f"{summary}\n\nIn: {hive}\\{key}\n\nThe old value is backed up and "
                                               f"can be undone in Backup & Restore.{warn}"):
            return
        ed = self.backend.regedit
        fn = ed.set_value if action == "set" else ed.delete_value

        def done(_):
            self.log.write(f"[OK] {summary} (undo it in Backup & Restore)")
            self.open(hive, key)

        self.app.bg(lambda: fn(hive, key, *args), done, error=lambda e: self.log.write(f"[FAILED] {e}"),
                    label="Changing registry")

    def new(self):
        dlg = ValueDialog(self.winfo_toplevel())
        self.wait_window(dlg)
        if dlg.result:
            name, kind, text = dlg.result
            if name in self._vals and not self.confirm("Replace value", f"'{name}' already exists. Replace it?"):
                return
            self._apply("set", name, kind, text, summary=f"Set {name or '(Default)'} ({kind})")

    def edit(self):
        from ..regedit import edit_text
        from ..registry import KINDS

        name = self._selected()
        if name is None:
            return
        value, kind = self._vals[name]
        if kind not in KINDS:
            self.log.write("[FAILED] This value has a special type. Use 'Open in Registry Editor' to change it.")
            return
        dlg = ValueDialog(self.winfo_toplevel(), name, kind, edit_text(value, kind), new=False)
        self.wait_window(dlg)
        if dlg.result:
            _, new_kind, text = dlg.result
            self._apply("set", name, new_kind, text, summary=f"Change {name or '(Default)'} to {text[:60]!r}")

    def delete(self):
        name = self._selected()
        if name is not None:
            self._apply("delete", name, summary=f"Delete {name or '(Default)'}")


# =============================================================================== Health check


class HealthPage(Page):
    STYLE = {"bad": ("✖", "PROBLEM", T.BAD), "warn": ("⚠", "IMPROVE", T.WARN),
             "info": ("ℹ", "INFO", T.ACCENT), "ok": ("✔", "GOOD", T.GOOD)}

    def build(self):
        self.header("Health Check", "Finds the settings and conditions that cost your PC the most performance - "
                                    "many of them outside Windows, like your BIOS and monitor.", T.GOOD)
        top = self.card()
        row = tk.Frame(top.body, bg=T.CARD)
        row.pack(fill="x")
        self.summary = tk.Label(row, text="Not checked yet", bg=T.CARD, fg=T.TEXT, font=T.F_H2)
        self.summary.pack(side="left")
        self.check_btn = Button(row, "↻  Run health check", self.run_check, color=T.CARD_HI)
        self.check_btn.pack(side="right")
        self.fix_btn = Button(row, "✔  Fix everything", self.fix_everything, color=T.GOOD)
        self.fix_btn.pack(side="right", padx=8)
        self.manual_card = self.card("Steps only you can do")
        self.manual_card.pack_forget()
        self.list_card = self.card("Results")
        self.log = self.log_card(height=6)
        self._checked = False

    def on_show(self):
        if not self._checked and self.app.specs is not None:
            self.run_check()

    def run_check(self):
        from ..health import evaluate

        specs = self.app.specs
        if specs is None:
            self.summary.configure(text="Still reading your hardware - try again in a moment.")
            return
        self._checked = True
        self.check_btn.set_enabled(False)
        self.summary.configure(text="Checking...", fg=T.MUTED)
        self.app.bg(lambda: evaluate(self.backend.health_facts(specs)), self.render,
                    error=lambda e: (self.check_btn.set_enabled(True), self.log.write(f"[FAILED] {e}")),
                    label="Running health check")

    def render(self, findings):
        from ..fixall import manual_steps
        from ..health import score

        self._findings = findings
        self.check_btn.set_enabled(True)
        steps = manual_steps(findings)
        for w in self.manual_card.body.winfo_children():
            w.destroy()
        if steps:
            self.manual_card.pack(fill="x", pady=(0, 16), before=self.list_card)
            for st in steps:
                tk.Label(self.manual_card.body, text="•  " + st, bg=T.CARD, fg=T.TEXT, wraplength=820,
                         justify="left").pack(anchor="w", pady=2)
            bios = next((f for f in findings if f.fix == "bios" and f.status in ("warn", "bad")), None)
            if bios:
                Button(self.manual_card.body, "Restart into BIOS", lambda: self.fix(bios), small=True).pack(
                    anchor="w", pady=(8, 0))
        else:
            self.manual_card.pack_forget()
        bad, warn = score(findings)
        if bad or warn:
            parts = [f"{bad} problem{'s' if bad != 1 else ''}" if bad else "",
                     f"{warn} thing{'s' if warn != 1 else ''} to improve" if warn else ""]
            self.summary.configure(text=" and ".join(p for p in parts if p) + " found",
                                   fg=T.BAD if bad else T.WARN)
        else:
            self.summary.configure(text="✔ Everything looks good", fg=T.GOOD)
        body = self.list_card.body
        for w in body.winfo_children():
            w.destroy()
        for i, f in enumerate(findings):
            if i:
                tk.Frame(body, bg=T.BORDER, height=1).pack(fill="x", pady=8)
            icon, tag, color = self.STYLE[f.status]
            row = tk.Frame(body, bg=T.CARD)
            row.pack(fill="x")
            txt = tk.Frame(row, bg=T.CARD)
            txt.pack(side="left", fill="x", expand=True)
            head = tk.Frame(txt, bg=T.CARD)
            head.pack(anchor="w")
            tk.Label(head, text=f"{icon}  {f.title}", bg=T.CARD, fg=T.TEXT, font=(T.FAMILY, 10, "bold")).pack(side="left")
            tk.Label(head, text=f" {tag} ", bg=T.CARD_HI, fg=color, font=(T.FAMILY, 8, "bold")).pack(side="left", padx=8)
            tk.Label(txt, text=f.detail, bg=T.CARD, fg=T.MUTED, font=T.F_SMALL, wraplength=640,
                     justify="left").pack(anchor="w", pady=(2, 0))
            if f.fix:
                Button(row, f.fix_label, lambda f=f: self.fix(f), small=True,
                       color=T.ACCENT if f.status in ("bad", "warn") else T.CARD_HI).pack(side="right", padx=(12, 0))

    def fix_everything(self):
        from .. import fixall

        dlg = FixAllDialog(self.winfo_toplevel())
        self.wait_window(dlg)
        if not dlg.result:
            return
        self.fix_btn.set_enabled(False)
        self.check_btn.set_enabled(False)
        log = self.app.logger(self.log)
        self.log.write("> Fix everything started - this can take several minutes")

        def done(res):
            self.fix_btn.set_enabled(True)
            self.check_btn.set_enabled(True)
            self.log.write(f"> Done: {len(res.done)} fixed, {len(res.skipped)} not needed, {len(res.failed)} failed")
            self.run_check()
            if res.needs_restart and self.confirm("Restart now?", "Some changes need a restart. Save your work, "
                                                  "then click Yes to restart in 10 seconds."):
                self.backend.restart_pc()

        def error(exc):
            self.fix_btn.set_enabled(True)
            self.check_btn.set_enabled(True)
            self.log.write(f"[FAILED] {exc}")

        self.app.bg(lambda: fixall.run(self.backend, dlg.result, log), done, error, label="Fixing everything")

    def fix(self, f):
        action = f.fix
        if action.startswith("page:"):
            self.app.show(action[5:])
        elif action.startswith("refresh:"):
            device, hz = action[len("refresh:"):].rsplit(":", 1)

            def done(res):
                ok, msg = res
                self.log.write(f"[OK] Monitor switched to {hz} Hz" if ok else f"[FAILED] {msg}")
                self.run_check()

            self.app.bg(lambda: self.backend.set_refresh_rate(device, int(hz)), done, label="Changing refresh rate")
        elif action == "bios":
            if self.confirm("Restart into BIOS", "Your PC will restart straight into the BIOS/UEFI setup in 5 "
                                                 "seconds. Save your work first.\n\nLook for 'XMP', 'EXPO' or "
                                                 "'DOCP' (memory) and 'Re-Size BAR' / 'Above 4G Decoding' "
                                                 "(often under Advanced or AI Tweaker). Save and exit with F10."):
                code = self.backend.restart_to_bios()
                self.log.write("> Restarting into BIOS..." if code == 0 else
                               "[FAILED] This PC can't restart straight into the BIOS. Restart and press Del or F2 "
                               "while it starts.")
        elif action == "restart":
            if self.confirm("Restart", "Restart your PC in 10 seconds? Save your work first."):
                self.backend.restart_pc()
                self.log.write("> Restarting in 10 seconds (run 'shutdown /a' to cancel)")


class FixAllDialog(tk.Toplevel):
    def __init__(self, parent):
        from ..fixall import ACTIONS

        super().__init__(parent)
        self.result = None
        self.title("Fix everything")
        self.configure(bg=T.CARD)
        self.transient(parent)
        self.resizable(False, False)
        body = tk.Frame(self, bg=T.CARD, padx=22, pady=18)
        body.pack(fill="both", expand=True)
        tk.Label(body, text="✔ Fix everything", bg=T.CARD, fg=T.TEXT, font=T.F_H2).pack(anchor="w")
        tk.Label(body, text="Applies every safe recommendation. Untick anything you want to skip.", bg=T.CARD,
                 fg=T.MUTED, font=T.F_SMALL).pack(anchor="w", pady=(2, 12))
        self.vars = {}
        for key, title, desc in ACTIONS:
            self.vars[key] = tk.BooleanVar(value=True)
            ToggleRow(body, title, desc, self.vars[key]).pack(fill="x", pady=4)
        tk.Label(body, text="Not included: BIOS settings (you do those yourself - see 'Steps only you can do') and "
                            "the optional security trade-offs (Memory Integrity, Defender exclusions).",
                 bg=T.CARD, fg=T.MUTED, font=T.F_SMALL, wraplength=520, justify="left").pack(anchor="w", pady=(10, 0))
        row = tk.Frame(body, bg=T.CARD)
        row.pack(fill="x", pady=(14, 0))
        Button(row, "Fix now", self._ok, color=T.GOOD).pack(side="right")
        Button(row, "Cancel", self.destroy, color=T.CARD_HI).pack(side="right", padx=8)
        self.bind("<Escape>", lambda _: self.destroy())
        self.update_idletasks()
        x = parent.winfo_rootx() + (parent.winfo_width() - self.winfo_width()) // 2
        y = parent.winfo_rooty() + (parent.winfo_height() - self.winfo_height()) // 3
        self.geometry(f"+{max(0, x)}+{max(0, y)}")
        self.grab_set()

    def _ok(self):
        chosen = {k for k, v in self.vars.items() if v.get()}
        self.result = chosen or None
        self.grab_release()
        self.destroy()
