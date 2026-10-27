"""Colours, fonts and ttk styling."""

from __future__ import annotations

import sys
import tkinter as tk
from tkinter import font as tkfont
from tkinter import ttk

# Slate dark palette. Text colours are checked against BG/CARD for WCAG AA (4.5:1);
# filled colours (ACCENT, BAD, GAMING) carry white button text at 4.5:1 or better.
BG = "#0b1120"
SIDEBAR = "#0f172a"
CARD = "#131c2e"
CARD_HI = "#1e293b"
HOVER = "#253349"
BORDER = "#263247"
INPUT = "#0a0f1c"
TEXT = "#f1f5f9"
TEXT_SOFT = "#cbd5e1"
MUTED = "#94a3b8"
ACCENT = "#2f6fed"
ACCENT_FG = "#7aa7ff"  # accent for text and icons on dark surfaces
FOCUS = "#e2e8f0"
AI = "#7c3aed"
GAMING = "#e11d48"
BALANCED = "#14b8a6"
GOOD = "#22c55e"
WARN = "#f59e0b"
BAD = "#f87171"

FAMILY = "Segoe UI" if sys.platform == "win32" else "DejaVu Sans"
FAMILY_SEMI = "Segoe UI Semibold" if sys.platform == "win32" else FAMILY
MONO = "Cascadia Mono" if sys.platform == "win32" else "DejaVu Sans Mono"

# Type scale: 9 / 10 / 12 / 20 / 30 pt.
F_TITLE = (FAMILY_SEMI, 20)
F_H2 = (FAMILY_SEMI, 12)
F_BODY = (FAMILY, 10)
F_LABEL = (FAMILY_SEMI, 10)
F_SMALL = (FAMILY, 9)
F_CAPS = (FAMILY_SEMI, 8)
F_BIG = (FAMILY, 30, "bold")
F_MONO = (MONO, 9)

# Windows icon font, picked in setup(); None means fall back to Unicode symbols.
ICON_FONT: str | None = None


def score_color(pct: float) -> str:
    return GOOD if pct >= 70 else WARN if pct >= 45 else BAD


def on_color(hex_color: str) -> str:
    """Readable text colour (white or near-black) for a filled background."""
    r, g, b = (int(hex_color[i:i + 2], 16) / 255 for i in (1, 3, 5))
    lin = [c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in (r, g, b)]
    lum = 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]
    return BG if lum > 0.3 else "#ffffff"


def _dark_title_bar(root: tk.Tk) -> None:
    """Ask Windows 10/11 to draw the window's title bar dark to match the app."""
    if sys.platform != "win32":
        return
    try:
        import ctypes

        root.update_idletasks()
        hwnd = ctypes.windll.user32.GetParent(root.winfo_id())
        on = ctypes.c_int(1)
        for attr in (20, 19):  # DWMWA_USE_IMMERSIVE_DARK_MODE (new id, then pre-20H1 id)
            if ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, attr, ctypes.byref(on), ctypes.sizeof(on)) == 0:
                break
    except Exception:
        pass


def _hex(c: str) -> tuple[int, int, int]:
    return tuple(int(c[i:i + 2], 16) for i in (1, 3, 5))


def _checkbox_image(root: tk.Tk, size: int, fill: str, edge: str, tick: str | None) -> tk.PhotoImage:
    """Draw an anti-aliased rounded checkbox (optionally ticked) without needing Pillow."""
    img = tk.PhotoImage(master=root, width=size, height=size)
    ss, r = 4, size * 0.22  # supersamples per axis, corner radius
    tick_pts = [(0.27, 0.52), (0.43, 0.68), (0.74, 0.34)]
    tick_w = max(1.6, size * 0.12)

    def inside(x, y, inset):
        lo, hi, rr = inset, size - inset, max(0.0, r - inset)
        cx, cy = min(max(x, lo + rr), hi - rr), min(max(y, lo + rr), hi - rr)
        return lo <= x <= hi and lo <= y <= hi and (x - cx) ** 2 + (y - cy) ** 2 <= rr ** 2

    def on_tick(x, y):
        for (ax, ay), (bx, by) in zip(tick_pts, tick_pts[1:]):
            ax, ay, bx, by = ax * size, ay * size, bx * size, by * size
            dx, dy = bx - ax, by - ay
            t = max(0.0, min(1.0, ((x - ax) * dx + (y - ay) * dy) / (dx * dx + dy * dy)))
            if (x - ax - t * dx) ** 2 + (y - ay - t * dy) ** 2 <= (tick_w / 2) ** 2:
                return True
        return False

    bg, cols = _hex(CARD), {"fill": _hex(fill), "edge": _hex(edge), "tick": _hex(tick or fill)}
    for py in range(size):
        row = []
        for px in range(size):
            acc, hits = [0, 0, 0], 0
            for sy in range(ss):
                for sx in range(ss):
                    x, y = px + (sx + 0.5) / ss, py + (sy + 0.5) / ss
                    if not inside(x, y, 0):
                        continue
                    hits += 1
                    key = "tick" if tick and on_tick(x, y) else "fill" if inside(x, y, 1) else "edge"
                    acc = [a + c for a, c in zip(acc, cols[key])]
            a = hits / ss ** 2
            # Blend partial-coverage edge pixels onto the card colour that checkboxes sit on.
            col = [int(acc[i] / hits * a + bg[i] * (1 - a)) if hits else bg[i] for i in range(3)]
            row.append("#%02x%02x%02x" % tuple(col))
        img.put("{" + " ".join(row) + "}", to=(0, py))
    return img


