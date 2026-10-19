"""OSRS Grand Exchange Tracker (v0.8) - Tkinter desktop app. Run: python ge_tracker.py
Pages: Home · Watchlist · Flip Finder · Item · Market · My Flips (+ optional scrolling best-flips ticker)."""
import sys, threading, time, datetime, tkinter as tk
from tkinter import ttk, messagebox
import ge_core as core
import suggestions as sg
import theme
from ui_common import SearchBox, IconCache, tip, GLOSSARY
from analytics_tab import WatchlistPage, ItemPage, MarketPage, FlipsPage
from suggestions_tab import FlipFinderPage
from home_tab import HomePage

VERSION = "0.8"
TABS = (("home", "🏠 Home"), ("watchlist", "★ Watchlist"), ("finder", "💰 Flip Finder"), ("item", "🔎 Item"),
        ("market", "📈 Market"), ("flips", "📒 My Flips"))
REFRESH_S = 60
TIPS = """Welcome to GE Tracker!  A few quick tips:

•  Look up any item with the search box at the top (or press Ctrl+F).
   Its page shows the price to buy at, the price to sell at, and the profit after tax.

•  Home shows the 5 best flips right now. Click a card to see the item.

•  Double-click an item in any list to open it. Right-click for more (watchlist, alerts…).

•  Flip Finder plans flips for your cash stack: type how much gp you have and press 'Find flips for me'.

•  Hover over a column title to see what it means (ROI, buy limit, …).

•  F5 refreshes prices (they also update every minute). Settings (⚙) has the finer controls.

Prices come from the OSRS Wiki and are estimates - margins can close and offers may not fill."""


def beep(root):
    try:
        import winsound
        winsound.MessageBeep(winsound.MB_ICONEXCLAMATION)
    except Exception:
        root.bell()


