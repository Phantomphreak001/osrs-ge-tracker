"""Light/Dark themes for the OSRS GE Tracker (v0.5). Pure palette data + Tk/ttk/matplotlib styling helpers."""
THEMES = ("light", "dark")
DEFAULT_THEME = "dark"

PALETTES = {
    "dark": {
        "dark": True, "bg": "#1e1e1e", "panel": "#252526", "field": "#2b2b2b", "fg": "#e0e0e0", "muted": "#9e9e9e",
        "border": "#3c3c3c", "accent": "#d4af37", "accent_fg": "#1e1e1e", "select": "#5a4a14", "select_fg": "#ffffff",
        "heading": "#2d2d30", "button": "#333337", "button_active": "#3f3f46", "tab_sel": "#2b2b2b",
        "pos": "#4caf50", "neg": "#ff6f6c", "info": "#64b5f6", "trough": "#1e1e1e",
        "ticker_bg": "#141414", "ticker_name": "#ffd75e", "ticker_text": "#dddddd", "ticker_pos": "#3ee05a",
        "ticker_dim": "#bbbbbb", "ticker_sep": "#555555", "ticker_hint": "#aaaaaa",
        "chart_bg": "#1e1e1e", "chart_fg": "#e0e0e0", "grid": "#555555",
        "s_high": "#4fa3e0", "s_low": "#ffa040", "s_mid": "#e0e0e0", "s_sma1": "#4caf50", "s_sma2": "#c38fff",
        "spread": "#d4af37", "zero": "#888888", "buy_band": "#4caf50", "sell_band": "#ef5350",
        "vol_hi": "#4fa3e0", "vol_lo": "#2c5d80", "cmap": "magma",
        "card": "#2a2a2e", "card_hover": "#34343a", "tip_bg": "#3a3a40", "tip_fg": "#f0f0f0",
    },
    "light": {
        "dark": False, "bg": "#f0f0f0", "panel": "#f5f5f5", "field": "#ffffff", "fg": "#1a1a1a", "muted": "#666666",
        "border": "#b0b0b0", "accent": "#7a5f00", "accent_fg": "#ffffff", "select": "#0063b1", "select_fg": "#ffffff",
        "heading": "#e1e1e1", "button": "#e6e6e6", "button_active": "#d4d4d4", "tab_sel": "#ffffff",
        "pos": "#006400", "neg": "#b00000", "info": "#00508c", "trough": "#dcdcdc",
        "ticker_bg": "#1b1b1b", "ticker_name": "#ffd75e", "ticker_text": "#dddddd", "ticker_pos": "#3ee05a",
        "ticker_dim": "#bbbbbb", "ticker_sep": "#555555", "ticker_hint": "#aaaaaa",
        "chart_bg": "#ffffff", "chart_fg": "#1a1a1a", "grid": "#b0b0b0",
        "s_high": "#1f77b4", "s_low": "#ff7f0e", "s_mid": "#333333", "s_sma1": "#2ca02c", "s_sma2": "#9467bd",
        "spread": "#ffd75e", "zero": "#999999", "buy_band": "#2ca02c", "sell_band": "#d62728",
        "vol_hi": "#3182bd", "vol_lo": "#9ecae1", "cmap": "viridis",
        "card": "#ffffff", "card_hover": "#eef4fb", "tip_bg": "#ffffe1", "tip_fg": "#1a1a1a",
    },
}


def normalize(name):
    n = (name or "").strip().lower()
    return n if n in PALETTES else DEFAULT_THEME


def palette(name):
    """Palette dict for a theme name; unknown/missing names fall back to the default theme."""
    return PALETTES[normalize(name)]


def get_theme(data):
    return normalize((data or {}).get("theme"))


def set_theme(store, name):
    store.data["theme"] = normalize(name); store.save()
    return store.data["theme"]


