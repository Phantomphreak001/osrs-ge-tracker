"""Home page (v0.7): quick search, top 5 flips as cards, watchlist snapshot, alerts, today's flip profit."""
import time, datetime, tkinter as tk
from tkinter import ttk
import ge_core as core
from analytics_tab import SortTable, gp, short_gp, pct, profit_fmt
from ui_common import SearchBox, tip

N_CARDS = 5


class FlipCard(ttk.Frame):
    def __init__(self, master, app):
        super().__init__(master, style="Card.TFrame", padding=10, cursor="hand2")
        self.app, self.iid = app, None
        r1 = ttk.Frame(self, style="CardInner.TFrame"); r1.pack(fill="x")
        self.icon = ttk.Label(r1, style="Card.TLabel"); self.icon.pack(side="left", padx=(0, 6))
        self.name = ttk.Label(r1, text="…", style="CardTitle.TLabel", wraplength=170); self.name.pack(side="left")
        self.prices = ttk.Label(self, text="", style="Card.TLabel"); self.prices.pack(anchor="w", pady=(6, 0))
        self.profit = ttk.Label(self, text="", style="CardPos.TLabel"); self.profit.pack(anchor="w")
        self.extra = ttk.Label(self, text="", style="CardMuted.TLabel"); self.extra.pack(anchor="w")
        self._parts = (self, r1, self.icon, self.name, self.prices, self.profit, self.extra)
        for w in self._parts:
            w.bind("<Button-1>", lambda e: self.iid and app.open_item(self.iid))
            w.bind("<Button-3>", lambda e: self.iid and app.item_menu(self.iid, e))
            w.bind("<Enter>", lambda e: self._hover(True)); w.bind("<Leave>", lambda e: self._hover(False))
        tip(self, "Click to open this item. Profit each is after the 2% GE tax.", app)

    def _hover(self, on):
        sfx = "H" if on else ""
        self.configure(style="CardHover.TFrame" if on else "Card.TFrame")
        self._parts[1].configure(style="CardInnerH.TFrame" if on else "CardInner.TFrame")
        for w, base in ((self.icon, "Card"), (self.name, "CardTitle"), (self.prices, "Card"),
                        (self.profit, "CardPos"), (self.extra, "CardMuted")):
            w.configure(style=f"{base}{sfx}.TLabel")

    def set(self, rank, r):
        self.iid = r["id"]; it = self.app.items.get(r["id"])
        self.name.config(text=f"{rank}. {r['name']}")
        self.prices.config(text=f"Buy at {gp(r['low'])}\nSell at {gp(r['high'])}")
        self.profit.config(text=f"{core.signed_gp(r['margin'])} each ({r['roi']:.1f}%)")
        self.extra.config(text=(f"≈{short_gp(r['profit_limit'])} per limit of {gp(r['limit'])}" if r["profit_limit"]
                                else f"{short_gp(r['vol'])} traded last hour"))
        img = self.app.icons.get(it, lambda im, iid=r["id"]: im and self.iid == iid and self.icon.config(image=im))
        self.icon.config(image=img or "")

    def clear(self, text="Waiting for prices…"):
        self.iid = None; self.name.config(text=text)
        for w in (self.prices, self.profit, self.extra): w.config(text="")
        self.icon.config(image="")


