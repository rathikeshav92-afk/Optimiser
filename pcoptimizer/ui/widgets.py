"""Reusable dark-themed widgets."""

from __future__ import annotations

import math
import tkinter as tk
from tkinter import ttk
from typing import Callable

from . import theme as T


class Button(tk.Label):
    """Flat button with hover, press and keyboard focus states (tk.Button can't be styled
    consistently on Windows). Surface colours (CARD / CARD_HI) give an outlined secondary button."""

    def __init__(self, parent, text, command=None, color=T.ACCENT, fg=None, small=False, **kw):
        secondary = color in (T.CARD, T.CARD_HI)
        self._color = T.CARD_HI if secondary else color
        self._fg = fg or (T.TEXT if secondary else T.on_color(self._color))
        self._edge = T.BORDER if secondary else self._color
        super().__init__(parent, text=text, bg=self._color, fg=self._fg, cursor="hand2", takefocus=1,
                         font=(T.FAMILY_SEMI, 9 if small else 10), padx=12 if small else 18,
                         pady=5 if small else 9, highlightthickness=1, highlightbackground=self._edge,
                         highlightcolor=T.FOCUS, **kw)
        self._hover = T.HOVER if secondary else _shade(self._color, 1.12)
        self._press = T.BORDER if secondary else _shade(self._color, 0.85)
        self._command, self._enabled, self._inside = command, True, False
        self.bind("<Enter>", lambda _: self._set_inside(True))
        self.bind("<Leave>", lambda _: self._set_inside(False))
        self.bind("<ButtonPress-1>", lambda _: self._enabled and self.configure(bg=self._press))
        self.bind("<ButtonRelease-1>", self._release)
        self.bind("<Return>", lambda _: self._fire())
        self.bind("<space>", lambda _: self._fire())

    def _set_inside(self, inside: bool) -> None:
        self._inside = inside
        if self._enabled:
            self.configure(bg=self._hover if inside else self._color)

    def _release(self, _event) -> None:
        # Fire on release over the button, like native buttons, so a press can be cancelled by dragging off.
        if self._enabled:
            self.configure(bg=self._hover if self._inside else self._color)
            if self._inside:
                self._fire()

    def _fire(self) -> None:
        if self._enabled and self._command:
            self._command()

    def set_enabled(self, enabled: bool) -> None:
        self._enabled = enabled
        self.configure(bg=self._color if enabled else T.CARD_HI, fg=self._fg if enabled else T.MUTED,
                       highlightbackground=self._edge if enabled else T.BORDER,
                       cursor="hand2" if enabled else "arrow", takefocus=1 if enabled else 0)


def _shade(hex_color: str, factor: float) -> str:
    r, g, b = (int(hex_color[i:i + 2], 16) for i in (1, 3, 5))
    return "#%02x%02x%02x" % tuple(min(255, int(c * factor)) for c in (r, g, b))


class Card(tk.Frame):
    def __init__(self, parent, title: str | None = None, subtitle: str | None = None, **kw):
        super().__init__(parent, bg=T.CARD, highlightthickness=1, highlightbackground=T.BORDER, **kw)
        self.body = tk.Frame(self, bg=T.CARD)
        if title:
            head = tk.Frame(self, bg=T.CARD)
            head.pack(fill="x", padx=20, pady=(18, 0))
            tk.Label(head, text=title, bg=T.CARD, fg=T.TEXT, font=T.F_H2).pack(anchor="w")
            if subtitle:
                tk.Label(head, text=subtitle, bg=T.CARD, fg=T.MUTED, font=T.F_SMALL, justify="left",
                         wraplength=700).pack(anchor="w", pady=(3, 0))
        self.body.pack(fill="both", expand=True, padx=20, pady=(14, 18))


