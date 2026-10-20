"""Pages for the OSRS GE Tracker (v0.7): Watchlist, Item page (price history + best time of day),
Market and My Flips. Uses ge_core's cached API calls only; all fetches run in background threads via app.bg()."""
import time, datetime, tkinter as tk
from tkinter import ttk, messagebox
import ge_core as core
import theme
from ui_common import heading_tips, tip, SearchBox

try:
    import matplotlib
    from matplotlib.figure import Figure
    from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
    HAS_MPL = True
except Exception:
    HAS_MPL = False

RANGES = {"24h": ("5m", 288), "7d": ("1h", 168), "30d": ("6h", 120), "1y": ("24h", 365)}
MARKET_TTL = 300          # recompute movers at most every 5 min (matches /1h cache)
WATCH_FETCH_GAP = 0.3     # seconds between lazy per-item timeseries calls

gp = core.fmt_gp                        # exact price (what you type into the GE)
short_gp = core.short_gp                # compact amount: 1.2k / 3.4m
gpk = gp                                # backwards-compatible alias
pct = lambda v: "-" if v is None else f"{v:.2f}%"
spct = core.fmt_pct
def axis_gp(v, _=None):
    """Chart axis labels: exact below 100k, otherwise k/m/b with enough decimals to tell ticks apart."""
    a = abs(v)
    if a < 100_000: return f"{v:,.0f}"
    for d, suf in ((1e9, "b"), (1e6, "m"), (1e3, "k")):
        if a >= d: return f"{v / d:.2f}".rstrip("0").rstrip(".") + suf


profit_fmt = lambda v: core.signed_gp(v, short=v is not None and abs(v) >= 100_000)


class SortTable(ttk.Frame):
    """Treeview with sortable headers over raw row dicts. cols: [(key, title, width, fmt)] (+ optional tips {key: glossary}).
    Double-click / Enter calls on_double(row); right-click shows the app's item menu when rows carry an 'id'."""
    def __init__(self, master, cols, height=10, on_click=None, on_double=None, title=None, tips=None, app=None,
                 advanced=()):
        super().__init__(master)
        self.app = app
        if title: ttk.Label(self, text=title, style="H2.TLabel").pack(anchor="w", pady=(2, 1))
        self.cols, self.rows, self._sort = cols, [], (None, False)
        self.advanced = set(advanced)
        f = ttk.Frame(self); f.pack(fill="both", expand=True)
        self.tv = ttk.Treeview(f, columns=[c[0] for c in cols], show="headings", height=height, selectmode="browse")
        for k, t, w, _ in cols:
            w = max(w, len(t) * 9 + 20)          # never truncate the (plain-language) header text
            self.tv.heading(k, text=t, command=lambda k=k: self.sort(k))
            self.tv.column(k, width=w, anchor="w" if k in ("name", "day", "when", "why", "notes", "hold") else "e",
                           stretch=k in ("name", "why", "notes"))
        sb = ttk.Scrollbar(f, command=self.tv.yview); self.tv.configure(yscrollcommand=sb.set)
        self.tv.pack(side="left", fill="both", expand=True); sb.pack(side="right", fill="y")
        if on_click: self.tv.bind("<<TreeviewSelect>>", lambda e: self._cb(on_click))
        if on_double:
            self.tv.bind("<Double-Button-1>", lambda e: self.tv.identify_region(e.x, e.y) == "cell" and self._cb(on_double))
            self.tv.bind("<Return>", lambda e: self._cb(on_double))
        if app: self.tv.bind("<Button-3>", self._menu)
        if tips: heading_tips(self.tv, tips, (lambda: app.pal) if app else None)
        self.sign_key = None
        if app: theme.tag_trees(self, app.pal)
        self.show_advanced(False)

    def show_advanced(self, on):
        self.tv["displaycolumns"] = [c[0] for c in self.cols if on or c[0] not in self.advanced]

    def _menu(self, e):
        row = self.tv.identify_row(e.y)
        if not row: return
        self.tv.selection_set(row); r = self.selected()
        if r and r.get("id"): self.app.item_menu(r["id"], e)

    def _cb(self, fn):
        r = self.selected()
        if r is not None: fn(r)

    def selected(self):
        s = self.tv.selection()
        return self.rows[int(s[0])] if s else None

    def set_rows(self, rows, sign_key=None):
        self.rows, self.sign_key = list(rows), sign_key or self.sign_key
        k, rev = self._sort
        if k: self._apply_sort(k, rev)
        self._render()

    def _render(self):
        self.tv.delete(*self.tv.get_children())
        for i, r in enumerate(self.rows):
            v = r.get(self.sign_key) if self.sign_key else None
            tag = () if not v else (("pos",) if v > 0 else ("neg",))
            self.tv.insert("", "end", iid=str(i), tags=tag, values=[f(r.get(k)) for k, _, _, f in self.cols])

    def _apply_sort(self, k, rev):
        def key(r):
            v = r.get(k)
            return (v is None, v.lower() if isinstance(v, str) else (v if v is not None else 0))
        self.rows.sort(key=key, reverse=rev)
        if rev:  # keep None last
            self.rows.sort(key=lambda r: r.get(k) is None)

    def sort(self, k):
        rev = not self._sort[1] if self._sort[0] == k else (k != "name")
        self._sort = (k, rev); self._apply_sort(k, rev); self._render()