class HomePage(ttk.Frame):
    def __init__(self, master, app):
        super().__init__(master, padding=12)
        self.app = app
        hd = ttk.Frame(self); hd.pack(fill="x")
        ttk.Label(hd, text="Grand Exchange at a glance", style="Big.TLabel").pack(side="left")
        self.updated = ttk.Label(hd, text="", style="Muted.TLabel"); self.updated.pack(side="right")
        sr = ttk.Frame(self); sr.pack(fill="x", pady=(8, 12))
        ttk.Label(sr, text="🔍  Look up an item:", font=("Segoe UI", 11)).pack(side="left")
        self.search = SearchBox(sr, app, app.open_item, width=50, placeholder="e.g. abyssal whip, death rune…",
                                font=("Segoe UI", 12))
        self.search.pack(side="left", padx=8)
        ttk.Label(sr, text="(Ctrl+F from anywhere)", style="Muted.TLabel").pack(side="left")
        fh = ttk.Frame(self); fh.pack(fill="x")
        ttk.Label(fh, text="Top 5 flips right now", style="H2.TLabel").pack(side="left")
        lk = ttk.Label(fh, text="See more in Flip Finder →", style="Link.TLabel", cursor="hand2"); lk.pack(side="right")
        lk.bind("<Button-1>", lambda e: app.goto("finder"))
        cards = ttk.Frame(self); cards.pack(fill="x", pady=(4, 12))
        self.cards = []
        for i in range(N_CARDS):
            c = FlipCard(cards, app); c.grid(row=0, column=i, sticky="nsew", padx=4); cards.columnconfigure(i, weight=1, uniform="c")
            c.clear(); self.cards.append(c)
        low = ttk.Frame(self); low.pack(fill="both", expand=True)
        left = ttk.Frame(low); left.pack(side="left", fill="both", expand=True)
        wh = ttk.Frame(left); wh.pack(fill="x")
        ttk.Label(wh, text="Your watchlist", style="H2.TLabel").pack(side="left")
        lk = ttk.Label(wh, text="Open watchlist →", style="Link.TLabel", cursor="hand2"); lk.pack(side="right")
        lk.bind("<Button-1>", lambda e: app.goto("watchlist"))
        self.t_watch = SortTable(left, [("name", "Item", 200, str), ("low", "Buy at", 90, gp), ("high", "Sell at", 90, gp),
                                        ("margin", "Profit each", 90, profit_fmt), ("roi", "ROI", 60, pct)],
                                 height=9, on_double=lambda r: app.open_item(r["id"]), app=app,
                                 tips={"low": "buy_at", "high": "sell_at", "margin": "profit_each", "roi": "roi"})
        self.t_watch.pack(fill="both", expand=True)
        self.w_empty = ttk.Label(self.t_watch, text="Nothing here yet - look up an item and press\n'Add to watchlist' on its page.",
                                 style="Muted.TLabel", justify="center")
        right = ttk.Frame(low, width=380); right.pack(side="left", fill="y", padx=(12, 0))
        pc = ttk.Frame(right, style="Card.TFrame", padding=12); pc.pack(fill="x")
        ttk.Label(pc, text="Today's flip profit", style="CardMuted.TLabel").pack(anchor="w")
        self.today = ttk.Label(pc, text="0", style="CardBig.TLabel"); self.today.pack(anchor="w")
        self.today_sub = ttk.Label(pc, text="", style="CardMuted.TLabel"); self.today_sub.pack(anchor="w")
        b = ttk.Button(pc, text="Log a flip…", command=lambda: app.goto("flips")); b.pack(anchor="w", pady=(6, 0))
        ac = ttk.Frame(right, style="Card.TFrame", padding=12); ac.pack(fill="both", expand=True, pady=(10, 0))
        ttk.Label(ac, text="Price alerts", style="CardMuted.TLabel").pack(anchor="w")
        self.alerts = ttk.Label(ac, text="", style="Card.TLabel", justify="left", wraplength=330); self.alerts.pack(anchor="w", fill="x")

    def refresh(self):
        app = self.app
        if app.latest:
            fl = core.best_flips(app.items, app.latest, app.vol, settings=core.ticker_settings(app.store.data))[:N_CARDS]
            for i, c in enumerate(self.cards):
                c.set(i + 1, fl[i]) if i < len(fl) else c.clear("No more flips pass the filters")
        rows = app.watch_page.rows() if app.items else []
        self.t_watch.set_rows(rows, "margin")
        if app.store.data["watch"]: self.w_empty.place_forget()
        else: self.w_empty.place(relx=.5, rely=.5, anchor="center")
        self.refresh_profit(); self.refresh_alerts()

    def refresh_profit(self):
        td, n = self.app.flog.today()
        self.today.config(text=core.signed_gp(td) + " gp" if n else "0 gp",
                          style="CardBigPos.TLabel" if td > 0 else "CardBigNeg.TLabel" if td < 0 else "CardBig.TLabel")
        self.today_sub.config(text=f"{n} flips today · all time {core.signed_gp(self.app.flog.total())}")

    def refresh_alerts(self):
        d = self.app.store.data
        log = list(reversed(d.get("alert_log", [])))[:6]
        nm = lambda i: self.app.items.get(i, {}).get("name", str(i))
        lines = []
        for a in log:
            when = datetime.datetime.fromtimestamp(a["ts"]).strftime("%a %H:%M")
            lines.append(f"🔔 {when}  {nm(a['id'])} went {'above' if a['kind'] == 'above' else 'below'} "
                         f"{short_gp(a['target'])} (was {short_gp(a['price'])})")
        armed = [(int(k), v) for k, v in d["alerts"].items() if (v.get("above") or v.get("below"))]
        waiting = [nm(i) for i, v in armed if len(v.get("fired", [])) < bool(v.get("above")) + bool(v.get("below"))]
        if waiting: lines.append(f"Watching {len(waiting)} alert(s): " + ", ".join(waiting[:5]) + ("…" if len(waiting) > 5 else ""))
        if not lines: lines = ["No alerts set. Open an item and press 'Set alert…' to get a popup when it hits your price."]
        self.alerts.config(text="\n".join(lines))
