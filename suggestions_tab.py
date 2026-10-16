"""Flip Finder page (v0.7): 'Best flips right now' (same ranking as the ticker) + 'Plan for my cash stack'
(the v0.6 Suggestions engine). Simple inputs up front, the rest behind 'More options'. Follows the app theme."""
import time, datetime, tkinter as tk
from tkinter import ttk, messagebox
import ge_core as core, suggestions as sg, theme
from analytics_tab import SortTable, gp, short_gp, pct, profit_fmt
from ui_common import tip

TS_GAP = 0.35            # seconds between lazy per-item timeseries calls (OSRS Wiki API courtesy)
TS_MAX_AGE = 6 * 3600    # 24h-step series barely change; cache for 6h
DISCLAIMER = ("Estimates from past OSRS Grand Exchange prices (OSRS Wiki) - not guarantees. Prices move, "
              "margins close and offers may not fill. In-game gp only.")
FLIP_COLS = [("name", "Item", 200, str), ("low", "Buy at", 90, gp), ("high", "Sell at", 90, gp),
             ("margin", "Profit each", 90, profit_fmt), ("roi", "ROI", 60, pct), ("limit", "Buy limit", 75, gp),
             ("profit_limit", "Profit per limit", 105, profit_fmt), ("vol", "Traded last hour", 105, short_gp)]
FLIP_TIPS = {"low": "buy_at", "high": "sell_at", "margin": "profit_each", "roi": "roi", "limit": "limit",
             "profit_limit": "profit_limit", "vol": "vol"}