class ScoreGauge(tk.Canvas):
    def __init__(self, parent, size=190, **kw):
        super().__init__(parent, width=size, height=size, bg=T.CARD, highlightthickness=0, **kw)
        self.size = size
        self.set(None)

    def set(self, score: int | None, grade: str = "") -> None:
        self.delete("all")
        s, pad, w = self.size, 14, 12
        self.create_arc(pad, pad, s - pad, s - pad, start=-45, extent=270, style="arc", width=w, outline=T.CARD_HI)
        if score is None:
            self.create_text(s / 2, s / 2, text="...", fill=T.MUTED, font=T.F_BIG)
            return
        color = T.score_color(score)
        extent = -270 * max(0, min(100, score)) / 100
        self.create_arc(pad, pad, s - pad, s - pad, start=225, extent=extent, style="arc", width=w, outline=color)
        # Round the arc's two ends so the meter reads as one smooth stroke.
        r, cx, cy = (s - 2 * pad) / 2, s / 2, s / 2
        for deg in (225, 225 + extent):
            x, y = cx + r * math.cos(math.radians(deg)), cy - r * math.sin(math.radians(deg))
            self.create_oval(x - w / 2, y - w / 2, x + w / 2, y + w / 2, fill=color, outline="")
        self.create_text(s / 2, s / 2 - 10, text=str(score), fill=T.TEXT, font=(T.FAMILY_SEMI, 36))
        self.create_text(s / 2, s / 2 + 24, text="out of 100", fill=T.MUTED, font=T.F_SMALL)
        self.create_text(s / 2, s - 20, text=grade, fill=color, font=(T.FAMILY_SEMI, 11))


class Bar(tk.Canvas):
    """Thin horizontal meter."""

    def __init__(self, parent, width=220, height=8, bg=T.CARD, **kw):
        super().__init__(parent, width=width, height=height, bg=bg, highlightthickness=0, **kw)
        self.w, self.h = width, height
        self.bind("<Configure>", lambda e: self._resize(e.width))
        self._pct, self._color = 0.0, T.ACCENT

    def _resize(self, width):
        self.w = width
        self.set(self._pct, self._color)

    def set(self, pct: float, color: str | None = None) -> None:
        self._pct, self._color = pct, color or self._color
        self.delete("all")
        self._pill(0, self.w, T.CARD_HI)
        filled = self.w * max(0, min(100, pct)) / 100
        if filled > 0:
            self._pill(0, max(filled, self.h), self._color)

    def _pill(self, x0: float, x1: float, color: str) -> None:
        """Rectangle with fully rounded ends."""
        h = self.h
        self.create_oval(x0, 0, x0 + h, h, fill=color, outline="")
        self.create_oval(x1 - h, 0, x1, h, fill=color, outline="")
        self.create_rectangle(x0 + h / 2, 0, x1 - h / 2, h, fill=color, width=0)