# ======================================================================================
class WatchlistPage(ttk.Frame):
    """Watchlist table (+ advanced 24h stats columns and the best-time scanner)."""
    def __init__(self, master, app):
        super().__init__(master, padding=6)
        self.app, self.api = app, app.api
        self.summ, self._watch_busy, self._watch_t = {}, False, 0
        bar = ttk.Frame(self); bar.pack(fill="x", pady=(0, 4))
        ttk.Label(bar, text="Your watchlist", style="Title.TLabel").pack(side="left")
        ttk.Label(bar, text="   Add:").pack(side="left")
        self.search = SearchBox(bar, app, self._add, width=28, placeholder="type an item to add…")
        self.search.pack(side="left", padx=4)
        b = ttk.Button(bar, text="Open item page", command=lambda: self._sel() and app.open_item(self._sel())); b.pack(side="left", padx=2)
        b = ttk.Button(bar, text="Set alert…", command=lambda: app.alert_dialog(self._sel())); b.pack(side="left", padx=2)
        tip(b, "Get a popup + sound when the price goes above or below a number you choose.", app)
        ttk.Button(bar, text="Remove", command=self._remove).pack(side="left", padx=2)
        self.hint = ttk.Label(bar, text="Double-click a row for charts & best time to trade · right-click for more",
                              style="Muted.TLabel"); self.hint.pack(side="right")
        cols = [("name", "Item", 220, str), ("low", "Buy at", 95, gp), ("high", "Sell at", 95, gp),
                ("margin", "Profit each", 95, profit_fmt), ("roi", "ROI", 65, pct), ("limit", "Buy limit", 75, gp),
                ("profit_limit", "Profit per limit", 105, profit_fmt), ("vol", "Traded last hour", 110, short_gp),
                ("updated", "Last trade", 85, str), ("alert", "Alert", 120, str),
                ("chg", "24h change", 85, spct), ("volatility", "Price swings", 90, pct), ("mtrend", "Profit trend", 105,
                                                                                         lambda v: v or "-")]
        tips = {"low": "buy_at", "high": "sell_at", "margin": "profit_each", "roi": "roi", "limit": "limit",
                "profit_limit": "profit_limit", "vol": "vol", "updated": "updated", "alert": "alert", "chg": "chg24",
                "volatility": "volatility", "mtrend": "trend"}
        self.pane = ttk.PanedWindow(self, orient="vertical"); self.pane.pack(fill="both", expand=True)
        self.table = SortTable(self.pane, cols, height=14, on_double=lambda r: app.open_item(r["id"]), tips=tips, app=app,
                               advanced=("chg", "volatility", "mtrend"))
        self.pane.add(self.table, weight=3)
        self.empty = ttk.Label(self.table, text="Your watchlist is empty.\nSearch above (or press Ctrl+F) to add items you care about.",
                               style="Muted.TLabel", justify="center", font=("Segoe UI", 11))
        # --- best-time scanner (advanced) ---
        self.scan_f = ttk.Frame(self.pane, padding=(0, 6, 0, 0))
        sb = ttk.Frame(self.scan_f); sb.pack(fill="x")
        ttk.Label(sb, text="Best time to trade (your watchlist", style="H2.TLabel").pack(side="left")
        self.scan_top = tk.IntVar(value=0)
        ttk.Label(sb, text=" + top").pack(side="left")
        ttk.Spinbox(sb, from_=0, to=18, textvariable=self.scan_top, width=3).pack(side="left")
        ttk.Label(sb, text="best flips)", style="H2.TLabel").pack(side="left")
        ttk.Button(sb, text="Scan", command=self.scan_roi).pack(side="left", padx=6)
        self.scan_lbl = ttk.Label(sb, text="Finds the hours each item is usually cheapest / priciest. Double-click a row for details.",
                                  style="Muted.TLabel"); self.scan_lbl.pack(side="left")
        scols = [("name", "Item", 200, str), ("roi_now", "ROI now", 70, pct), ("roi_t", "ROI if timed", 90, pct),
                 ("m_t", "Profit each if timed", 120, profit_fmt), ("buy_h", "Buy around (ET)", 110, str),
                 ("sell_h", "Sell around (ET)", 110, str), ("peak_h", "Busiest hour", 90, str), ("conf", "Confidence", 85, str)]
        self.t_scan = SortTable(self.scan_f, scols, height=6, on_double=lambda r: app.open_item(r["id"], view="timing"),
                                tips={"roi_now": "roi", "roi_t": "best_time", "m_t": "profit_each", "buy_h": "best_time",
                                      "sell_h": "best_time", "conf": "confidence"}, app=app)
        self.t_scan.pack(fill="both", expand=True)
        self._scan_busy, self._scan_shown = False, False

    def _sel(self):
        r = self.table.selected(); return r and r["id"]

    def _add(self, iid):
        self.app.add_watch(iid); self.select(iid)

    def select(self, iid):
        for i, r in enumerate(self.table.rows):
            if r["id"] == iid: self.table.tv.selection_set(str(i)); self.table.tv.see(str(i))

    def _remove(self):
        iid = self._sel()
        if not iid: self.app.status("Select an item in the list first."); return
        self.app.remove_watch(iid)

    def show_advanced(self, on):
        self.table.show_advanced(on)
        if on and not self._scan_shown: self.pane.add(self.scan_f, weight=2); self._scan_shown = True
        elif not on and self._scan_shown: self.pane.forget(self.scan_f); self._scan_shown = False
        if on: self.refresh_stats()

    def rows(self):
        app, out = self.app, []
        for iid in app.store.data["watch"]:
            it = app.items.get(iid)
            if not it: continue
            p = app.latest.get(str(iid), {})
            hi, lo = p.get("high"), p.get("low")
            m, roi = core.margin(hi, lo, it["name"])
            ts = max(p.get("highTime") or 0, p.get("lowTime") or 0)
            a = app.store.data["alerts"].get(str(iid))
            al = "" if not a else " ".join(x for x in (a.get("above") and f"≥{core.short_gp(a['above'])}",
                                                        a.get("below") and f"≤{core.short_gp(a['below'])}") if x)
            s = self.summ.get(iid, {}); md = s.get("margin_trend")
            lim = it.get("limit")
            out.append({"id": iid, "name": it["name"], "high": hi, "low": lo, "margin": m, "roi": roi, "limit": lim,
                        "profit_limit": m * lim if (m is not None and lim) else None, "vol": app.vol.get(str(iid)),
                        "updated": core.ago(time.time() - ts) if ts else "-", "alert": al,
                        "chg": s.get("chg"), "volatility": s.get("vol"),
                        "mtrend": None if not s else (f"{s['arrow']} {core.signed_gp(md)}" if md is not None else s["arrow"])})
        return out

    def refresh(self):
        sel = self._sel()
        self.table.set_rows(self.rows(), "margin")
        if sel: self.select(sel)
        if self.app.store.data["watch"]: self.empty.place_forget()
        else: self.empty.place(relx=.5, rely=.45, anchor="center")
        if self.app.advanced and time.time() - self._watch_t > 300: self.refresh_stats()

    def refresh_stats(self):
        """Lazy 1h timeseries per watched item -> 24h change / volatility / margin trend (advanced columns)."""
        if self._watch_busy or not self.app.items: return
        todo = list(self.app.store.data["watch"])
        if not todo: return
        self._watch_busy = True; self._watch_t = time.time()
        def work():
            for iid in todo:
                try:
                    cached = (iid, "1h") in self.api._ts and time.time() - self.api._ts[(iid, "1h")][0] < 300
                    d = self.api.timeseries(iid, "1h")
                    self.summ[iid] = core.watch_summary(d, name=self.app.items.get(iid, {}).get("name"))
                    if not cached: time.sleep(WATCH_FETCH_GAP)
                except Exception:
                    pass
        def done(_):
            self._watch_busy = False; self.refresh()
        def fail(_e): self._watch_busy = False
        self.app.bg(work, done, "Loading 24h stats", on_error=fail)

    def scan_roi(self):
        if self._scan_busy: return
        app = self.app; ids = list(app.store.data["watch"])
        n = max(0, self.scan_top.get() or 0)
        if n:
            fl = core.best_flips(app.items, app.latest, app.vol, settings=core.ticker_settings(app.store.data))
            ids += [r["id"] for r in fl[:n] if r["id"] not in ids]
        if not ids: self.scan_lbl.config(text="Your watchlist is empty."); return
        self._scan_busy = True; st = app.timing_settings(); cache = app.item_page.tcache
        def work():
            for i, iid in enumerate(ids):
                try:
                    cached = (iid, "1h") in self.api._ts and time.time() - self.api._ts[(iid, "1h")][0] < 300
                    d = self.api.timeseries(iid, "1h")
                    it = app.items.get(iid, {})
                    cache[iid] = core.timing_analysis(d, it.get("name"), it.get("limit"), st)
                    self.after(0, lambda i=i: self.scan_lbl.config(text=f"Scanning {i + 1} of {len(ids)}…"))
                    if not cached: time.sleep(WATCH_FETCH_GAP * 2)
                except Exception:
                    pass
            return ids
        def done(ids):
            self._scan_busy = False; self._render_scan(ids)
        def fail(_e): self._scan_busy = False
        app.bg(work, done, "Best-time scan", on_error=fail)

    def _render_scan(self, ids):
        rows = []; cache = self.app.item_page.tcache
        for iid in ids:
            ta = cache.get(iid); nm = self.app.items.get(iid, {}).get("name", str(iid))
            p = self.app.latest.get(str(iid), {}); _, rn = core.margin(p.get("high"), p.get("low"), nm)
            if not ta: continue
            b, r = ta["best"], ta["roi"]
            rows.append({"id": iid, "name": nm, "roi_now": rn, "roi_t": r and r["roi"], "m_t": r and r["margin"],
                         "buy_h": core.fmt_hour(b["buy_h"], b["window"]), "sell_h": core.fmt_hour(b["sell_h"], b["window"]),
                         "peak_h": core.fmt_hour(b["peak_h"]), "conf": ta["conf"].title()})
        self.t_scan.set_rows(sorted(rows, key=lambda r: -(r["roi_t"] or -1e9)), "roi_t")
        self.scan_lbl.config(text=f"{len(rows)} items scanned at {datetime.datetime.now():%H:%M} · times are US Eastern (ET)")


