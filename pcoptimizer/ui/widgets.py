"""Reusable dark-themed widgets."""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from . import theme as T


class Button(tk.Label):
    """Flat button with hover colour (tk.Button can't be styled consistently on Windows)."""

    def __init__(self, parent, text, command=None, color=T.ACCENT, fg="#ffffff", small=False, **kw):
        super().__init__(parent, text=text, bg=color, fg=fg, cursor="hand2",
                         font=(T.FAMILY, 9 if small else 10, "bold"),
                         padx=10 if small else 16, pady=4 if small else 8, **kw)
        self._color, self._command, self._enabled = color, command, True
        self.bind("<Enter>", lambda _: self._enabled and self.configure(bg=_shade(self._color, 1.15)))
        self.bind("<Leave>", lambda _: self.configure(bg=self._color if self._enabled else T.BORDER))
        self.bind("<Button-1>", lambda _: self._enabled and self._command and self._command())

    def set_enabled(self, enabled: bool) -> None:
        self._enabled = enabled
        self.configure(bg=self._color if enabled else T.BORDER, fg="#ffffff" if enabled else T.MUTED,
                       cursor="hand2" if enabled else "arrow")


def _shade(hex_color: str, factor: float) -> str:
    r, g, b = (int(hex_color[i:i + 2], 16) for i in (1, 3, 5))
    return "#%02x%02x%02x" % tuple(min(255, int(c * factor)) for c in (r, g, b))


class Card(tk.Frame):
    def __init__(self, parent, title: str | None = None, subtitle: str | None = None, **kw):
        super().__init__(parent, bg=T.CARD, highlightthickness=1, highlightbackground=T.BORDER, **kw)
        self.body = tk.Frame(self, bg=T.CARD)
        if title:
            head = tk.Frame(self, bg=T.CARD)
            head.pack(fill="x", padx=16, pady=(14, 0))
            tk.Label(head, text=title, bg=T.CARD, fg=T.TEXT, font=T.F_H2).pack(anchor="w")
            if subtitle:
                tk.Label(head, text=subtitle, bg=T.CARD, fg=T.MUTED, font=T.F_SMALL, justify="left",
                         wraplength=700).pack(anchor="w", pady=(2, 0))
        self.body.pack(fill="both", expand=True, padx=16, pady=14)


class ScoreGauge(tk.Canvas):
    def __init__(self, parent, size=190, **kw):
        super().__init__(parent, width=size, height=size, bg=T.CARD, highlightthickness=0, **kw)
        self.size = size
        self.set(None)

    def set(self, score: int | None, grade: str = "") -> None:
        self.delete("all")
        s, pad, w = self.size, 14, 14
        self.create_arc(pad, pad, s - pad, s - pad, start=-45, extent=270, style="arc", width=w, outline=T.CARD_HI)
        if score is None:
            self.create_text(s / 2, s / 2, text="...", fill=T.MUTED, font=T.F_BIG)
            return
        color = T.score_color(score)
        self.create_arc(pad, pad, s - pad, s - pad, start=225, extent=-270 * score / 100, style="arc", width=w,
                        outline=color)
        self.create_text(s / 2, s / 2 - 10, text=str(score), fill=T.TEXT, font=(T.FAMILY, 38, "bold"))
        self.create_text(s / 2, s / 2 + 26, text="/ 100", fill=T.MUTED, font=T.F_BODY)
        self.create_text(s / 2, s - 22, text=grade, fill=color, font=(T.FAMILY, 11, "bold"))


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
        self.create_rectangle(0, 0, self.w, self.h, fill=T.CARD_HI, width=0)
        self.create_rectangle(0, 0, self.w * max(0, min(100, pct)) / 100, self.h, fill=self._color, width=0)


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
        self.text = tk.Text(self, height=height, bg="#0b0d12", fg="#c9d1e0", insertbackground=T.TEXT,
                            font=T.F_MONO, relief="flat", wrap="word", padx=10, pady=8, state="disabled")
        sb = ttk.Scrollbar(self, orient="vertical", command=self.text.yview)
        self.text.configure(yscrollcommand=sb.set)
        self.text.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        for tag, color in (("ok", T.GOOD), ("err", T.BAD), ("head", T.ACCENT)):
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
        tk.Label(head, text=title, bg=T.CARD, fg=T.TEXT, font=(T.FAMILY, 10, "bold")).pack(side="left")
        for text, color in badges or []:
            tk.Label(head, text=f" {text} ", bg=T.CARD_HI, fg=color, font=(T.FAMILY, 8, "bold")).pack(side="left", padx=(6, 0))
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