class FlipFinderPage(ttk.Frame):
    def __init__(self, master, app):
        super().__init__(master, padding=6)
        self.app, self.api, self.rows, self._busy, self.daily = app, app.api, [], False, {}
        F = app.store.data.get("finder", core.FINDER_DEFAULTS)
        pane = ttk.PanedWindow(self, orient="vertical"); pane.pack(fill="both", expand=True)
        # ---- best flips right now ----
        top = ttk.Frame(pane); pane.add(top, weight=2)
        h = ttk.Frame(top); h.pack(fill="x")
        ttk.Label(h, text="Best flips right now", style="Title.TLabel").pack(side="left")
        self.now_lbl = ttk.Label(h, text="  waiting for prices…", style="Muted.TLabel"); self.now_lbl.pack(side="left")
        ttk.Label(h, text="Double-click to open · right-click to add to watchlist", style="Muted.TLabel").pack(side="right")
        self.t_now = SortTable(top, FLIP_COLS, height=7, on_double=lambda r: app.open_item(r["id"]), tips=FLIP_TIPS, app=app)
        self.t_now.pack(fill="both", expand=True)
        # ---- planner ----
        bot = ttk.Frame(pane, padding=(0, 8, 0, 0)); pane.add(bot, weight=3)
        ttk.Label(bot, text="Plan for my cash stack", style="Title.TLabel").pack(anchor="w")
        f1 = ttk.Frame(bot); f1.pack(fill="x", pady=(4, 0))
        self.cash = tk.StringVar(value=F["cash"]); self.risk = tk.StringVar(value=F["risk"])
        self.strat = tk.StringVar(value=F["strategy"] if F["strategy"] in sg.STRATEGIES else sg.STRATEGIES[0])
        self.mem = tk.StringVar(value=F["members"])
        self.max_items = tk.IntVar(value=F["max_items"]); self.slots = tk.IntVar(value=F["slots"])
        self.fill = tk.StringVar(value=str(F["fill_pct"])); self.cap = tk.StringVar(value=str(F["cap_pct"]))
        self.nobond = tk.BooleanVar(value=F["exclude_bonds"])
        def lab(parent, t, g):
            l = ttk.Label(parent, text=t); l.pack(side="left", padx=(10, 3)); tip(l, g, app); return l
        lab(f1, "I have (gp):", "cash"); e = ttk.Entry(f1, textvariable=self.cash, width=10, font=("Segoe UI", 10)); e.pack(side="left")
        e.bind("<Return>", lambda ev: self.generate()); tip(e, "cash", app)
        lab(f1, "Risk:", "risk"); ttk.Combobox(f1, textvariable=self.risk, values=sg.RISKS, width=8, state="readonly").pack(side="left")
        lab(f1, "Style:", "strategy")
        cb = ttk.Combobox(f1, textvariable=self.strat, values=sg.STRATEGIES, width=16, state="readonly"); cb.pack(side="left")
        tip(cb, "strategy", app)
        self.gen_btn = ttk.Button(f1, text="Find flips for me", style="Accent.TButton", command=self.generate)
        self.gen_btn.pack(side="left", padx=12)
        self.more = tk.BooleanVar(value=False)
        ttk.Checkbutton(f1, text="More options", variable=self.more, command=self._toggle_more).pack(side="left")
        self.f2 = ttk.Frame(bot)
        for t, var, w, lo, hi, inc, g in (("Items:", self.mem, 0, 0, 0, 0, "Only members items, only F2P items, or all."),
                                          ("Max items:", self.max_items, 4, 1, 20, 1, "How many different items to suggest."),
                                          ("GE slots:", self.slots, 3, 1, 8, 1, "slots"),
                                          ("Fill % of hourly trades:", self.fill, 4, 1, 50, 1, "fill"),
                                          ("Max % of cash per item:", self.cap, 4, 5, 100, 5, "cap")):
            lab(self.f2, t, g)
            if var is self.mem:
                ttk.Combobox(self.f2, textvariable=var, values=("All", "Members", "F2P"), width=8, state="readonly").pack(side="left")
            else:
                ttk.Spinbox(self.f2, from_=lo, to=hi, increment=inc, textvariable=var, width=w).pack(side="left")
        c = ttk.Checkbutton(self.f2, text="Skip bonds", variable=self.nobond); c.pack(side="left", padx=10)
        tip(c, "Old school bonds have special GE rules - usually best left out.", app)
        self.f3 = ttk.Frame(bot); self.f3.pack(fill="x", pady=4)
        ttk.Button(self.f3, text="Open item page", command=self._open).pack(side="left")
        ttk.Button(self.f3, text="Add all to watchlist", command=self.add_all).pack(side="left", padx=4)
        b = ttk.Button(self.f3, text="Log as flip…", command=self.log_flip); b.pack(side="left")
        tip(b, "Did this flip? Record it in My Flips (pre-filled).", app)
        self.disc = ttk.Label(self.f3, text=DISCLAIMER, style="Warn.TLabel", wraplength=700, justify="left")
        self.disc.pack(side="left", padx=12)
        cols = [("name", "Item", 180, str), ("buy", "Buy at", 80, gp), ("sell", "Sell at", 80, gp),
                ("qty", "How many", 70, gp), ("cost", "Cost", 70, short_gp), ("profit", "Expected profit", 105, profit_fmt),
                ("roi", "ROI", 60, pct), ("hold", "Time", 140, str), ("confidence", "Confidence", 80, str),
                ("why", "Why", 260, str), ("notes", "Watch out for", 260, str)]
        tips = {"buy": "buy_at", "sell": "Target sell price.", "qty": "qty", "cost": "cost", "profit": "net_profit",
                "roi": "roi", "hold": "hold", "confidence": "confidence", "why": "why", "notes": "notes"}
        self.table = SortTable(bot, cols, height=8, on_double=lambda r: app.open_item(r["id"]), tips=tips, app=app,
                               advanced=("notes",))
        self.table.pack(fill="both", expand=True)
        self.table.tv.bind("<<TreeviewSelect>>", lambda e: self._sel_info(), add="+")
        self.totals = ttk.Label(bot, text="Type how much gp you have and press 'Find flips for me'.", font=("Segoe UI", 10, "bold"))
        self.totals.pack(anchor="w", pady=2)
        self.info = ttk.Label(bot, text="", style="Muted.TLabel"); self.info.pack(anchor="w")
        self.retheme()

    def _toggle_more(self):
        if self.more.get(): self.f2.pack(fill="x", pady=(4, 0), before=self.f3)
        else: self.f2.pack_forget()

    def set_advanced(self, on):
        self.table.show_advanced(on)
        if on and not self.more.get(): self.more.set(True); self._toggle_more()

    def apply_settings(self, F):
        self.fill.set(str(F["fill_pct"])); self.cap.set(str(F["cap_pct"])); self.slots.set(F["slots"])
        self.max_items.set(F["max_items"]); self.nobond.set(F["exclude_bonds"]); self.mem.set(F["members"])

    def retheme(self):
        P = self.app.pal
        ttk.Style(self).configure("Warn.TLabel", foreground=P["accent"])
        theme.tag_trees(self, P)

    def refresh_now(self):
        app = self.app
        st = core.ticker_settings(app.store.data)
        fl = core.best_flips(app.items, app.latest, app.vol, settings=st)
        self.t_now.set_rows(fl, "margin")
        self.now_lbl.config(text=f"  {len(fl)} items · ranked by {'ROI' if st.get('rank_by') == 'roi' else 'profit per limit'}"
                                 f" · only busy items with fresh prices")

    def _sel_info(self):
        r = self.table.selected()
        if r: self.info.config(text=f"{r['name']}: {r['strategy']} - {r['why']}. Watch out for: {r.get('notes') or '-'}. "
                                    f"Buy limit {gp(r.get('limit'))}, traded last hour {short_gp(r['vol'])}.")

    # ---------- generate ----------
    def _params(self):
        cash = sg.parse_gp(self.cash.get())
        fill = min(max(float(self.fill.get()) / 100, 0.01), 0.5)
        cap = float(self.cap.get()) / 100
        risk = self.risk.get()
        F = self.app.store.data.setdefault("finder", {})
        F.update(cash=self.cash.get().strip(), risk=risk, strategy=self.strat.get(), members=self.mem.get(),
                 max_items=int(self.max_items.get()), slots=int(self.slots.get()), fill_pct=float(self.fill.get()),
                 cap_pct=float(self.cap.get()), exclude_bonds=bool(self.nobond.get()))
        self.app.store.save()
        return dict(cash=cash, strategy=self.strat.get(), risk=risk, members=self.mem.get(), exclude_bonds=self.nobond.get(),
                    fill_frac=fill, slots=int(self.slots.get()), max_items=int(self.max_items.get()),
                    max_share=(0 if risk == "High" else cap))

    def generate(self, on_done=None):
        if self._busy: return
        if not self.app.items or not self.app.latest:
            self.totals.config(text="Still loading prices… try again in a moment."); return
        try: p = self._params()
        except (ValueError, tk.TclError):
            messagebox.showerror("Flip Finder", "Please type your cash like 10m, 500k or 2,500,000 "
                                                "(and whole numbers in the other boxes).", parent=self); return
        self._busy = True; self.gen_btn.state(["disabled"]); self.totals.config(text="Looking for flips…")
        items, latest = self.app.items, self.app.latest
        def work():
            h1 = self.api.hourly_now()                                   # shared 5-min cached bulk /1h
            old = self.api.hourly_at(time.time() - 25 * 3600)            # cached historical bucket
            daily = {}
            if p["strategy"] != "Quick flips":
                c = sg.base_candidates(items, latest, h1, old, p["cash"], p["risk"], None, p["members"], p["exclude_bonds"])
                short = sg.shortlist(c, p["strategy"])
                for i, x in enumerate(short):
                    k = x["id"]
                    if k not in self.daily or time.time() - self.daily[k][0] > TS_MAX_AGE:
                        self.after(0, lambda i=i, n=len(short): self.totals.config(text=f"Checking price history {i+1} of {n}…"))
                        try:
                            ts = self.api.timeseries(k, "24h", max_age=TS_MAX_AGE)
                            self.daily[k] = (time.time(), sg.daily_stats(ts))
                        except Exception:
                            continue
                        time.sleep(TS_GAP)
                    daily[k] = self.daily[k][1]
                daily = {k: v for k, v in daily.items() if v}
            return sg.suggest(items, latest, h1, old, daily=daily, **p)
        def done(res):
            self._busy = False; self.gen_btn.state(["!disabled"]); self.show(res, p)
            if on_done: on_done()
        def fail(e):
            self._busy = False; self.gen_btn.state(["!disabled"]); self.totals.config(text=core.friendly_error(e))
        self.app.bg(work, done, "Finding flips", on_error=fail)

    def show(self, res, p):
        rows, tot = res; self.rows = rows
        self.table.set_rows(rows, "profit")
        if not rows:
            self.totals.config(text="Nothing safe enough found - try a higher risk, another style or more gp."); return
        self.totals.config(text=(f"{tot['n']} items · uses {short_gp(tot['used'])} of your {short_gp(tot['cash'])} "
                                 f"({tot['used'] / tot['cash']:.0%}) · expected profit {short_gp(tot['profit_lo'])} to "
                                 f"{short_gp(tot['profit_hi'])} after tax · {p['risk']} risk, {p['strategy']} · "
                                 f"{datetime.datetime.now():%H:%M}"))

    # ---------- actions ----------
    def _need(self):
        r = self.table.selected()
        if not r: self.app.status("Select a suggestion in the list first.")
        return r

    def _open(self):
        r = self._need()
        if r: self.app.open_item(r["id"])

    def add_all(self):
        for r in self.rows: self.app.store.add(r["id"])
        self.app.on_watch_changed(); self.app.status(f"Added {len(self.rows)} suggested items to your watchlist")

    def log_flip(self):
        r = self._need()
        if r: self.app.log_flip(r["id"], qty=r["qty"], buy=r["buy"], sell=r["sell"])


SuggestionsTab = FlipFinderPage     # v0.6 name