class ScrollFrame(tk.Frame):
    """A vertically scrolling container; put children in ``.inner``."""

    def __init__(self, parent, bg=T.BG, scrollbar=True, **kw):
        super().__init__(parent, bg=bg, **kw)
        self.canvas = tk.Canvas(self, bg=bg, highlightthickness=0)
        self.scroll = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.inner = tk.Frame(self.canvas, bg=bg)
        self._win = self.canvas.create_window((0, 0), window=self.inner, anchor="nw")
        self.canvas.configure(yscrollcommand=self.scroll.set)
        self.canvas.pack(side="left", fill="both", expand=True)
        if scrollbar:
            self.scroll.pack(side="right", fill="y")
        self.inner.bind("<Configure>", lambda _: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas.bind("<Configure>", lambda e: self.canvas.itemconfigure(self._win, width=e.width))
        self.bind_all("<MouseWheel>", self._wheel, add="+")
        self.bind_all("<Button-4>", lambda e: self._wheel(e, -120), add="+")
        self.bind_all("<Button-5>", lambda e: self._wheel(e, 120), add="+")

    def _wheel(self, event, delta=None):
        if not self.winfo_ismapped():
            return
        widget = self.winfo_containing(event.x_root, event.y_root)
        while widget is not None and widget is not self:
            if isinstance(widget, (tk.Text, ttk.Treeview)):
                return  # let those scroll themselves
            widget = widget.master
        if widget is self:
            d = delta if delta is not None else -event.delta
            self.canvas.yview_scroll(int(d / 120) or (1 if d > 0 else -1), "units")


class LogBox(tk.Frame):
    def __init__(self, parent, height=10, **kw):
        super().__init__(parent, bg=T.CARD, **kw)
        self.text = tk.Text(self, height=height, bg=T.INPUT, fg=T.TEXT_SOFT, insertbackground=T.TEXT,
                            font=T.F_MONO, relief="flat", wrap="word", padx=12, pady=10, state="disabled",
                            highlightthickness=0, spacing1=1, spacing3=1)
        sb = ttk.Scrollbar(self, orient="vertical", command=self.text.yview)
        self.text.configure(yscrollcommand=sb.set)
        self.text.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        for tag, color in (("ok", T.GOOD), ("err", T.BAD), ("head", T.ACCENT_FG)):
            self.text.tag_configure(tag, foreground=color)

    def write(self, line: str) -> None:
        tag = ("ok" if line.startswith("[OK]") else "err" if line.startswith(("[FAILED]", "ERROR")) else
               "head" if line.startswith(">") else None)
        self.text.configure(state="normal")
        self.text.insert("end", line + "\n", tag)
        self.text.see("end")
        self.text.configure(state="disabled")

    def clear(self) -> None:
        self.text.configure(state="normal")
        self.text.delete("1.0", "end")
        self.text.configure(state="disabled")


class ToggleRow(tk.Frame):
    """Title, description and a checkbox - used for tweaks and clean targets."""

    def __init__(self, parent, title, description, var: tk.BooleanVar, status: str = "", status_color=T.MUTED,
                 badges: list[tuple[str, str]] | None = None):
        super().__init__(parent, bg=T.CARD)
        chk = ttk.Checkbutton(self, variable=var)
        chk.grid(row=0, column=0, rowspan=2, sticky="n", padx=(0, 10), pady=2)
        head = tk.Frame(self, bg=T.CARD)
        head.grid(row=0, column=1, sticky="w")
        tk.Label(head, text=title, bg=T.CARD, fg=T.TEXT, font=T.F_LABEL).pack(side="left")
        for text, color in badges or []:
            tk.Label(head, text=text, bg=T.CARD_HI, fg=color, font=T.F_CAPS, padx=6, pady=1).pack(side="left", padx=(8, 0))
        self.desc = tk.Label(self, text=description, bg=T.CARD, fg=T.MUTED, font=T.F_SMALL, justify="left",
                             anchor="w", wraplength=620)
        self.desc.grid(row=1, column=1, sticky="w")
        self.status = tk.Label(self, text=status, bg=T.CARD, fg=status_color, font=(T.FAMILY, 9, "bold"))
        self.status.grid(row=0, column=2, rowspan=2, sticky="e", padx=(10, 0))
        self.columnconfigure(1, weight=1)
        for w in (self, head, self.desc):
            w.bind("<Button-1>", lambda _: var.set(not var.get()))

    def set_status(self, text: str, color: str) -> None:
        self.status.configure(text=text, fg=color)


class Spinner(tk.Canvas):
    """Small rotating arc shown while work runs in the background."""

    def __init__(self, parent, size=14, bg=T.BG, color=T.ACCENT_FG, **kw):
        super().__init__(parent, width=size, height=size, bg=bg, highlightthickness=0, **kw)
        self.size, self.color, self._angle, self._job = size, color, 0, None

    def start(self) -> None:
        if self._job is None:
            self.pack(side="left")
            self._spin()

    def stop(self) -> None:
        if self._job is not None:
            self.after_cancel(self._job)
            self._job = None
        self.delete("all")
        self.pack_forget()

    def _spin(self) -> None:
        s = self.size
        self.delete("all")
        self.create_arc(2, 2, s - 2, s - 2, start=self._angle, extent=270, style="arc", width=2, outline=self.color)
        self._angle = (self._angle - 18) % 360
        self._job = self.after(40, self._spin)


class Toast(tk.Frame):
    """A message card that slides in at the top-right of the window and hides itself after a few seconds."""

    KINDS = {"good": (T.GOOD, "", "✔"), "warn": (T.WARN, "", "⚠"), "bad": (T.BAD, "", "✖"),
             "info": (T.ACCENT_FG, "", "ℹ")}
    WIDTH = 380

    def __init__(self, parent):
        super().__init__(parent, bg=T.CARD_HI, highlightthickness=1, highlightbackground=T.BORDER)
        self._queue: list[tuple] = []
        self._showing = False
        self._hide_job = None
        self.stripe = tk.Frame(self, width=4)
        self.stripe.pack(side="left", fill="y")
        self.icon = tk.Label(self, bg=T.CARD_HI, font=(T.ICON_FONT or T.FAMILY, 13), width=2)
        self.icon.pack(side="left", anchor="n", padx=(10, 2), pady=12)
        body = tk.Frame(self, bg=T.CARD_HI)
        body.pack(side="left", fill="both", expand=True, pady=10)
        self.title = tk.Label(body, bg=T.CARD_HI, fg=T.TEXT, font=T.F_LABEL, anchor="w", justify="left",
                              wraplength=self.WIDTH - 110)
        self.title.pack(fill="x")
        self.msg = tk.Label(body, bg=T.CARD_HI, fg=T.TEXT_SOFT, font=T.F_SMALL, anchor="w", justify="left",
                            wraplength=self.WIDTH - 110)
        self.msg.pack(fill="x", pady=(2, 0))
        self.actions = tk.Frame(body, bg=T.CARD_HI)
        close = tk.Label(self, text="" if T.ICON_FONT else "✕", bg=T.CARD_HI, fg=T.MUTED, cursor="hand2",
                         font=(T.ICON_FONT or T.FAMILY, 9))
        close.pack(side="right", anchor="n", padx=10, pady=12)
        close.bind("<Button-1>", lambda _: self.hide())

    def lift_above(self) -> None:
        if self._showing:
            self.tkraise()

    def show(self, title: str, message: str, kind: str = "info", action: tuple[str, Callable] | None = None,
             seconds: float | None = None) -> None:
        self._queue.append((title, message, kind, action, seconds or (12 if kind in ("warn", "bad") else 6)))
        if self._showing is False:  # None means a message is sliding away; it shows the next one
            self._next()

    def _next(self) -> None:
        if not self._queue:
            self._showing = False
            return
        title, message, kind, action, seconds = self._queue.pop(0)
        color, glyph, fallback = self.KINDS[kind]
        self.stripe.configure(bg=color)
        self.icon.configure(text=glyph if T.ICON_FONT else fallback, fg=color)
        self.title.configure(text=title)
        self.msg.configure(text=message)
        for w in self.actions.winfo_children():
            w.destroy()
        if action:
            label, fn = action
            Button(self.actions, label, lambda: (self.hide(), fn()), small=True).pack(side="left")
            self.actions.pack(fill="x", pady=(8, 0))
        else:
            self.actions.pack_forget()
        self._showing = True
        self.update_idletasks()
        h = self.winfo_reqheight()
        self._slide(-h, 16, h)
        self._hide_job = self.after(int(seconds * 1000), self.hide)

    def _slide(self, start: float, end: float, height: int, step: int = 0, steps: int = 12, then=None) -> None:
        t = step / steps
        ease = 1 - (1 - t) ** 3  # ease-out: fast start, gentle stop
        y = start + (end - start) * ease
        self.place(relx=1, x=-20, y=y, anchor="ne", width=self.WIDTH)
        self.tkraise()
        if step < steps:
            self.after(15, lambda: self._slide(start, end, height, step + 1, steps, then))
        elif then:
            then()

    def hide(self) -> None:
        if not self._showing:
            return
        if self._hide_job:
            self.after_cancel(self._hide_job)
            self._hide_job = None
        self._showing = None  # mid-animation: new messages queue up until this one is gone

        def gone():
            self.place_forget()
            self._next()

        h = self.winfo_height()
        self._slide(16, -h - 4, h, then=gone)
