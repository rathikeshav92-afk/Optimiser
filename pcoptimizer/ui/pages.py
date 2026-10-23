"""All application pages."""

from __future__ import annotations

import os
import time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from .. import benchmark, profiles
from ..cleaner import human
from ..commands import COMMANDS
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
        Button(row, "♲  Clean junk", lambda: self.app.show("cleaner"), color=T.CARD_HI).pack(side="left", padx=(0, 10))
        self.ram_btn = Button(row, "Free up RAM", self.free_ram, color=T.CARD_HI)
        self.ram_btn.pack(side="left")
        tk.Label(actions.body, text="One-Click Boost creates a restore point, applies every recommended speed tweak, "
                                    "cleans junk files, frees RAM and flushes DNS. Everything can be undone.",
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
        if not self.confirm("One-Click Boost",
                            "This will:\n\n• Create a system restore point\n• Apply all recommended speed tweaks\n"
                            "• Clean temporary/junk files\n• Free up RAM and flush DNS\n\n"
                            "Every change can be undone from 'Backup & Restore'. Continue?"):
            return
        self.boost_btn.set_enabled(False)
        log = self.app.logger(self.log)
        b, mgr = self.backend, self.backend.tweaks

        def work():
            restore = next(c for c in COMMANDS if c.id == "restore_point")
            log("> Creating restore point (can take a minute)...")
            code = b.run_command(restore.command, lambda _l: None)
            log("[OK] Restore point created" if code == 0 else "[FAILED] Restore point (System Protection may be off)")
            log("> Applying speed tweaks...")
            for t in mgr.in_category("speed"):
                if t.recommended:
                    try:
                        mgr.apply(t.id)
                        log(f"[OK] {t.title}")
                    except Exception as exc:
                        log(f"[FAILED] {t.title}: {exc}")
            log("> Cleaning junk files...")
            freed = 0
            for target in b.clean_targets:
                if target.recommended:
                    try:
                        freed += b.clean(target, log)
                    except Exception as exc:
                        log(f"[FAILED] {target.title}: {exc}")
            log("> Freeing RAM...")
            log(f"[OK] Trimmed {b.trim_ram()} processes")
            log("[OK] DNS cache flushed" if b.flush_dns() else "[FAILED] DNS flush")
            return freed

        def done(freed):
            self.boost_btn.set_enabled(True)
            self.log.write(f"> Boost complete - {human(freed)} freed. Restart your PC to finish applying tweaks.")

        self.app.bg(work, done, error=lambda e: (self.boost_btn.set_enabled(True), self.log.write(f"ERROR: {e}")),
                    label="Boosting")


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
        self.load_startup()

    def recommended(self):
        for t in self.list.tweaks:
            self.list.vars[t.id].set(t.recommended)

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
                    log(f"[OK] {mgr.by_id[i].title}")
                except Exception as exc:
                    log(f"[FAILED] {mgr.by_id[i].title}: {exc}")

        def done(_):
            self.apply_btn.set_enabled(True)
            if any(mgr.by_id[i].needs_restart for i in ids):
                self.log.write("> Some tweaks need a restart (or sign out) to take effect.")
            self.list.refresh()

        self.app.bg(work, done, label="Applying tweaks")

    def undo(self):
        ids = self.list.selected()
        mgr, log = self.backend.tweaks, self.app.logger(self.log)

        def work():
            for i in ids:
                if mgr.revert(i):
                    log(f"[OK] Restored original: {mgr.by_id[i].title}")

        self.app.bg(work, lambda _: self.list.refresh(), label="Restoring")

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
                btn = Button(row, "Run", lambda c=c: self.run(c.command, c.title, c.needs_restart), small=True)
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
                tk.Label(txt, text=c.command, bg=T.CARD, fg=T.ACCENT, font=T.F_MONO, justify="left",
                         wraplength=700).pack(anchor="w", pady=(2, 0))

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
        ttk.Checkbutton(row, text="  Switch to Gaming Mode when launching", variable=self.auto_gaming).pack(side="right")

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
            for key, title, _ in self.OPTIONS:
                var = tk.BooleanVar(value=getattr(game, key))
                ttk.Checkbutton(opts, text=" " + title, variable=var,
                                command=lambda g=game, k=key, v=var: self.set_option(g, k, v.get())).pack(side="left", padx=(0, 14))
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

    def set_option(self, game, key, value):
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
        self.log = self.log_card()

    def on_show(self):
        self.list.refresh()

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