def _style_checkbox(root: tk.Tk, style: ttk.Style) -> None:
    size = max(16, round(13 * float(root.tk.call("tk", "scaling"))))
    images = (
        _checkbox_image(root, size, INPUT, BORDER, None),
        _checkbox_image(root, size, CARD_HI, MUTED, None),
        _checkbox_image(root, size, ACCENT, ACCENT, "#ffffff"),
        _checkbox_image(root, size, _shade_hex(ACCENT, 1.12), _shade_hex(ACCENT, 1.12), "#ffffff"),
    )
    root._checkbox_images = images  # keep references so Tk doesn't drop the images
    off, off_hover, on, on_hover = images
    style.element_create("Box.indicator", "image", off, ("selected active", on_hover), ("selected", on),
                         ("active", off_hover), padding=(0, 0, 8, 0), sticky="")
    style.layout("TCheckbutton", [("Checkbutton.padding", {"sticky": "nswe", "children": [
        ("Box.indicator", {"side": "left", "sticky": ""}),
        ("Checkbutton.focus", {"side": "left", "sticky": "w", "children": [
            ("Checkbutton.label", {"sticky": "nswe"})]})]})])


def _shade_hex(c: str, factor: float) -> str:
    return "#%02x%02x%02x" % tuple(min(255, int(v * factor)) for v in _hex(c))


def setup(root: tk.Tk) -> None:
    global ICON_FONT
    families = set(tkfont.families(root))
    ICON_FONT = next((f for f in ("Segoe Fluent Icons", "Segoe MDL2 Assets") if f in families), None)
    _dark_title_bar(root)

    root.configure(bg=BG)
    style = ttk.Style(root)
    style.theme_use("clam")
    style.configure(".", background=BG, foreground=TEXT, font=F_BODY, borderwidth=0, focuscolor=ACCENT_FG)
    style.configure("TFrame", background=BG)
    style.configure("Card.TFrame", background=CARD)
    style.configure("TLabel", background=BG, foreground=TEXT)
    for widget in ("TCheckbutton", "TRadiobutton"):
        style.configure(widget, background=CARD, foreground=TEXT, focuscolor=ACCENT_FG, indicatorbackground=INPUT,
                        indicatorforeground="#ffffff", indicatormargin=(0, 0, 8, 0), upperbordercolor=BORDER,
                        lowerbordercolor=BORDER)
        style.map(widget, background=[("active", CARD)], foreground=[("active", TEXT)],
                  indicatorbackground=[("selected", ACCENT), ("active", CARD_HI)])
    _style_checkbox(root, style)
    style.configure("Vertical.TScrollbar", background=CARD_HI, troughcolor=BG, arrowcolor=MUTED, bordercolor=BG,
                    lightcolor=CARD_HI, darkcolor=CARD_HI, gripcount=0, arrowsize=12)
    style.map("Vertical.TScrollbar", background=[("active", HOVER), ("pressed", BORDER)])
    style.configure("Horizontal.TProgressbar", background=ACCENT, troughcolor=CARD_HI, bordercolor=CARD,
                    lightcolor=ACCENT, darkcolor=ACCENT, thickness=6)
    style.configure("Treeview", background=CARD, fieldbackground=CARD, foreground=TEXT_SOFT, rowheight=30,
                    bordercolor=BORDER, lightcolor=CARD, darkcolor=CARD)
    style.configure("Treeview.Heading", background=CARD_HI, foreground=MUTED, font=F_CAPS, relief="flat",
                    padding=(8, 6))
    style.map("Treeview", background=[("selected", ACCENT)], foreground=[("selected", "#ffffff")])
    style.map("Treeview.Heading", background=[("active", HOVER)])
    style.configure("TCombobox", fieldbackground=INPUT, background=CARD_HI, foreground=TEXT, arrowcolor=TEXT,
                    bordercolor=BORDER, lightcolor=INPUT, darkcolor=INPUT, selectbackground=INPUT,
                    selectforeground=TEXT, padding=(8, 4))
    style.map("TCombobox", fieldbackground=[("readonly", INPUT)], foreground=[("readonly", TEXT)],
              selectbackground=[("readonly", INPUT)], selectforeground=[("readonly", TEXT)],
              bordercolor=[("focus", ACCENT_FG)])
    root.option_add("*TCombobox*Listbox.background", CARD_HI)
    root.option_add("*TCombobox*Listbox.foreground", TEXT)
    root.option_add("*TCombobox*Listbox.selectBackground", ACCENT)
    root.option_add("*TCombobox*Listbox.selectForeground", "#ffffff")
    # Plain tk inputs used across the pages: visible focus ring and selection colour.
    for cls in ("Entry", "Text"):
        root.option_add(f"*{cls}.highlightThickness", 1)
        root.option_add(f"*{cls}.highlightBackground", BORDER)
        root.option_add(f"*{cls}.highlightColor", ACCENT_FG)
        root.option_add(f"*{cls}.selectBackground", ACCENT)
        root.option_add(f"*{cls}.selectForeground", "#ffffff")