def _lum(hx):
    hx = hx.lstrip("#"); ch = [int(hx[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    ch = [c / 12.92 if c <= .03928 else ((c + .055) / 1.055) ** 2.4 for c in ch]
    return .2126 * ch[0] + .7152 * ch[1] + .0722 * ch[2]


def contrast(a, b):
    la, lb = sorted((_lum(a), _lum(b)), reverse=True)
    return (la + .05) / (lb + .05)


# ---------------- Tk / ttk ----------------
def apply_ttk(root, p):
    from tkinter import ttk
    s = ttk.Style(root)
    try: s.theme_use("clam")
    except Exception: pass
    s.configure(".", background=p["bg"], foreground=p["fg"], fieldbackground=p["field"], bordercolor=p["border"],
                darkcolor=p["bg"], lightcolor=p["bg"], troughcolor=p["trough"], selectbackground=p["select"],
                selectforeground=p["select_fg"], insertcolor=p["fg"], focuscolor=p["accent"])
    s.map(".", foreground=[("disabled", p["muted"])])
    s.configure("TFrame", background=p["bg"]); s.configure("TLabel", background=p["bg"], foreground=p["fg"])
    s.configure("TButton", background=p["button"], foreground=p["fg"], bordercolor=p["border"],
                lightcolor=p["button"], darkcolor=p["button"])
    s.map("TButton", background=[("pressed", p["select"]), ("active", p["button_active"])],
          foreground=[("pressed", p["select_fg"])])
    for w in ("TCheckbutton", "TRadiobutton"):
        s.configure(w, background=p["bg"], foreground=p["fg"], indicatorbackground=p["field"],
                    indicatorforeground=p["fg"], upperbordercolor=p["border"], lowerbordercolor=p["border"])
        s.map(w, background=[("active", p["bg"])], indicatorbackground=[("selected", p["accent"]), ("active", p["button_active"])],
              indicatorforeground=[("selected", p["accent_fg"])])
    for w in ("TEntry", "TCombobox", "TSpinbox"):
        s.configure(w, fieldbackground=p["field"], foreground=p["fg"], background=p["button"], arrowcolor=p["fg"],
                    insertcolor=p["fg"], bordercolor=p["border"], lightcolor=p["field"], darkcolor=p["field"])
        s.map(w, fieldbackground=[("readonly", p["field"]), ("disabled", p["bg"])],
              foreground=[("readonly", p["fg"])], selectbackground=[("readonly", p["field"])],
              selectforeground=[("readonly", p["fg"])], bordercolor=[("focus", p["accent"])])
    s.configure("TNotebook", background=p["bg"], bordercolor=p["border"], tabmargins=(2, 4, 2, 0))
    s.configure("TNotebook.Tab", background=p["button"], foreground=p["muted"], bordercolor=p["border"],
                lightcolor=p["button"], padding=(10, 3))
    s.map("TNotebook.Tab", background=[("selected", p["tab_sel"]), ("active", p["button_active"])],
          foreground=[("selected", p["accent"] if p["dark"] else p["fg"]), ("active", p["fg"])],
          lightcolor=[("selected", p["accent"])])
    s.configure("Treeview", background=p["field"], fieldbackground=p["field"], foreground=p["fg"], bordercolor=p["border"],
                lightcolor=p["field"], darkcolor=p["field"], rowheight=22)
    s.map("Treeview", background=[("selected", p["select"])], foreground=[("selected", p["select_fg"])])
    s.configure("Treeview.Heading", background=p["heading"], foreground=p["fg"], bordercolor=p["border"],
                lightcolor=p["heading"], darkcolor=p["heading"], relief="flat")
    s.map("Treeview.Heading", background=[("active", p["button_active"])], foreground=[("active", p["accent"])])
    for o in ("Vertical", "Horizontal"):
        s.configure(f"{o}.TScrollbar", background=p["button"], troughcolor=p["trough"], bordercolor=p["border"],
                    arrowcolor=p["fg"], lightcolor=p["button"], darkcolor=p["button"])
        s.map(f"{o}.TScrollbar", background=[("active", p["button_active"])])
    s.configure("TPanedwindow", background=p["border"]); s.configure("Sash", sashthickness=5, background=p["border"])
    s.configure("TSeparator", background=p["border"])
    s.configure("Muted.TLabel", foreground=p["muted"]); s.configure("Info.TLabel", foreground=p["info"])
    s.configure("Pos.TLabel", foreground=p["pos"]); s.configure("Neg.TLabel", foreground=p["neg"])
    s.configure("Status.TLabel", background=p["panel"], foreground=p["fg"])
    s.configure("Status.TFrame", background=p["panel"])
    s.configure("Offline.TLabel", background=p["panel"], foreground=p["neg"])
    s.configure("Title.TLabel", font=("Segoe UI", 15, "bold"))
    s.configure("H2.TLabel", font=("Segoe UI", 11, "bold"))
    s.configure("Big.TLabel", font=("Segoe UI", 18, "bold"))
    s.configure("Card.TFrame", background=p["card"], bordercolor=p["border"], relief="solid", borderwidth=1)
    s.configure("CardInner.TFrame", background=p["card"], borderwidth=0, relief="flat")
    s.configure("CardInnerH.TFrame", background=p["card_hover"], borderwidth=0, relief="flat")
    s.configure("CardHover.TFrame", background=p["card_hover"], bordercolor=p["accent"], relief="solid", borderwidth=1)
    for nm, fg, font in (("Card", p["fg"], None), ("CardTitle", p["fg"], ("Segoe UI", 10, "bold")),
                         ("CardMuted", p["muted"], ("Segoe UI", 9)), ("CardPos", p["pos"], ("Segoe UI", 11, "bold")),
                         ("CardNeg", p["neg"], ("Segoe UI", 11, "bold")), ("CardBig", p["fg"], ("Segoe UI", 18, "bold")),
                         ("CardBigPos", p["pos"], ("Segoe UI", 18, "bold")), ("CardBigNeg", p["neg"], ("Segoe UI", 18, "bold"))):
        kw = {"background": p["card"], "foreground": fg}
        if font: kw["font"] = font
        s.configure(f"{nm}.TLabel", **kw)
        s.configure(f"{nm}H.TLabel", **dict(kw, background=p["card_hover"]))
    s.configure("Accent.TButton", background=p["accent"], foreground=p["accent_fg"], lightcolor=p["accent"],
                darkcolor=p["accent"], font=("Segoe UI", 9, "bold"))
    s.map("Accent.TButton", background=[("pressed", p["select"]), ("active", p["button_active"])],
          foreground=[("active", p["fg"])])
    s.configure("Link.TLabel", foreground=p["info"])
    s.configure("Card.TCheckbutton", background=p["card"])
    # dropdown listbox of ttk.Combobox (option db; applies to popdowns created afterwards)
    for k, v in (("*TCombobox*Listbox.background", p["field"]), ("*TCombobox*Listbox.foreground", p["fg"]),
                 ("*TCombobox*Listbox.selectBackground", p["select"]), ("*TCombobox*Listbox.selectForeground", p["select_fg"]),
                 ("*Listbox.background", p["field"]), ("*Listbox.foreground", p["fg"]),
                 ("*Toplevel.background", p["bg"]), ("*Menu.background", p["panel"]), ("*Menu.foreground", p["fg"]),
                 ("*Menu.activeBackground", p["select"]), ("*Menu.activeForeground", p["select_fg"]),
                 ("*Entry.background", p["field"]), ("*Entry.foreground", p["fg"]), ("*Entry.insertBackground", p["fg"]),
                 ("*Label.background", p["bg"]), ("*Label.foreground", p["fg"]),
                 ("*Button.background", p["button"]), ("*Button.foreground", p["fg"]),
                 ("*Button.activeBackground", p["button_active"]), ("*Button.activeForeground", p["fg"]),
                 ("*Frame.background", p["bg"])):
        root.option_add(k, v)
    root.configure(bg=p["bg"])
    return s


def recolor_tk(widget, p):
    """Walk a widget tree recoloring classic tk widgets (ttk ones follow the style)."""
    import tkinter as tk
    stack = [widget]
    while stack:
        w = stack.pop(); stack.extend(w.winfo_children())
        try:
            if isinstance(w, tk.Listbox):
                w.configure(bg=p["field"], fg=p["fg"], selectbackground=p["select"], selectforeground=p["select_fg"],
                            highlightbackground=p["border"], highlightcolor=p["accent"])
            elif isinstance(w, tk.Canvas) and getattr(w, "_themed", True):
                if getattr(w, "_ticker", False): w.configure(bg=p["ticker_bg"])
                else: w.configure(bg=p["chart_bg"], highlightbackground=p["bg"])
            elif isinstance(w, (tk.Tk, tk.Toplevel)):
                w.configure(bg=p["bg"])
            elif isinstance(w, tk.Menu):
                w.configure(bg=p["panel"], fg=p["fg"], activebackground=p["select"], activeforeground=p["select_fg"])
        except Exception:
            pass


def tag_trees(widget, p):
    from tkinter import ttk
    stack = [widget]
    while stack:
        w = stack.pop(); stack.extend(w.winfo_children())
        if isinstance(w, ttk.Treeview):
            w.tag_configure("pos", foreground=p["pos"]); w.tag_configure("neg", foreground=p["neg"])


# ---------------- matplotlib ----------------
def style_fig(fig, p):
    fig.set_facecolor(p["chart_bg"])
    for ax in fig.axes: style_ax(ax, p)


def style_ax(ax, p):
    ax.set_facecolor(p["chart_bg"])
    for sp in ax.spines.values(): sp.set_color(p["grid"])
    ax.tick_params(colors=p["chart_fg"], which="both")
    ax.xaxis.label.set_color(p["chart_fg"]); ax.yaxis.label.set_color(p["chart_fg"]); ax.title.set_color(p["chart_fg"])
    for t in ax.texts: t.set_color(p["chart_fg"])
    lg = ax.get_legend()
    if lg: style_legend(lg, p)


def style_legend(lg, p):
    lg.get_frame().set_facecolor(p["panel"]); lg.get_frame().set_edgecolor(p["border"])
    for t in lg.get_texts(): t.set_color(p["chart_fg"])


def grid(ax, p):
    ax.grid(True, color=p["grid"], alpha=.45 if p["dark"] else .6, lw=.6)