# ======================================================================================
class ItemPage(ttk.Frame):
    """One page per item: key numbers in plain words, price history chart, best time of day (timing/ROI)."""
    def __init__(self, master, app):
        super().__init__(master, padding=8)
        self.app, self.api = app, app.api
        self.iid, self.tcache = None, {}
        self._dive_args = self._timing_draw_args = None
        self._cbar = None
        # header
        hd = ttk.Frame(self); hd.pack(fill="x")
        self.icon_lbl = ttk.Label(hd); self.icon_lbl.pack(side="left", padx=(0, 8))
        self.title = ttk.Label(hd, text="No item selected", style="Title.TLabel"); self.title.pack(side="left")
        self.sub = ttk.Label(hd, text="", style="Muted.TLabel"); self.sub.pack(side="left", padx=8)
        self.btn_watch = ttk.Button(hd, text="☆ Add to watchlist", command=self._toggle_watch, width=20)
        self.btn_flip = ttk.Button(hd, text="Log a flip…", command=lambda: self.iid and app.log_flip(self.iid))
        self.btn_alert = ttk.Button(hd, text="Set alert…", command=lambda: self.iid and app.alert_dialog(self.iid))
        for b in (self.btn_flip, self.btn_alert, self.btn_watch): b.pack(side="right", padx=2)
        tip(self.btn_flip, "Log what you bought - it waits in My Flips → Open flips until you press 'Sold…'.", app)
        self.search = SearchBox(hd, app, lambda iid: app.open_item(iid), width=26, placeholder="open another item…")
        self.search.pack(side="right", padx=10)
        # fact cards
        self.facts = ttk.Frame(self); self.facts.pack(fill="x", pady=6)
        self.fact = {}
        for i, (k, label, g) in enumerate((("low", "Buy at", "buy_at"), ("high", "Sell at", "sell_at"),
                                           ("margin", "Profit each", "profit_each"), ("roi", "ROI", "roi"),
                                           ("limit", "Buy limit (4h)", "limit"), ("pl", "Profit per limit", "profit_limit"),
                                           ("vol", "Traded last hour", "vol"), ("age", "Last trade", "updated"))):
            c = ttk.Frame(self.facts, style="Card.TFrame", padding=(10, 4)); c.grid(row=0, column=i, sticky="nsew", padx=3)
            self.facts.columnconfigure(i, weight=1)
            t = ttk.Label(c, text=label, style="CardMuted.TLabel"); t.pack(anchor="w")
            v = ttk.Label(c, text="-", style="CardTitle.TLabel", font=("Segoe UI", 12, "bold")); v.pack(anchor="w")
            for w in (c, t, v): tip(w, g, app)
            self.fact[k] = v
        self.rec = ttk.Label(self, text="", style="Info.TLabel", font=("Segoe UI", 10, "bold"), wraplength=1250, justify="left")
        self.rec.pack(anchor="w")
        tip(self.rec, "best_time", app)
        # view switch
        ctl = ttk.Frame(self); ctl.pack(fill="x", pady=(6, 2))
        self.view = tk.StringVar(value="history")
        ttk.Radiobutton(ctl, text="Price history", value="history", variable=self.view, command=self._show_view).pack(side="left")
        rb = ttk.Radiobutton(ctl, text="Best time of day", value="timing", variable=self.view, command=self._show_view)
        rb.pack(side="left", padx=8); tip(rb, "best_time", app)
        self.rng_f = ttk.Frame(ctl); self.rng_f.pack(side="left", padx=20)
        ttk.Label(self.rng_f, text="Range:").pack(side="left")
        self.rng = tk.StringVar(value="24h")
        for r in RANGES:
            ttk.Radiobutton(self.rng_f, text=r, value=r, variable=self.rng, command=self.load_dive).pack(side="left")
        self.loading = ttk.Label(ctl, text="", style="Muted.TLabel"); self.loading.pack(side="left", padx=10)
        self.adv_f = ttk.Frame(ctl)
        ttk.Button(self.adv_f, text="Reload", command=self.load_timing).pack(side="right")
        self.t_win = tk.IntVar(value=2); self.n_start, self.n_end = tk.IntVar(value=18), tk.IntVar(value=6)
        for lbl, var, lo, hi, g in (("Window (h)", self.t_win, 1, 6, "Length of the buy / sell window in hours."),
                                    ("Night ends", self.n_end, 0, 23, "End hour (ET) of the 'last night' window."),
                                    ("Night starts", self.n_start, 0, 23, "Start hour (ET) of the 'last night' window.")):
            ttk.Spinbox(self.adv_f, from_=lo, to=hi, textvariable=var, width=3).pack(side="right")
            l = ttk.Label(self.adv_f, text=lbl); l.pack(side="right", padx=(8, 2)); tip(l, g, app)
        self.adv_stats = ttk.Label(self, text="", font=("Segoe UI", 9), justify="left", wraplength=1250)
        # charts
        self.host = ttk.Frame(self); self.host.pack(fill="both", expand=True)
        self.empty = ttk.Label(self.host, text="Search for an item (Ctrl+F) or double-click any item in a list to see it here.",
                               style="Muted.TLabel", font=("Segoe UI", 11))
        self.empty.place(relx=.5, rely=.4, anchor="center")
        if HAS_MPL:
            self.dfig = Figure(figsize=(8, 4), dpi=90)
            self.ax_p = self.dfig.add_subplot(211); self.ax_v = self.dfig.add_subplot(212, sharex=self.ax_p)
            self.dcanvas = FigureCanvasTkAgg(self.dfig, master=self.host)
            self.tfig = Figure(figsize=(9, 3.6), dpi=90)
            gs = self.tfig.add_gridspec(2, 2, width_ratios=[2.2, 1])
            self.ax_tp = self.tfig.add_subplot(gs[0, 0]); self.ax_tv = self.tfig.add_subplot(gs[1, 0], sharex=self.ax_tp)
            self.ax_hm = self.tfig.add_subplot(gs[:, 1])
            self.tcanvas = FigureCanvasTkAgg(self.tfig, master=self.host)
        else:
            ttk.Label(self.host, text="Install matplotlib for charts (py -3 -m pip install --user matplotlib).").pack()
        self.set_advanced(False)

    # ---- helpers ----
    def name(self, iid): return self.app.items.get(iid, {}).get("name", str(iid))

    def set_advanced(self, on):
        self._adv = on
        if on:
            self.adv_stats.pack(anchor="w", before=self.host)
            if self.view.get() == "timing": self.adv_f.pack(side="right")
        else:
            self.adv_stats.pack_forget(); self.adv_f.pack_forget()
        if self._dive_args: self._draw_dive(*self._dive_args)

    def apply_settings(self, st):
        self.t_win.set(st.get("window", 2)); self.n_start.set(st.get("night_start", 18)); self.n_end.set(st.get("night_end", 6))

    def _show_view(self):
        if not HAS_MPL or not self.iid: return
        v = self.view.get()
        self.dcanvas.get_tk_widget().pack_forget(); self.tcanvas.get_tk_widget().pack_forget()
        if v == "history":
            self.dcanvas.get_tk_widget().pack(fill="both", expand=True); self.rng_f.pack(side="left", padx=20, before=self.loading)
            self.adv_f.pack_forget()
        else:
            self.tcanvas.get_tk_widget().pack(fill="both", expand=True); self.rng_f.pack_forget()
            if self._adv: self.adv_f.pack(side="right")
        self._update_adv_text()

    def open(self, iid, view=None):
        self.iid = iid; self.empty.place_forget()
        self._dive_args = self._timing_draw_args = None
        if view: self.view.set(view)
        it = self.app.items.get(iid, {})
        self.title.config(text=it.get("name", str(iid)))
        self.sub.config(text=("Members" if it.get("members") else "Free-to-play") +
                        (f" · {it['examine']}" if it.get("examine") else ""))
        img = self.app.icons.get(it, lambda im, iid=iid: im and self.iid == iid and self.icon_lbl.config(image=im))
        self.icon_lbl.config(image=img or "")
        self.rec.config(text="Working out the best time of day to trade…")
        self.update_prices(); self._show_view(); self.load_dive(); self.load_timing()

    def _toggle_watch(self):
        if not self.iid: return
        if self.iid in self.app.store.data["watch"]: self.app.remove_watch(self.iid)
        else: self.app.add_watch(self.iid)
        self.update_prices()

    def update_prices(self):
        """Refresh fact cards from the latest bulk prices (called on every price refresh)."""
        iid = self.iid
        if not iid: return
        it = self.app.items.get(iid, {}); p = self.app.latest.get(str(iid), {})
        hi, lo = p.get("high"), p.get("low"); m, roi = core.margin(hi, lo, it.get("name"))
        lim = it.get("limit"); ts = max(p.get("highTime") or 0, p.get("lowTime") or 0)
        sty = lambda v: "CardPos.TLabel" if v and v > 0 else "CardNeg.TLabel" if v and v < 0 else "CardTitle.TLabel"
        vals = {"low": (gp(lo), None), "high": (gp(hi), None), "margin": (core.signed_gp(m, short=False), m),
                "roi": (pct(roi), roi), "limit": (gp(lim), None),
                "pl": (core.signed_gp(m * lim) if m is not None and lim else "-", m),
                "vol": (short_gp(self.app.vol.get(str(iid))), None), "age": (core.ago(time.time() - ts) if ts else "-", None)}
        for k, (txt, sign) in vals.items():
            self.fact[k].config(text=txt, style=sty(sign) if k in ("margin", "roi", "pl") else "CardTitle.TLabel")
        watching = iid in self.app.store.data["watch"]
        self.btn_watch.config(text="★ Watching (remove)" if watching else "☆ Add to watchlist")

    # ---- price history (was: Deep dive) ----
    def load_dive(self):
        iid = self.iid
        if not iid: return
        step, n = RANGES[self.rng.get()]
        self.loading.config(text="Loading chart…")
        self.app.bg(lambda: self.api.timeseries(iid, step, max_age=3600 if step == "24h" else None)[-n:],
                    lambda d: self._draw_dive(iid, d), "Loading chart", on_error=lambda e: self.loading.config(text=""))

    def _draw_dive(self, iid, d):
        if iid != self.iid: return
        self.loading.config(text="")
        self._dive_args = (iid, d); P = self.app.pal
        rng = self.rng.get(); step = RANGES[rng][0]
        mids = core.series_mids(d); st = core.series_stats(mids)
        vols = [core.h1_vol(x) for x in d]
        self._dive_txt = (f"Last {rng}: low {gp(st['min'] and round(st['min']))} · high {gp(round(st['max']))} · "
                          f"average {gp(round(st['avg']))} · change {spct(st['chg'])} · price swings per {step} "
                          f"{pct(core.volatility(mids))} · total traded {short_gp(sum(vols))}") if st else "No trades in this range."
        self._update_adv_text()
        if not HAS_MPL: return
        t = [datetime.datetime.fromtimestamp(x["timestamp"]) for x in d]
        nan = lambda s: [v if v else float("nan") for v in s]
        ax, av = self.ax_p, self.ax_v; ax.clear(); av.clear()
        p = self.app.latest.get(str(iid), {}); hi, lo = p.get("high"), p.get("low")
        ax.plot(t, nan([x.get("avgHighPrice") for x in d]), color=P["s_high"], lw=1, alpha=.8, label="sell at (avg high)")
        ax.plot(t, nan([x.get("avgLowPrice") for x in d]), color=P["s_low"], lw=1, alpha=.8, label="buy at (avg low)")
        if self._adv:
            ax.plot(t, nan(mids), color=P["s_mid"], lw=1.2, label="mid")
            short, long_ = (12, 48) if step == "5m" else (6, 24) if step == "1h" else (4, 20) if step == "6h" else (7, 30)
            ax.plot(t, nan(core.sma(mids, short)), color=P["s_sma1"], lw=1.2, ls="--", label=f"avg of {short} steps")
            ax.plot(t, nan(core.sma(mids, long_)), color=P["s_sma2"], lw=1.2, ls="--", label=f"avg of {long_} steps")
            if hi and lo:
                ax.axhspan(min(hi, lo), max(hi, lo), color=P["spread"], alpha=.35, label="current spread")
        ax.legend(loc="upper left", fontsize=7, ncol=3); theme.grid(ax, P)
        fmt = matplotlib.ticker.FuncFormatter(axis_gp)
        ax.yaxis.set_major_formatter(fmt); ax.set_ylabel("price (gp)")
        w = {"5m": 4 / 1440, "1h": 1 / 24 * .8, "6h": .25 * .8, "24h": .8}[step]
        av.bar(t, [x.get("highPriceVolume") or 0 for x in d], width=w, color=P["s_high"], label="bought")
        av.bar(t, [x.get("lowPriceVolume") or 0 for x in d], width=w, bottom=[x.get("highPriceVolume") or 0 for x in d],
               color=P["s_low"], label="sold")
        av.yaxis.set_major_formatter(fmt); av.set_ylabel("traded"); theme.grid(av, P)
        av.legend(loc="upper left", fontsize=7)
        self.dfig.autofmt_xdate(); self.dfig.tight_layout(); theme.style_fig(self.dfig, P); self.dcanvas.draw_idle()

    # ---- best time of day (was: Timing / ROI) ----
    def _tsettings(self):
        try: w = max(1, int(self.t_win.get() or 2))
        except (tk.TclError, ValueError): w = 2
        return {"window": w}

    def load_timing(self):
        iid = self.iid
        if not iid: return
        def work():
            d1 = self.api.timeseries(iid, "1h"); time.sleep(WATCH_FETCH_GAP)
            d5 = self.api.timeseries(iid, "5m")
            return d1, d5
        self.app.bg(work, lambda r: self._draw_timing(iid, *r), "Loading best-time data",
                    on_error=lambda e: self.rec.config(text="Couldn't load trading history for this item right now."))

    def _draw_timing(self, iid, d1, d5):
        if iid != self.iid: return
        it = self.app.items.get(iid, {}); nm = self.name(iid); lim = it.get("limit")
        last1 = max((x["timestamp"] for x in d1), default=0)
        merged = list(d1) + [x for x in d5 if x["timestamp"] >= last1 + 3600]
        try:
            ta = core.timing_analysis(merged, nm, lim, self._tsettings())
        except RuntimeError as e:          # no tz data on Windows
            self.rec.config(text=str(e)); return
        self.tcache[iid] = ta
        prof, bh, r = ta["profile"], ta["best"], ta["roi"]
        days = len({x["timestamp"] // 86400 for x in d1})
        w = bh["window"]
        if r:
            pl = f", about {short_gp(r['per_limit'])} per buy limit" if r["per_limit"] is not None else ""
            rec = (f"Best time to trade (last ~{days} days): usually cheapest {core.fmt_hour(bh['buy_h'], w)} ET "
                   f"(~{gp(r['buy'])}), priciest {core.fmt_hour(bh['sell_h'], w)} ET (~{gp(r['sell'])}) → "
                   f"{core.signed_gp(r['margin'], short=False)} each after tax ({r['roi']:.2f}%){pl}. "
                   f"Confidence: {ta['conf'].title()}.")
            if ta["conf"] in ("low", "none"): rec = "⚠ Weak pattern - treat as a guess. " + rec
        else:
            rec = f"No clear best time of day for this item ({ta['why']})."
        self.rec.config(text=rec)
        try: nt = core.night_window(merged, start=self.n_start.get() % 24, end=self.n_end.get() % 24)
        except (tk.TclError, ValueError): nt = {"n": 0}
        p = self.app.latest.get(str(iid), {})
        lines = [f"Confidence detail: {ta['why']}."]
        if nt["n"]:
            cmp = ""
            if p.get("low") and nt.get("peak_low"):
                cmp = f" · buy price now {gp(p['low'])} ({spct(core.pct_change(nt['peak_low'], p['low']))} vs then)"
            lines.append(f"Last night {nt['start']:%a %I%p}-{nt['end']:%a %I%p} ET: busiest at {core.fmt_hour(nt['peak_h'])} "
                         f"({nt['peak_vol']:,} traded), buy {gp(nt['peak_low'] and round(nt['peak_low']))} / sell "
                         f"{gp(nt['peak_high'] and round(nt['peak_high']))} then; {nt['total_vol']:,} traded overnight{cmp}")
        lines.append(f"Busiest hour {core.fmt_hour(bh['peak_h'])}, quietest {core.fmt_hour(bh['quiet_h'])} ET. "
                     "Past tendencies, not guarantees: offers fill fastest in busy hours; a high sell in quiet hours may sit.")
        self._timing_txt = "\n".join(lines)
        self._update_adv_text()
        self._timing_draw_args = (prof, bh, d1)
        self._render_timing_charts(prof, bh, d1)

    def _update_adv_text(self):
        t = getattr(self, "_timing_txt", "") if self.view.get() == "timing" else getattr(self, "_dive_txt", "")
        self.adv_stats.config(text=t)

    def _render_timing_charts(self, prof, bh, d1):
        if not HAS_MPL: return
        P = self.app.pal
        ap, av, hm = self.ax_tp, self.ax_tv, self.ax_hm
        for a in (ap, av, hm): a.clear()
        if self._cbar:
            try: self._cbar.remove()
            except Exception: pass
            self._cbar = None
        H = list(range(24)); nan = lambda xs: [x if x else float("nan") for x in xs]
        w = bh["window"]
        buys = {(bh["buy_h"] + i) % 24 for i in range(w)} if bh["buy_h"] is not None else set()
        sells = {(bh["sell_h"] + i) % 24 for i in range(w)} if bh["sell_h"] is not None else set()
        ap.plot(H, nan([h["high"] for h in prof]), "-o", ms=3, color=P["s_high"], label="sell at (avg high)")
        ap.plot(H, nan([h["low"] for h in prof]), "-o", ms=3, color=P["s_low"], label="buy at (avg low)")
        if self._adv: ap.plot(H, nan([h["mid"] for h in prof]), "--", color=P["s_mid"], lw=1, label="mid")
        for h in buys: ap.axvspan(h - .5, h + .5, color=P["buy_band"], alpha=.18)
        for h in sells: ap.axvspan(h - .5, h + .5, color=P["sell_band"], alpha=.15)
        ap.set_title("Average price by hour (ET) - green = cheapest (buy), red = priciest (sell)", fontsize=9)
        ap.legend(fontsize=7, loc="best"); theme.grid(ap, P)
        fmt = matplotlib.ticker.FuncFormatter(axis_gp); ap.yaxis.set_major_formatter(fmt)
        cols = [P["buy_band"] if h in buys else P["sell_band"] if h in sells else
                P["vol_lo"] if h not in bh["eligible"] else P["vol_hi"] for h in H]
        av.bar(H, [h["vol"] or 0 for h in prof], color=cols)
        if bh["median_vol"]: av.axhline(bh["median_vol"], color=P["zero"], lw=.8, ls=":")
        av.set_title("Average amount traded per hour (pale = too quiet to count)", fontsize=9)
        av.yaxis.set_major_formatter(fmt); theme.grid(av, P)
        av.set_xticks(H); av.set_xticklabels([core.fmt_hour(h).replace("AM", "a").replace("PM", "p") for h in H], fontsize=7)
        ap.tick_params(labelsize=7); av.tick_params(labelsize=7)
        mat = core.dow_hour_volume(d1)
        if sum(1 for row in mat for v in row if v is not None) >= 24 * 5:
            img = [[float("nan") if v is None else v for v in row] for row in mat]
            im = hm.imshow(img, aspect="auto", cmap=P["cmap"], origin="upper")
            hm.set_yticks(range(7)); hm.set_yticklabels(["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"], fontsize=7)
            hm.set_xticks(range(0, 24, 3)); hm.set_xticklabels([core.fmt_hour(h) for h in range(0, 24, 3)], fontsize=6)
            self._cbar = self.tfig.colorbar(im, ax=hm, fraction=.05); self._cbar.ax.tick_params(labelsize=6)
            self._cbar.ax.set_facecolor(P["chart_bg"]); self._cbar.outline.set_edgecolor(P["grid"])
            for t in self._cbar.ax.get_yticklabels(): t.set_color(P["chart_fg"])
            hm.set_title("Busiest times: weekday × hour (ET)", fontsize=9)
        else:
            hm.axis("off"); hm.text(.5, .5, "Not enough data\nfor weekday heatmap", ha="center", color=P["chart_fg"], transform=hm.transAxes)
        self.tfig.tight_layout(); theme.style_fig(self.tfig, P); self.tcanvas.draw_idle()

    def retheme(self):
        P = self.app.pal; self.search.retheme()
        if self._dive_args: self._draw_dive(*self._dive_args)
        elif HAS_MPL: theme.style_fig(self.dfig, P); self.dcanvas.draw_idle()
        if self._timing_draw_args: self._render_timing_charts(*self._timing_draw_args)
        elif HAS_MPL: theme.style_fig(self.tfig, P); self.tcanvas.draw_idle()


# ======================================================================================
class MarketPage(ttk.Frame):
    def __init__(self, master, app):
        super().__init__(master, padding=6)
        self.app, self.api = app, app.api
        self._market_t, self._market_busy = 0, False
        bar = ttk.Frame(self); bar.pack(fill="x")
        ttk.Label(bar, text="Market today", style="Title.TLabel").pack(side="left")
        self.market_lbl = ttk.Label(bar, text="  Loads after the first price update.", style="Muted.TLabel")
        self.market_lbl.pack(side="left")
        self.adv_f = ttk.Frame(bar)
        self.minvol = tk.IntVar(value=core.MOVER_DEFAULTS["min_vol"])
        ttk.Button(self.adv_f, text="Apply", command=lambda: self.refresh(force=True)).pack(side="right")
        ttk.Spinbox(self.adv_f, from_=0, to=1_000_000, increment=100, textvariable=self.minvol, width=8).pack(side="right")
        l = ttk.Label(self.adv_f, text="Min traded per hour:"); l.pack(side="right"); tip(l, "min_vol", app)
        ttk.Label(bar, text="Double-click any item to open it", style="Muted.TLabel").pack(side="right", padx=10)
        g = ttk.Frame(self); g.pack(fill="both", expand=True)
        mcols = [("name", "Item", 180, str), ("old", "Price yesterday", 100, lambda v: gp(v and round(v))),
                 ("now", "Price now", 90, lambda v: gp(v and round(v))), ("chg", "Change", 70, spct),
                 ("vol", "Traded last hour", 100, short_gp), ("value", "gp traded", 80, short_gp)]
        tcols = [("name", "Item", 180, str), ("mid", "Price", 90, lambda v: gp(v and round(v))),
                 ("vol", "Traded last hour", 110, short_gp), ("value", "gp traded", 90, short_gp)]
        tips = {"old": "mid", "now": "mid", "mid": "mid", "chg": "chg", "vol": "vol", "value": "value"}
        kw = dict(on_double=lambda r: app.open_item(r["id"]), height=9, tips=tips, app=app)
        self.t_gain = SortTable(g, mcols, title="📈 Biggest price rises (vs yesterday)", **kw)
        self.t_lose = SortTable(g, mcols, title="📉 Biggest price drops (vs yesterday)", **kw)
        self.t_vol = SortTable(g, tcols, title="Most traded items (last hour)", **kw)
        self.t_val = SortTable(g, tcols, title="Most gp traded (last hour)", **kw)
        for i, t in enumerate((self.t_gain, self.t_lose, self.t_vol, self.t_val)):
            t.grid(row=i // 2, column=i % 2, sticky="nsew", padx=3, pady=3)
        g.columnconfigure(0, weight=1); g.columnconfigure(1, weight=1)
        g.rowconfigure(0, weight=1); g.rowconfigure(1, weight=1)

    def set_advanced(self, on):
        self.adv_f.pack(side="right") if on else self.adv_f.pack_forget()

    def refresh(self, force=False):
        if self._market_busy or not self.app.items: return
        if not force and time.time() - self._market_t < MARKET_TTL and self._market_t: return
        self._market_busy = True
        try: mv = max(0, int(self.minvol.get() or 0))
        except (tk.TclError, ValueError): mv = core.MOVER_DEFAULTS["min_vol"]
        items, st = self.app.items, {"min_vol": mv}
        self.app.store.data.setdefault("market", {})["min_vol"] = mv
        self.market_lbl.config(text="  Loading…")
        def work():
            now = self.api.hourly_now()
            old = self.api.hourly_at(time.time() - 25 * 3600)   # completed bucket ~24h before the current one
            return core.market_movers(items, now, old, st), core.most_traded(items, now)
        def done(res):
            self._market_busy = False; self._market_t = time.time()
            (g, l), (v, val) = res
            self.t_gain.set_rows(g, "chg"); self.t_lose.set_rows(l, "chg")
            self.t_vol.set_rows(v); self.t_val.set_rows(val)
            self.market_lbl.config(text=f"  Updated {datetime.datetime.now():%H:%M} · prices compared with the same hour yesterday")
        def fail(e):
            self._market_busy = False; self.market_lbl.config(text="  Couldn't load market data - will retry.")
        self.app.bg(work, done, "Loading market data", on_error=fail)


# ======================================================================================
class FlipsPage(ttk.Frame):
    """My Flips: record flips you've done and see profit (was: Analytics → Flip log)."""
    def __init__(self, master, app):
        super().__init__(master, padding=6)
        self.app = app
        self.flog = app.flog
        top = ttk.Frame(self); top.pack(fill="x")
        ttk.Label(top, text="My flips", style="Title.TLabel").pack(side="left")
        self.f_total = ttk.Label(top, text="", font=("Segoe UI", 11, "bold")); self.f_total.pack(side="left", padx=14)
        self._build_open()
        body = ttk.Frame(self); body.pack(fill="both", expand=True)
        cols = [("when", "When", 120, str), ("name", "Item", 170, str), ("qty", "How many", 70, gp),
                ("buy", "Bought at", 85, gp), ("sell", "Sold at", 85, gp), ("tax", "GE tax", 75, short_gp),
                ("profit", "Profit", 90, profit_fmt)]
        self.t_flips = SortTable(body, cols, height=12, title="All flips (newest first)", app=app,
                                 tips={"tax": "tax", "profit": "Profit after GE tax for the whole flip."},
                                 on_double=lambda r: r.get("id") and app.open_item(r["id"]))
        db = ttk.Button(self.t_flips, text="Delete selected flip", command=self._del_flip); db.pack(anchor="e", pady=2)
        self.t_items = SortTable(body, [("name", "Item", 150, str), ("n", "Flips", 45, gp), ("qty", "Items", 60, short_gp),
                                        ("profit", "Profit", 90, profit_fmt)], height=6, title="By item", app=app)
        self.t_days = SortTable(body, [("day", "Day", 90, str), ("profit", "Profit", 90, profit_fmt)], height=6,
                                title="By day", app=app)
        self.t_flips.grid(row=0, column=0, rowspan=2, sticky="nsew", padx=3)
        self.t_items.grid(row=0, column=1, sticky="nsew", padx=3); self.t_days.grid(row=0, column=2, sticky="nsew", padx=3)
        cf = ttk.Frame(body); cf.grid(row=1, column=1, columnspan=2, sticky="nsew")
        body.columnconfigure(0, weight=3); body.columnconfigure(1, weight=2); body.columnconfigure(2, weight=1)
        body.rowconfigure(0, weight=1); body.rowconfigure(1, weight=1)
        if HAS_MPL:
            self.ffig = Figure(figsize=(4, 2), dpi=80); self.ax_f = self.ffig.add_subplot(111)
            self.fcanvas = FigureCanvasTkAgg(self.ffig, master=cf); self.fcanvas.get_tk_widget().pack(fill="both", expand=True)
        self.render()

    # ---------- open flips (v0.8): log the buy now, enter the sale when it sells ----------
    def _item_values(self, combo, var):
        s = var.get().strip().lower()
        if len(s) >= 2 and getattr(self.app, "names", None):
            starts = [n for n in self.app.names if n.lower().startswith(s)]
            combo["values"] = (starts + [n for n in self.app.names if s in n.lower() and not n.lower().startswith(s)])[:30]

    def _live(self, iid, key):
        return (self.app.latest.get(str(iid)) or {}).get(key) if iid is not None else None

    def _build_open(self):
        app = self.app
        form = ttk.Frame(self, style="Card.TFrame", padding=8); form.pack(fill="x", pady=(0, 6))
        l = ttk.Label(form, text="Log a flip:", style="CardTitle.TLabel"); l.pack(side="left", padx=(0, 6))
        tip(l, "Log what you bought - it goes into 'Open flips' below. When it sells on the GE, "
               "select it and press 'Sold…' (or double-click it) to enter the sold-at price.", app)
        self.o_item, self.o_qty, self.o_buy = (tk.StringVar() for _ in range(3))
        self._o_auto = None
        for lbl, var, w, g in (("Item", self.o_item, 28, None), ("How many", self.o_qty, 8, "qty"),
                               ("Bought at (each)", self.o_buy, 11, "Pre-filled with the current Buy at price - change it "
                                                                   "to what you actually paid. You can type 1.2k or 3m.")):
            lb = ttk.Label(form, text=lbl, style="Card.TLabel"); lb.pack(side="left", padx=(8, 2))
            e = ttk.Combobox(form, textvariable=var, width=w) if var is self.o_item else ttk.Entry(form, textvariable=var, width=w)
            e.pack(side="left")
            if g: tip(lb, g, app); tip(e, g, app)
            e.bind("<Return>", lambda ev: self._log_buy())
            if var is self.o_item: self.o_combo = e
        self.o_item.trace_add("write", lambda *_: (self._item_values(self.o_combo, self.o_item), self._o_prefill()))
        self.o_combo.bind("<<ComboboxSelected>>", lambda e: self._o_prefill())
        ttk.Button(form, text="Log flip", style="Accent.TButton", command=self._log_buy).pack(side="left", padx=8)
        self.o_preview = ttk.Label(form, text="", style="Card.TLabel"); self.o_preview.pack(side="left", padx=4)
        for v in (self.o_qty, self.o_buy): v.trace_add("write", lambda *_: self._o_cost())
        box = ttk.Frame(self); box.pack(fill="x", pady=(0, 4))
        cols = [("when", "Bought", 110, str), ("name", "Item", 170, str), ("qty", "How many", 70, gp),
                ("buy", "Bought at", 85, gp), ("cost", "Total cost", 85, short_gp), ("held", "Held for", 70, str),
                ("live", "Sell at now", 85, gp), ("est", "Est. profit", 90, profit_fmt), ("note", "Note", 120, str)]
        self.t_open = SortTable(box, cols, height=5, title="Open flips (bought, not sold yet)", app=app,
                                tips={"live": "sell_at", "held": "How long since you logged the buy.",
                                      "cost": "How many x Bought at - the gp you have tied up.",
                                      "est": "Profit after GE tax if all of them sold at the current 'Sell at now' price."},
                                on_double=self._sold_dialog)
        self.t_open.pack(side="left", fill="x", expand=True)
        self.o_empty = ttk.Label(self.t_open, text="No open flips. Log a flip above - when it sells, select it and press 'Sold…'.",
                                 style="Muted.TLabel")
        bf = ttk.Frame(box); bf.pack(side="left", fill="y", padx=(6, 0), pady=(24, 0))
        b = ttk.Button(bf, text="Sold…", style="Accent.TButton", command=lambda: self._with_open(self._sold_dialog))
        b.pack(fill="x", pady=2); tip(b, "It sold! Enter the sell price (and how many, for a partial sale). "
                                        "It moves to your flip history below.", app)
        ttk.Button(bf, text="Edit…", command=lambda: self._with_open(self._edit_dialog)).pack(fill="x", pady=2)
        ttk.Button(bf, text="Delete", command=lambda: self._with_open(self._del_open)).pack(fill="x", pady=2)
        self.o_total = ttk.Label(bf, text="", style="Muted.TLabel", justify="left"); self.o_total.pack(fill="x", pady=(6, 0))

    def _o_prefill(self):
        iid = self.app.by_name.get(self.o_item.get().strip().lower())
        low = self._live(iid, "low")
        if low and (not self.o_buy.get().strip() or self.o_buy.get() == self._o_auto):
            self._o_auto = str(low); self.o_buy.set(self._o_auto)

    def _o_cost(self):
        try:
            c = self._num(self.o_qty.get()) * self._num(self.o_buy.get())
            self.o_preview.config(text=f"Cost: {core.fmt_gp(c)} gp")
        except Exception:
            self.o_preview.config(text="")

    def prefill_buy(self, iid, qty=None, buy=None):
        self.o_item.set(self.app.items.get(iid, {}).get("name", ""))
        b = buy or self._live(iid, "low")
        if b: self._o_auto = str(b); self.o_buy.set(self._o_auto)
        self.o_qty.set(str(qty) if qty else "")

    def _log_buy(self):
        name = self.o_item.get().strip()
        iid = self.app.by_name.get(name.lower())
        if iid is None:
            messagebox.showerror("Log a flip", f"I don't know an item called {name!r}. Pick one from the list.", parent=self); return
        try:
            o = self.flog.add_open(iid, self.app.items[iid]["name"], self._num(self.o_qty.get()), self._num(self.o_buy.get()))
        except Exception:
            messagebox.showerror("Log a flip", "Please enter how many you bought and the price you paid each "
                                              "(numbers like 1500, 1.5k or 2m).", parent=self); return
        self.o_qty.set(""); self.render_open(); self.app.on_flips_changed()
        self.app.status(f"Logged flip: {o['qty']:,} × {o['name']} at {core.fmt_gp(o['buy'])}. "
                        "Press 'Sold…' when it sells.")

    def _with_open(self, fn):
        r = self.t_open.selected()
        if r is None: self.app.status("Select an open flip in the list first."); return
        fn(r)

    def _open_form(self, title, r, fields, on_save, preview=None):
        """Small dialog: fields = [(label, initial, tip)]; on_save(values) -> True to close."""
        d = self.app._dialog(title)
        f = ttk.Frame(d, padding=14); f.pack(fill="both", expand=True)
        ttk.Label(f, text=f"{r['qty']:,} × {r['name']}", style="H2.TLabel").grid(row=0, column=0, columnspan=2, sticky="w")
        ttk.Label(f, text=f"Bought at {core.fmt_gp(r['buy'])} each · {core.ago(time.time() - r['ts'])} · "
                          f"Sell at now: {core.fmt_gp(self._live(r['id'], 'high'))}",
                  style="Muted.TLabel").grid(row=1, column=0, columnspan=2, sticky="w", pady=(0, 8))
        vars_ = []
        for i, (lbl, init, g) in enumerate(fields):
            v = tk.StringVar(value=init); vars_.append(v)
            lb = ttk.Label(f, text=lbl); lb.grid(row=2 + i, column=0, sticky="w")
            e = ttk.Entry(f, textvariable=v, width=24); e.grid(row=2 + i, column=1, padx=6, pady=3, sticky="w")
            if g: tip(lb, g, self.app)
            if i == 0: e.focus_set(); e.select_range(0, "end")
        pv = ttk.Label(f, text=""); pv.grid(row=2 + len(fields), column=0, columnspan=2, sticky="w", pady=(4, 6))
        if preview:
            def upd(*_):
                try: t, good = preview([v.get() for v in vars_]); pv.config(text=t, style="Pos.TLabel" if good else "Neg.TLabel")
                except Exception: pv.config(text="", style="TLabel")
            for v in vars_: v.trace_add("write", upd)
            upd()
        def save():
            if on_save([v.get() for v in vars_], d): d.destroy()
        bf = ttk.Frame(f); bf.grid(row=3 + len(fields), column=0, columnspan=2, sticky="e")
        ttk.Button(bf, text="Cancel", command=d.destroy).pack(side="left", padx=4)
        ttk.Button(bf, text="Save", style="Accent.TButton", command=save).pack(side="left")
        d.bind("<Return>", lambda e: save())
        return d

    def _sold_dialog(self, r):
        live = self._live(r["id"], "high")
        def preview(v):
            q = r["qty"] if not v[1].strip() else self._num(v[1]); sp = self._num(v[0])
            p = core.flip_profit(q, r["buy"], sp, r["name"]); tax = core.ge_tax(sp, r["name"]) * q
            left = f" · {r['qty'] - q:,} stay open" if 0 < q < r["qty"] else ""
            return f"Profit after tax: {core.signed_gp(p, short=False)} gp (GE tax {core.fmt_gp(tax)}){left}", p >= 0
        def save(v, d):
            try:
                q = None if not v[1].strip() else self._num(v[1])
                self.flog.sell_open(r["oid"], self._num(v[0]), q)
            except KeyError:
                messagebox.showerror("Sold", "That open flip no longer exists.", parent=d); return True
            except Exception:
                messagebox.showerror("Sold", f"Please enter the sell price each, and how many sold (1 to {r['qty']:,}).",
                                     parent=d); return False
            self.render(); self.app.on_flips_changed(); self.app.status(f"Moved {r['name']} to your flip history."); return True
        self._open_form(f"Sold - {r['name']}", r,
                        [("Sold at (each)", str(live or ""), "Pre-filled with the current Sell at price - change it to "
                                                             "what it actually sold for. You can type 1.2k or 3m."),
                         ("How many sold", str(r["qty"]), "Sold only some? Enter how many - the rest stay open.")],
                        save, preview)

    def _edit_dialog(self, r):
        def save(v, d):
            name = v[0].strip(); iid = self.app.by_name.get(name.lower())
            if iid is None and name.lower() != r["name"].lower():
                messagebox.showerror("Edit open flip", f"I don't know an item called {name!r}.", parent=d); return False
            try:
                kw = {"qty": self._num(v[1]), "buy": self._num(v[2]), "note": v[3]}
                if iid is not None: kw.update(id=iid, name=self.app.items[iid]["name"])
                self.flog.edit_open(r["oid"], **kw)
            except KeyError:
                return True
            except Exception:
                messagebox.showerror("Edit open flip", "Please enter how many and the price you paid each "
                                                       "(numbers like 1500, 1.5k or 2m).", parent=d); return False
            self.render_open(); self.app.on_flips_changed(); return True
        self._open_form(f"Edit open flip - {r['name']}", r,
                        [("Item", r["name"], None), ("How many", str(r["qty"]), "qty"),
                         ("Bought at (each)", str(r["buy"]), "You can type 1.2k or 3m."), ("Note", r.get("note", ""), None)],
                        save)

    def _del_open(self, r):
        if not messagebox.askyesno("Delete open flip", f"Delete this open flip of {r['qty']:,} × {r['name']}?\n"
                                   "(Use 'Sold…' instead if it sold - that keeps it in your profit history.)", parent=self):
            return
        try: self.flog.delete_open(r["oid"])
        except KeyError: pass
        self.render_open(); self.app.on_flips_changed()

    def render_open(self):
        rows = self.flog.open_rows(self.app.latest)
        for r in rows: r["when"] = datetime.datetime.fromtimestamp(r["ts"]).strftime("%Y-%m-%d %H:%M")
        self.t_open.set_rows(sorted(rows, key=lambda r: -r["ts"]), "est")
        if rows: self.o_empty.place_forget()
        else: self.o_empty.place(relx=.5, rely=.6, anchor="center")
        est = [r["est"] for r in rows if r["est"] is not None]
        self.o_total.config(text=f"{len(rows)} open\n{core.short_gp(self.flog.open_cost())} gp in\n"
                                 f"est. {core.signed_gp(sum(est)) if est else '-'}" if rows else "")

    def prefill(self, iid, qty=None, buy=None, sell=None):
        """v0.9: 'Log a flip' only takes the buy; the sell price is entered later via 'Sold…'."""
        self.prefill_buy(iid, qty, buy)

    @staticmethod
    def _num(s):
        s = (s or "").strip().lower().replace(",", "")
        mult = 1_000_000 if s.endswith("m") else 1000 if s.endswith("k") else 1
        return int(float(s.rstrip("mk")) * mult)

    def _log_flip(self): self._log_buy()

    def _del_flip(self):
        r = self.t_flips.selected()
        if r is None: self.app.status("Select a flip in the list first."); return
        if not messagebox.askyesno("Delete flip", f"Delete this flip of {r['qty']:,} × {r['name']}?", parent=self): return
        self.flog.delete(r["idx"]); self.render(); self.app.on_flips_changed()

    def render(self):
        self.render_open()
        rows = [dict(f, idx=i, when=datetime.datetime.fromtimestamp(f["ts"]).strftime("%Y-%m-%d %H:%M"))
                for i, f in enumerate(self.flog.flips)]
        self.t_flips.set_rows(sorted(rows, key=lambda r: -r["ts"]), "profit")
        self.t_items.set_rows([dict(v, name=k) for k, v in self.flog.by_item().items()], "profit")
        self.t_days.set_rows([{"day": k, "profit": v} for k, v in self.flog.by_day().items()], "profit")
        tot = self.flog.total(); td, tn = self.flog.today()
        self.f_total.config(text=f"Today: {core.signed_gp(td)} ({tn} flips)   ·   All time: {core.signed_gp(tot)} "
                                 f"({len(self.flog.flips)} flips, after tax)",
                            style="Pos.TLabel" if tot >= 0 else "Neg.TLabel")
        if HAS_MPL:
            ax = self.ax_f; ax.clear(); c = self.flog.cumulative(); P = self.app.pal
            if c:
                ax.step([datetime.datetime.fromtimestamp(t) for t, _ in c], [v for _, v in c], where="post", color=P["s_sma1"])
                ax.axhline(0, color=P["zero"], lw=.6)
            else:
                ax.text(.5, .5, "Your profit over time\nappears here once you log flips", ha="center", va="center",
                        transform=ax.transAxes, fontsize=9)
            ax.set_title("Total profit over time", fontsize=9); theme.grid(ax, P); ax.tick_params(labelsize=7)
            ax.yaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(axis_gp))
            self.ffig.autofmt_xdate(); self.ffig.tight_layout(); theme.style_fig(self.ffig, P); self.fcanvas.draw_idle()

    def retheme(self):
        self.render()
