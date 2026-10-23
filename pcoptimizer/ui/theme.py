"""Colours, fonts and ttk styling."""

from __future__ import annotations

import sys
import tkinter as tk
from tkinter import ttk

BG = "#0f1117"
SIDEBAR = "#151821"
CARD = "#1b1f2b"
CARD_HI = "#232838"
BORDER = "#2a3042"
TEXT = "#e8eaf0"
MUTED = "#8b93a7"
ACCENT = "#4f8cff"
GAMING = "#ff4d6d"
BALANCED = "#2ec4b6"
GOOD = "#3ddc84"
WARN = "#ffb020"
BAD = "#ff5c5c"

FAMILY = "Segoe UI" if sys.platform == "win32" else "DejaVu Sans"
MONO = "Cascadia Mono" if sys.platform == "win32" else "DejaVu Sans Mono"

F_TITLE = (FAMILY, 20, "bold")
F_H2 = (FAMILY, 13, "bold")
F_BODY = (FAMILY, 10)
F_SMALL = (FAMILY, 9)
F_BIG = (FAMILY, 30, "bold")
F_MONO = (MONO, 9)


def score_color(pct: float) -> str:
    return GOOD if pct >= 70 else WARN if pct >= 45 else BAD


def setup(root: tk.Tk) -> None:
    root.configure(bg=BG)
    style = ttk.Style(root)
    style.theme_use("clam")
    style.configure(".", background=BG, foreground=TEXT, font=F_BODY, borderwidth=0)
    style.configure("TFrame", background=BG)
    style.configure("Card.TFrame", background=CARD)
    style.configure("TLabel", background=BG, foreground=TEXT)
    style.configure("TCheckbutton", background=CARD, foreground=TEXT, focuscolor=CARD, indicatorbackground=CARD_HI,
                    indicatorforeground=TEXT)
    style.map("TCheckbutton", background=[("active", CARD)], indicatorbackground=[("selected", ACCENT)])
    style.configure("Vertical.TScrollbar", background=CARD_HI, troughcolor=BG, arrowcolor=MUTED, bordercolor=BG,
                    lightcolor=CARD_HI, darkcolor=CARD_HI)
    style.configure("Horizontal.TProgressbar", background=ACCENT, troughcolor=CARD_HI, bordercolor=CARD,
                    lightcolor=ACCENT, darkcolor=ACCENT, thickness=8)
    style.configure("Treeview", background=CARD, fieldbackground=CARD, foreground=TEXT, rowheight=26,
                    bordercolor=BORDER)
    style.configure("Treeview.Heading", background=CARD_HI, foreground=MUTED, font=F_SMALL, relief="flat")
    style.map("Treeview", background=[("selected", ACCENT)], foreground=[("selected", "#ffffff")])
    style.map("Treeview.Heading", background=[("active", BORDER)])