class App:
    def __init__(self, root, show_tips=True):
        self.root, self.api, self.store = root, core.Api(), core.Store()
        self.flog = core.FlipLog()
        ui = self.store.data["ui"]
        root.title(f"OSRS GE Tracker {VERSION}")
        root.geometry(core.fit_geometry(ui.get("geometry"), root.winfo_screenwidth(), root.winfo_screenheight()))
        root.minsize(1000, 640)
        self.items, self.by_name, self.names, self.latest, self.vol = {}, {}, [], {}, {}
        self.last_ok, self.offline_msg, self._busy = 0, None, {}
        self.advanced = bool(ui.get("advanced"))
        self.theme = theme.get_theme(self.store.data); self.pal = theme.palette(self.theme)
        theme.apply_ttk(root, self.pal)
        self.icons = IconCache(root, self.api.dir)
        self._build()
        self._retheme_widgets()
        self._apply_settings()
        self.goto(ui.get("tab") or "home")
        self.status("Loading item list…")
        self._load_mapping()
        root.protocol("WM_DELETE_WINDOW", self.quit)
        root.after(1000, self._clock)
        root.after(REFRESH_S * 1000, self.tick)
        if show_tips and not ui.get("tips_seen"): root.after(800, self.show_tips)

    # ================= UI =================
    def _build(self):
        R = self.root
        # menus
        mb = tk.Menu(R); fm = tk.Menu(mb, tearoff=0); vm = tk.Menu(mb, tearoff=0); hm = tk.Menu(mb, tearoff=0)
        fm.add_command(label="Refresh prices", accelerator="F5", command=lambda: self.refresh(force=True))
        fm.add_command(label="Settings…", command=self.settings_dialog)
        fm.add_separator(); fm.add_command(label="Quit", command=self.quit)
        for i, (k, t) in enumerate(TABS):
            vm.add_command(label=t, accelerator=f"Ctrl+{i + 1}", command=lambda k=k: self.goto(k))
        vm.add_separator()
        self.theme_var = tk.StringVar(value=self.theme)
        for t in ("light", "dark"):
            vm.add_radiobutton(label=f"{t.title()} theme", value=t, variable=self.theme_var,
                               command=lambda: self.set_theme(self.theme_var.get()))
        self.adv_var = tk.BooleanVar(value=self.advanced)
        vm.add_checkbutton(label="Show advanced columns & controls", variable=self.adv_var, command=self._toggle_adv)
        self.show_tk = tk.BooleanVar(value=self.store.data.get("show_ticker", True))
        vm.add_checkbutton(label="Show scrolling flips ticker", variable=self.show_tk, command=self._toggle_ticker)
        hm.add_command(label="Quick tips", command=self.show_tips)
        hm.add_command(label="What do the words mean?", command=self.glossary_dialog)
        hm.add_command(label="About", command=lambda: messagebox.showinfo(
            "About", f"OSRS GE Tracker {VERSION}\nLive Grand Exchange prices from the OSRS Wiki real-time prices API.\n"
                     "Read-only public data - no game client interaction.", parent=R))
        mb.add_cascade(label="File", menu=fm); mb.add_cascade(label="View", menu=vm); mb.add_cascade(label="Help", menu=hm)
        R.config(menu=mb); self._menus = (mb, fm, vm, hm)
        # ticker
        self.tk_canvas = tk.Canvas(R, height=28, bg=self.pal["ticker_bg"], highlightthickness=0, cursor="hand2")
        self.tk_canvas._ticker = True
        self.tk_canvas.bind("<Enter>", lambda e: setattr(self, "_tk_paused", True))
        self.tk_canvas.bind("<Leave>", lambda e: setattr(self, "_tk_paused", False))
        self._tk_paused, self._tk_entries = False, []
        self.tk_canvas.create_text(10, 14, anchor="w", fill=self.pal["ticker_hint"], font=("Segoe UI", 10),
                                   text="Best flips: waiting for prices…")
        # top bar
        top = ttk.Frame(R, padding=(8, 6)); top.pack(fill="x"); self._top = top
        if self.store.data.get("show_ticker", True): self.tk_canvas.pack(fill="x", before=top)
        ttk.Label(top, text="GE Tracker", style="H2.TLabel").pack(side="left", padx=(0, 12))
        self.search = SearchBox(top, self, self.open_item, width=36, placeholder="🔍 Search any item…  (Ctrl+F)")
        self.search.pack(side="left")
        b = ttk.Button(top, text="⟳ Refresh", command=lambda: self.refresh(force=True)); b.pack(side="left", padx=6)
        tip(b, "Get the latest prices now (F5). Prices also update by themselves every minute.", self)
        b = ttk.Button(top, text="⚙ Settings", command=self.settings_dialog); b.pack(side="right")
        self.theme_btn = ttk.Button(top, width=8, command=lambda: self.set_theme("light" if self.theme == "dark" else "dark"))
        self.theme_btn.pack(side="right", padx=4); self._theme_btn_text()
        c = ttk.Checkbutton(top, text="Advanced", variable=self.adv_var, command=self._toggle_adv); c.pack(side="right", padx=8)
        tip(c, "Show extra columns (price swings, trends, best-time scanner) and fine-tuning controls.", self)
        # status bar (packed before the notebook so it is never clipped)
        sb = ttk.Frame(R, style="Status.TFrame", padding=(6, 2)); sb.pack(fill="x", side="bottom")
        self.statusv = tk.StringVar()
        self.status_lbl = ttk.Label(sb, textvariable=self.statusv, anchor="w", style="Status.TLabel")
        self.status_lbl.pack(side="left", fill="x", expand=True)
        self.prog = ttk.Progressbar(sb, mode="indeterminate", length=120)
        self.busy_lbl = ttk.Label(sb, text="", style="Status.TLabel")
        self.age_lbl = ttk.Label(sb, text="", style="Status.TLabel"); self.age_lbl.pack(side="right")
        # pages
        self.nb = ttk.Notebook(R); self.nb.pack(fill="both", expand=True)
        self.home = HomePage(self.nb, self)
        self.watch_page = WatchlistPage(self.nb, self)
        self.finder = FlipFinderPage(self.nb, self)
        self.item_page = ItemPage(self.nb, self)
        self.market = MarketPage(self.nb, self)
        self.flips_page = FlipsPage(self.nb, self)
        self.pages = {"home": self.home, "watchlist": self.watch_page, "finder": self.finder, "item": self.item_page,
                      "market": self.market, "flips": self.flips_page}
        for k, t in TABS: self.nb.add(self.pages[k], text=f" {t} ")
        self.nb.bind("<<NotebookTabChanged>>", lambda e: self._tab_changed())
        # keys
        R.bind_all("<Control-f>", lambda e: self.focus_search()); R.bind_all("<Control-F>", lambda e: self.focus_search())
        R.bind_all("<F5>", lambda e: self.refresh(force=True))
        for i, (k, _) in enumerate(TABS): R.bind_all(f"<Control-Key-{i + 1}>", lambda e, k=k: self.goto(k))
        self.root.after(33, self._tk_step)

    # ---------- navigation ----------
    def current(self):
        try: return TABS[self.nb.index(self.nb.select())][0]
        except Exception: return "home"

    def goto(self, key):
        if key not in self.pages: key = "home"
        self.nb.select(self.pages[key])

    def _tab_changed(self):
        k = self.current(); self.store.data["ui"]["tab"] = k
        if not self.items: return
        if k == "market": self.market.refresh()
        elif k == "home": self.home.refresh()
        elif k == "watchlist": self.watch_page.refresh()
        elif k == "finder": self.finder.refresh_now()

    def focus_search(self):
        if self.current() == "home": self.home.search.focus()
        else: self.search.focus()
        return "break"

    def open_item(self, iid, view=None):
        if iid is None or iid not in self.items: return
        self.goto("item"); self.item_page.open(iid, view)

    def item_menu(self, iid, event):
        m = tk.Menu(self.root, tearoff=0, bg=self.pal["panel"], fg=self.pal["fg"],
                    activebackground=self.pal["select"], activeforeground=self.pal["select_fg"])
        m.add_command(label="Open item page", command=lambda: self.open_item(iid))
        if iid in self.store.data["watch"]: m.add_command(label="Remove from watchlist", command=lambda: self.remove_watch(iid))
        else: m.add_command(label="Add to watchlist", command=lambda: self.add_watch(iid))
        m.add_command(label="Set price alert…", command=lambda: self.alert_dialog(iid))
        m.add_command(label="Log a flip…", command=lambda: self.log_flip(iid))
        m.add_command(label="Log a buy (sell later)…", command=lambda: self.log_buy(iid))
        m.add_separator()
        m.add_command(label="Copy name", command=lambda: (self.root.clipboard_clear(),
                                                         self.root.clipboard_append(self.items[iid]["name"])))
        try: m.tk_popup(event.x_root, event.y_root)
        finally: m.grab_release()

    # ---------- watchlist / flips actions ----------
    def add_watch(self, iid):
        self.store.add(iid); self.on_watch_changed()
        self.status(f"Added {self.items.get(iid, {}).get('name', iid)} to your watchlist")

    def remove_watch(self, iid):
        nm = self.items.get(iid, {}).get("name", iid)
        self.store.remove(iid); self.on_watch_changed(); self.status(f"Removed {nm} from your watchlist")

    def on_watch_changed(self):
        self.watch_page.refresh(); self.home.refresh(); self.item_page.update_prices()

    def on_flips_changed(self):
        self.home.refresh_profit()

    def log_flip(self, iid, qty=None, buy=None, sell=None):
        self.flips_page.prefill(iid, qty, buy, sell); self.goto("flips")

    def log_buy(self, iid, qty=None, buy=None):
        self.flips_page.prefill_buy(iid, qty, buy); self.goto("flips")

    def alert_dialog(self, iid):
        if not iid: messagebox.showinfo("Price alert", "Select an item first.", parent=self.root); return
        it = self.items[iid]; p = self.latest.get(str(iid), {}); a = self.store.data["alerts"].get(str(iid), {})
        d = self._dialog(f"Price alert - {it['name']}")
        f = ttk.Frame(d, padding=14); f.pack(fill="both", expand=True)
        ttk.Label(f, text=it["name"], style="H2.TLabel").grid(row=0, column=0, columnspan=3, sticky="w")
        ttk.Label(f, text=f"Now: buy at {core.fmt_gp(p.get('low'))} · sell at {core.fmt_gp(p.get('high'))}",
                  style="Muted.TLabel").grid(row=1, column=0, columnspan=3, sticky="w", pady=(0, 8))
        up, dn = tk.StringVar(value=str(a.get("above") or "")), tk.StringVar(value=str(a.get("below") or ""))
        ttk.Label(f, text="Tell me when the sell price goes ABOVE:").grid(row=2, column=0, sticky="w")
        e1 = ttk.Entry(f, textvariable=up, width=14); e1.grid(row=2, column=1, padx=6, pady=3)
        ttk.Label(f, text="Tell me when the buy price drops BELOW:").grid(row=3, column=0, sticky="w")
        ttk.Entry(f, textvariable=dn, width=14).grid(row=3, column=1, padx=6, pady=3)
        ttk.Label(f, text="Leave blank for no alert. You can type 1.2m or 850k. Each alert fires once.",
                  style="Muted.TLabel").grid(row=4, column=0, columnspan=3, sticky="w", pady=(4, 8))
        def parse(s):
            s = s.strip()
            return sg.parse_gp(s) if s else None
        def save():
            try: above, below = parse(up.get()), parse(dn.get())
            except ValueError:
                messagebox.showerror("Price alert", "Please type a price like 1500, 1.5k or 2m.", parent=d); return
            if above is None and below is None: self.store.data["alerts"].pop(str(iid), None); self.store.save()
            else: self.store.set_alert(iid, above=above, below=below)
            d.destroy(); self.on_watch_changed(); self._check_alerts()
        bf = ttk.Frame(f); bf.grid(row=5, column=0, columnspan=3, sticky="e")
        ttk.Button(bf, text="Remove alert", command=lambda: (up.set(""), dn.set(""), save())).pack(side="left", padx=4)
        ttk.Button(bf, text="Cancel", command=d.destroy).pack(side="left", padx=4)
        ttk.Button(bf, text="Save", style="Accent.TButton", command=save).pack(side="left")
        d.bind("<Return>", lambda e: save()); e1.focus_set()

    # ---------- dialogs ----------
    def _dialog(self, title):
        d = tk.Toplevel(self.root); d.title(title); d.transient(self.root); d.configure(bg=self.pal["bg"])
        d.bind("<Escape>", lambda e: d.destroy())
        d.after(10, lambda: (d.grab_set() if d.winfo_exists() else None))
        d.after(30, lambda: d.winfo_exists() and d.geometry(
            f"+{self.root.winfo_rootx() + max(0, (self.root.winfo_width() - d.winfo_reqwidth()) // 2)}"
            f"+{self.root.winfo_rooty() + max(0, (self.root.winfo_height() - d.winfo_reqheight()) // 3)}"))
        return d

    def show_tips(self):
        d = self._dialog("Quick tips")
        f = ttk.Frame(d, padding=16); f.pack(fill="both", expand=True)
        ttk.Label(f, text=TIPS, justify="left", wraplength=560, font=("Segoe UI", 10)).pack(anchor="w")
        again = tk.BooleanVar(value=True)
        ttk.Checkbutton(f, text="Don't show this again (Help → Quick tips brings it back)", variable=again).pack(anchor="w", pady=(10, 0))
        def close():
            self.store.data["ui"]["tips_seen"] = bool(again.get()); self.store.save(); d.destroy()
        ttk.Button(f, text="Got it", style="Accent.TButton", command=close).pack(anchor="e", pady=(8, 0))
        d.protocol("WM_DELETE_WINDOW", close); self.tips_win = d
        return d

    def glossary_dialog(self):
        d = self._dialog("What do the words mean?")
        f = ttk.Frame(d, padding=14); f.pack(fill="both", expand=True)
        keys = ("buy_at", "sell_at", "profit_each", "roi", "limit", "profit_limit", "vol", "tax", "volatility",
                "confidence", "best_time", "trend", "fill", "cap", "risk", "strategy")
        ttk.Label(f, text="\n\n".join(GLOSSARY[k] for k in keys), justify="left", wraplength=620).pack(anchor="w")
        ttk.Button(f, text="Close", command=d.destroy).pack(anchor="e", pady=(8, 0))

    def settings_dialog(self):
        D = self.store.data; T, F, M, TM = D["ticker"], D["finder"], D["market"], D["timing"]
        d = self._dialog("Settings")
        nb = ttk.Notebook(d); nb.pack(fill="both", expand=True, padx=8, pady=8)
        V = {}
        def page(title):
            f = ttk.Frame(nb, padding=12); nb.add(f, text=title); f._r = 0; return f
        def row(f, label, key, var, widget, g=None):
            l = ttk.Label(f, text=label); l.grid(row=f._r, column=0, sticky="w", pady=3, padx=(0, 10))
            widget.grid(row=f._r, column=1, sticky="w"); f._r += 1; V[key] = var
            if g: tip(l, g, self); tip(widget, g, self)
        def spin(f, var, lo, hi, inc=1, w=8): return ttk.Spinbox(f, from_=lo, to=hi, increment=inc, textvariable=var, width=w)
        g = page("General")
        v = tk.StringVar(value=self.theme); row(g, "Theme", "theme", v, ttk.Combobox(g, textvariable=v, values=("dark", "light"), state="readonly", width=8))
        v = tk.BooleanVar(value=D.get("auto_refresh", True)); row(g, "Update prices every minute", "auto", v, ttk.Checkbutton(g, variable=v))
        v = tk.BooleanVar(value=D.get("show_ticker", True)); row(g, "Show scrolling flips ticker", "ticker", v, ttk.Checkbutton(g, variable=v))
        v = tk.BooleanVar(value=self.advanced); row(g, "Show advanced columns & controls", "adv", v, ttk.Checkbutton(g, variable=v))
        v = tk.BooleanVar(value=self.icons.enabled and D["ui"].get("icons", True))
        row(g, "Show item icons (from the OSRS Wiki)", "icons", v, ttk.Checkbutton(g, variable=v))
        b = page("Best flips filter")
        ttk.Label(b, text="Used by Home, Flip Finder 'right now' and the ticker.", style="Muted.TLabel").grid(row=0, column=0, columnspan=2, sticky="w"); b._r = 1
        v = tk.StringVar(value=T.get("rank_by", "profit")); row(b, "Rank by", "rank_by", v, ttk.Combobox(b, textvariable=v, values=("profit", "roi"), state="readonly", width=8),
                                                                "profit = profit per buy limit; roi = % return.")
        v = tk.IntVar(value=T["max_age_min"]); row(b, "Max minutes since last trade", "max_age_min", v, spin(b, v, 5, 240, 5), "age")
        v = tk.IntVar(value=T["min_vol_1h"]); row(b, "Min traded per hour", "min_vol_1h", v, spin(b, v, 0, 100000, 50), "min_vol")
        v = tk.DoubleVar(value=T["max_roi"]); row(b, "Max ROI %", "max_roi", v, spin(b, v, 1, 500, 5), "max_roi")
        v = tk.IntVar(value=T["top_n"]); row(b, "How many to list", "top_n", v, spin(b, v, 5, 50))
        p = page("Flip planner")
        v = tk.DoubleVar(value=F["fill_pct"]); row(p, "Fill % of hourly trades", "fill_pct", v, spin(p, v, 1, 50), "fill")
        v = tk.DoubleVar(value=F["cap_pct"]); row(p, "Max % of cash per item", "cap_pct", v, spin(p, v, 5, 100, 5), "cap")
        v = tk.IntVar(value=F["slots"]); row(p, "GE slots", "slots", v, spin(p, v, 1, 8), "slots")
        v = tk.IntVar(value=F["max_items"]); row(p, "Max items", "max_items", v, spin(p, v, 1, 20))
        v = tk.BooleanVar(value=F["exclude_bonds"]); row(p, "Skip bonds", "exclude_bonds", v, ttk.Checkbutton(p, variable=v))
        o = page("Market & timing")
        v = tk.IntVar(value=M["min_vol"]); row(o, "Rises/drops: min traded per hour", "m_min_vol", v, spin(o, v, 0, 1_000_000, 100), "min_vol")
        v = tk.IntVar(value=TM["window"]); row(o, "Best-time window (hours)", "window", v, spin(o, v, 1, 6))
        v = tk.IntVar(value=TM["night_start"]); row(o, "'Last night' starts (ET hour)", "night_start", v, spin(o, v, 0, 23))
        v = tk.IntVar(value=TM["night_end"]); row(o, "'Last night' ends (ET hour)", "night_end", v, spin(o, v, 0, 23))
        def save():
            try:
                T.update(rank_by=V["rank_by"].get(), max_age_min=int(V["max_age_min"].get()), min_vol_1h=int(V["min_vol_1h"].get()),
                         max_roi=float(V["max_roi"].get()), top_n=int(V["top_n"].get()))
                F.update(fill_pct=float(V["fill_pct"].get()), cap_pct=float(V["cap_pct"].get()), slots=int(V["slots"].get()),
                         max_items=int(V["max_items"].get()), exclude_bonds=bool(V["exclude_bonds"].get()))
                M["min_vol"] = int(V["m_min_vol"].get())
                TM.update(window=int(V["window"].get()), night_start=int(V["night_start"].get()) % 24,
                          night_end=int(V["night_end"].get()) % 24)
            except (tk.TclError, ValueError):
                messagebox.showerror("Settings", "Please enter whole numbers in the boxes.", parent=d); return
            D["auto_refresh"] = bool(V["auto"].get()); D["ui"]["icons"] = bool(V["icons"].get())
            self.show_tk.set(bool(V["ticker"].get())); self._toggle_ticker()
            self.adv_var.set(bool(V["adv"].get())); self._toggle_adv()
            self.store.save(); self._apply_settings()
            if V["theme"].get() != self.theme: self.set_theme(V["theme"].get())
            d.destroy(); self.on_prices()
        bf = ttk.Frame(d, padding=(8, 0, 8, 8)); bf.pack(fill="x")
        ttk.Button(bf, text="Restore defaults", command=lambda: self._restore_defaults(d)).pack(side="left")
        ttk.Button(bf, text="Save", style="Accent.TButton", command=save).pack(side="right")
        ttk.Button(bf, text="Cancel", command=d.destroy).pack(side="right", padx=4)
        self.settings_win = d
        return d

    def _restore_defaults(self, d):
        if not messagebox.askyesno("Settings", "Reset all filters and planner settings to defaults?\n"
                                               "(Your watchlist, alerts and flips are kept.)", parent=d): return
        D = self.store.data
        D["ticker"] = dict(core.TICKER_DEFAULTS); D["finder"] = dict(core.FINDER_DEFAULTS)
        D["market"] = dict(core.MARKET_UI_DEFAULTS); D["timing"] = dict(core.TIMING_UI_DEFAULTS)
        self.store.save(); self._apply_settings(); d.destroy(); self.on_prices()

    def _apply_settings(self):
        D = self.store.data
        self.icons.enabled = D["ui"].get("icons", True)
        self.finder.apply_settings(D["finder"]); self.market.minvol.set(D["market"]["min_vol"])
        self.item_page.apply_settings(D["timing"])
        for p in (self.watch_page, ): p.show_advanced(self.advanced)
        self.item_page.set_advanced(self.advanced); self.market.set_advanced(self.advanced); self.finder.set_advanced(self.advanced)

    def timing_settings(self):
        return {"window": self.store.data["timing"]["window"]}

    def _toggle_adv(self):
        self.advanced = bool(self.adv_var.get()); self.store.data["ui"]["advanced"] = self.advanced; self.store.save()
        self.watch_page.show_advanced(self.advanced); self.item_page.set_advanced(self.advanced)
        self.market.set_advanced(self.advanced); self.finder.set_advanced(self.advanced)

    # ---------- status / background work ----------
    def status(self, s, kind="info"):
        self.statusv.set(s)
        self.status_lbl.configure(style="Offline.TLabel" if kind in ("error", "warn") else "Status.TLabel")

    def bg(self, work, done, what="Loading", on_error=None):
        """Run work() in a thread; done(result) on the Tk thread. Shows a busy indicator; friendly error text."""
        tok = object(); self._busy[tok] = what; self._busy_changed()
        def run():
            try:
                res = work()
                self.root.after(0, lambda: (self._busy.pop(tok, None), self._busy_changed(), done(res)))
            except Exception as e:
                def fail(e=e):
                    self._busy.pop(tok, None); self._busy_changed()
                    self.status(f"{what} failed: {core.friendly_error(e)}", kind="error")
                    if on_error: on_error(e)
                try: self.root.after(0, fail)
                except Exception: pass
        threading.Thread(target=run, daemon=True).start()

    def _busy_changed(self):
        if self._busy:
            self.busy_lbl.config(text=list(self._busy.values())[-1] + "…")
            if not self.prog.winfo_ismapped():
                self.prog.pack(side="right", padx=6, before=self.age_lbl); self.busy_lbl.pack(side="right", before=self.prog)
                self.prog.start(12)
        else:
            self.prog.stop(); self.prog.pack_forget(); self.busy_lbl.pack_forget()

    def _clock(self):
        try:
            if self.last_ok:
                age = time.time() - self.last_ok
                self.age_lbl.config(text=f"Prices updated {core.ago(age)}" + (" (offline)" if self.offline_msg else ""),
                                    style="Offline.TLabel" if self.offline_msg or age > 300 else "Status.TLabel")
            if self.current() == "watchlist" and int(time.time()) % 15 == 0 and self.items: self.watch_page.refresh()
        finally:
            self.root.after(1000, self._clock)

    # ---------- theme ----------
    def _theme_btn_text(self):
        self.theme_btn.config(text="☀ Light" if self.theme == "dark" else "☾ Dark")

    def set_theme(self, name):
        """Switch Light/Dark live, persist to watchlist.json, restyle everything incl. charts."""
        self.theme = theme.set_theme(self.store, name); self.pal = theme.palette(self.theme)
        self.theme_var.set(self.theme); self._theme_btn_text()
        theme.apply_ttk(self.root, self.pal)
        self._retheme_widgets()
        self._tk_entries = []; self._update_ticker() if self.items else None
        self.item_page.retheme(); self.finder.retheme(); self.flips_page.retheme()
        for s in (self.search, self.home.search, self.watch_page.search): s.retheme()

    def _retheme_widgets(self):
        theme.recolor_tk(self.root, self.pal); theme.tag_trees(self.root, self.pal)
        for m in getattr(self, "_menus", ()):
            m.configure(bg=self.pal["panel"], fg=self.pal["fg"], activebackground=self.pal["select"],
                        activeforeground=self.pal["select_fg"], selectcolor=self.pal["accent"])

    # ---------- best-flips ticker ----------
    def _toggle_ticker(self):
        on = self.show_tk.get(); self.store.data["show_ticker"] = on; self.store.save()
        if on: self.tk_canvas.pack(fill="x", before=self._top)
        else: self.tk_canvas.pack_forget()

    def _update_ticker(self):
        flips = core.best_flips(self.items, self.latest, self.vol, settings=core.ticker_settings(self.store.data))
        cv, P = self.tk_canvas, self.pal
        cv.configure(bg=P["ticker_bg"])
        x = cv.winfo_width() if not self._tk_entries else (cv.bbox(self._tk_entries[0]) or (10,))[0]
        cv.delete("all"); self._tk_entries = []
        if not flips:
            cv.create_text(10, 14, anchor="w", fill=P["ticker_hint"], font=("Segoe UI", 10),
                           text="Best flips: none pass the filters right now"); return
        f, fb = ("Segoe UI", 10), ("Segoe UI", 10, "bold")
        for i, r in enumerate(flips):
            tag = f"e{i}"; xx = x
            parts = ((f"{i+1}. {r['name']} ", P["ticker_name"], fb),
                     (f"buy {core.fmt_gp(r['low'])} → sell {core.fmt_gp(r['high'])} ", P["ticker_text"], f),
                     (f"{core.signed_gp(r['margin'])} each ", P["ticker_pos"], fb),
                     (f"({r['roi']:.1f}%) ", P["ticker_pos"], f),
                     (f"≈{core.short_gp(r['profit_limit'])}/limit" if r["profit_limit"] else "", P["ticker_dim"], f),
                     ("   ◆   ", P["ticker_sep"], f))
            for txt, col, fo in parts:
                if not txt: continue
                t = cv.create_text(xx, 14, anchor="w", text=txt, fill=col, font=fo, tags=(tag, "ent"))
                xx = cv.bbox(t)[2]
            cv.tag_bind(tag, "<Button-1>", lambda e, iid=r["id"]: self.open_item(iid))
            self._tk_entries.append(tag); x = xx

    def _tk_step(self):
        try:
            if self._tk_entries and not self._tk_paused and self.tk_canvas.winfo_ismapped():
                cv = self.tk_canvas; cv.move("ent", -2, 0)
                first = self._tk_entries[0]; b = cv.bbox(first)
                if b and b[2] < 0:   # scrolled fully off the left: recycle to the end
                    end = max(cv.bbox(self._tk_entries[-1])[2], cv.winfo_width())
                    cv.move(first, end - b[0], 0)
                    self._tk_entries.append(self._tk_entries.pop(0))
        finally:
            self.root.after(33, self._tk_step)

    # ================= data =================
    def _load_mapping(self):
        def done(m):
            self.items = {i["id"]: i for i in m}
            self.by_name = {i["name"].lower(): i["id"] for i in m}
            self.names = sorted(i["name"] for i in m)
            self.status(f"Loaded {len(self.items):,} items. Getting prices…")
            self.refresh(force=True)
        def fail(e):
            self.offline_msg = core.friendly_error(e)
            self.status("Couldn't load the item list. " + self.offline_msg + " Retrying in 30s.", kind="error")
            self.root.after(30_000, self._load_mapping)
        self.bg(self.api.mapping, done, "Loading item list", on_error=fail)

    def refresh(self, force=False):
        if not self.items: return
        def work():
            lat, t = self.api.latest(force=force)
            try: vol = self.api.volumes_1h()
            except Exception: vol = self.vol
            return lat, vol, t
        def fail(e):
            self.offline_msg = core.friendly_error(e)
            extra = " Showing the last prices we got." if self.latest else ""
            self.status(self.offline_msg + extra, kind="error")
        self.bg(work, lambda r: self._apply(*r), "Updating prices", on_error=fail)

    def _apply(self, lat, vol, t):
        self.latest, self.vol, self.last_ok, self.offline_msg = lat, vol, t, None
        self.on_prices()
        self._check_alerts()
        self.status(f"{len(self.items):,} items · watching {len(self.store.data['watch'])} · "
                    f"prices from the OSRS Wiki real-time prices API")
        self._clock_once()

    def _clock_once(self):
        if self.last_ok: self.age_lbl.config(text=f"Prices updated {core.ago(time.time() - self.last_ok)}", style="Status.TLabel")

    def on_prices(self):
        if not self.items: return
        self._update_ticker()
        self.watch_page.refresh(); self.home.refresh(); self.finder.refresh_now(); self.item_page.update_prices()
        self.flips_page.render_open()
        if self.current() == "market": self.market.refresh()

    def _check_alerts(self):
        hits = core.check_alerts(self.store.data["alerts"], self.latest)
        if not hits: return
        for i, k, p, tg in hits: self.store.log_alert(i, k, p, tg)
        self.store.save(); beep(self.root); self.home.refresh_alerts()
        msg = "\n".join(f"{self.items[i]['name']}: {'sell price is now above' if k == 'above' else 'buy price is now below'} "
                        f"{core.fmt_gp(tg)} (now {core.fmt_gp(p)})" for i, k, p, tg in hits)
        self._alert_popup(msg)

    def _alert_popup(self, msg):
        """Non-blocking alert window (prices keep updating behind it)."""
        w = getattr(self, "_alert_win", None)
        if w is not None and w.winfo_exists():
            w._lbl.config(text=w._lbl.cget("text") + "\n" + msg); w.lift(); return
        w = tk.Toplevel(self.root); w.title("GE price alert"); w.configure(bg=self.pal["bg"]); w.transient(self.root)
        try: w.attributes("-topmost", True)
        except tk.TclError: pass
        f = ttk.Frame(w, padding=14); f.pack(fill="both", expand=True)
        ttk.Label(f, text="🔔 Price alert", style="H2.TLabel").pack(anchor="w")
        w._lbl = ttk.Label(f, text=msg, justify="left", wraplength=460); w._lbl.pack(anchor="w", pady=6)
        ttk.Button(f, text="OK", style="Accent.TButton", command=w.destroy).pack(anchor="e")
        w.bind("<Escape>", lambda e: w.destroy()); self._alert_win = w

    def tick(self):
        try:
            if not self.items: pass
            elif self.store.data.get("auto_refresh", True) or self.offline_msg: self.refresh(force=True)
        finally:
            self.root.after(REFRESH_S * 1000, self.tick)

    # compat helpers (v0.6 names)
    def jump_to_watchlist(self, iid): self.add_watch(iid); self.goto("watchlist"); self.watch_page.select(iid)
    def _fill_table(self): self.watch_page.refresh()

    def quit(self):
        try:
            if self.root.state() == "normal": self.store.data["ui"]["geometry"] = self.root.geometry()
            self.store.data["ui"]["tab"] = self.current(); self.store.save()
        except Exception:
            pass
        self.root.destroy()


def main():
    root = tk.Tk()
    App(root); root.mainloop()


if __name__ == "__main__":
    main()
