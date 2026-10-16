"""Shared UI helpers (v0.7): plain-language glossary, hover tooltips, item search box, cached item icons."""
import os, threading, base64, urllib.request, tkinter as tk
from tkinter import ttk
import ge_core as core

# Plain-language explanations shown on hover (column headers, labels, settings).
GLOSSARY = {
    "buy_at": "Buy at: the price people are currently SELLING for (last instant-sell / 'low' price). "
              "Put a buy offer in around here.",
    "sell_at": "Sell at: the price people are currently BUYING for (last instant-buy / 'high' price). "
               "Put a sell offer in around here.",
    "profit_each": "Profit each (margin): Sell at - GE tax - Buy at. What one item earns after the 2% GE tax.",
    "roi": "ROI (return on investment): profit as a % of what you paid. 2% ROI = 2 gp back for every 100 gp spent.",
    "limit": "Buy limit: the most of this item the GE lets you buy every 4 hours.",
    "profit_limit": "Profit per limit: Profit each x Buy limit - roughly the most one 4-hour flip could make.",
    "vol": "Traded last hour: how many were bought + sold on the GE in the last hour. "
           "Higher = your offers fill faster.",
    "value": "gp traded: total value that changed hands in the last hour (price x quantity).",
    "updated": "When the most recent trade happened at these prices.",
    "alert": "Your price alerts for this item (≥ above / ≤ below).",
    "chg": "Price change compared with the same hour yesterday (mid price).",
    "chg24": "Price change over the last 24 hours.",
    "volatility": "Volatility (price swings): how much the price jumps around hour to hour. "
                  "Higher = riskier, prices can move against you.",
    "trend": "Profit trend: is the margin growing (↑) or shrinking (↓) over the last day?",
    "confidence": "Confidence: how reliable the pattern / estimate is. Low = treat it as a guess.",
    "mid": "Mid price: halfway between Buy at and Sell at.",
    "qty": "Quantity: how many to buy.",
    "cost": "Cost: gp needed to buy that quantity.",
    "net_profit": "Expected profit after GE tax if everything sells at the target.",
    "hold": "How long it may take to fill / how long to hold the item.",
    "notes": "Things that could go wrong with this item.",
    "why": "Why this item was picked.",
    "best_time": "Hours (US Eastern time) when this item has historically been cheapest / most expensive. "
                 "A tendency, not a guarantee.",
    "tax": "GE tax: 2% of the sale price (rounded down, max 5m per item). Some cheap items are exempt.",
    "fill": "Fill %: the share of the hourly trading volume you expect YOUR offers to get. Lower = safer estimate.",
    "cap": "Max % of your cash stack put into one item (spreads risk). Ignored on High risk.",
    "slots": "GE slots: how many offers you can have open at once (8 for members, 3 F2P).",
    "risk": "Risk: Low only picks busy, steady items; High allows rarer, jumpier items with bigger margins.",
    "strategy": "Quick flips = buy and resell within hours. Overnight = buy cheap dips and sell next day. "
                "Longer = hold for 1-4 weeks.",
    "cash": "How much gp you want to use. You can type 10m, 2.5k, 1b or 1,234.",
    "age": "Ignore items whose last trade is older than this (stale prices).",
    "min_vol": "Ignore items that traded fewer than this many in the last hour.",
    "max_roi": "Ignore items with a suspiciously huge ROI - usually stale or manipulated prices.",
}


class Tooltip:
    """Hover tooltip for any widget. text may be a string or a callable(event) -> str|None."""
    def __init__(self, widget, text, pal_fn=None, delay=450):
        self.w, self.text, self.pal_fn, self.delay = widget, text, pal_fn, delay
        self.tip = self._job = None; self._last = None
        widget.bind("<Enter>", self._sched, add="+"); widget.bind("<Leave>", self.hide, add="+")
        widget.bind("<Motion>", self._move, add="+"); widget.bind("<ButtonPress>", self.hide, add="+")

    def _txt(self, e):
        return self.text(e) if callable(self.text) else self.text

    def _sched(self, e):
        self.hide(); self._job = self.w.after(self.delay, lambda: self.show(e))

    def _move(self, e):
        if not callable(self.text): return
        t = self._txt(e)
        if t != self._last: self._sched(e)

    def show(self, e):
        t = self._txt(e); self._last = t
        if not t: return
        P = self.pal_fn() if self.pal_fn else {"tip_bg": "#ffffe1", "tip_fg": "#000000", "border": "#888888"}
        self.tip = tw = tk.Toplevel(self.w); tw.wm_overrideredirect(True)
        tw.wm_geometry(f"+{self.w.winfo_pointerx() + 14}+{self.w.winfo_pointery() + 16}")
        tk.Label(tw, text=t, justify="left", wraplength=360, bg=P["tip_bg"], fg=P["tip_fg"], relief="solid",
                 borderwidth=1, padx=6, pady=4, font=("Segoe UI", 9)).pack()

    def hide(self, e=None):
        if self._job:
            try: self.w.after_cancel(self._job)
            except Exception: pass
            self._job = None
        if self.tip:
            try: self.tip.destroy()
            except Exception: pass
            self.tip = None


def heading_tips(tv, tips, pal_fn=None):
    """Tooltips on Treeview column headings: tips = {column_id: text or GLOSSARY key}."""
    cols = list(tv["columns"])
    def txt(e):
        if tv.identify_region(e.x, e.y) != "heading": return None
        c = tv.identify_column(e.x)
        try: k = cols[int(c[1:]) - 1]
        except (ValueError, IndexError): return None
        t = tips.get(k)
        return GLOSSARY.get(t, t) if t else None
    return Tooltip(tv, txt, pal_fn)


def tip(widget, key_or_text, app=None):
    return Tooltip(widget, GLOSSARY.get(key_or_text, key_or_text), (lambda: app.pal) if app else None)


class SearchBox(ttk.Frame):
    """Item search with live suggestions; on_pick(item_id) is called on Enter / click."""
    def __init__(self, master, app, on_pick, width=40, placeholder="Search any item…", font=None):
        super().__init__(master)
        self.app, self.on_pick, self.placeholder = app, on_pick, placeholder
        self.var = tk.StringVar()
        self.entry = ttk.Entry(self, textvariable=self.var, width=width, font=font)
        self.entry.pack(fill="x", expand=True)
        self.lb = None; self._ph = False
        self.var.trace_add("write", lambda *_: self._update())
        self.entry.bind("<Return>", lambda e: self.pick())
        self.entry.bind("<Down>", lambda e: self._focus_list())
        self.entry.bind("<Escape>", lambda e: (self.var.set(""), self.close()))
        self.entry.bind("<FocusIn>", lambda e: self._clear_ph())
        self.entry.bind("<FocusOut>", lambda e: self.after(150, self._focus_out))
        self._set_ph()

    def _set_ph(self):
        if not self.var.get():
            self._ph = True; self.var.set(self.placeholder); self.entry.configure(foreground=self.app.pal["muted"])

    def _clear_ph(self):
        if self._ph:
            self._ph = False; self.var.set(""); self.entry.configure(foreground=self.app.pal["fg"])

    def _focus_out(self):
        if self.lb and self.focus_get() is self.lb: return
        self.close(); self._set_ph()

    def focus(self):
        self.entry.focus_set(); self._clear_ph(); self.entry.select_range(0, "end")

    def retheme(self):
        self.entry.configure(foreground=self.app.pal["muted"] if self._ph else self.app.pal["fg"])

    def matches(self, s, n=12):
        names = getattr(self.app, "names", None) or []
        s = s.strip().lower()
        if len(s) < 2: return []
        starts = [x for x in names if x.lower().startswith(s)]
        contains = [x for x in names if s in x.lower() and not x.lower().startswith(s)]
        return (starts + contains)[:n]

    def _update(self):
        if self._ph: return
        res = self.matches(self.var.get())
        if not res: self.close(); return
        top = self.winfo_toplevel(); P = self.app.pal
        if self.lb is None:
            self.lb = tk.Listbox(top, height=min(12, len(res)), bg=P["field"], fg=P["fg"], selectbackground=P["select"],
                                 selectforeground=P["select_fg"], highlightthickness=1, highlightbackground=P["accent"],
                                 activestyle="none", font=("Segoe UI", 10))
            self.lb.bind("<ButtonRelease-1>", lambda e: self.pick(from_list=True))
            self.lb.bind("<Return>", lambda e: self.pick(from_list=True))
            self.lb.bind("<Escape>", lambda e: (self.close(), self.entry.focus_set()))
            self.lb.bind("<FocusOut>", lambda e: self.after(150, self._focus_out))
        self.lb.delete(0, "end")
        for n in res: self.lb.insert("end", n)
        self.lb.configure(height=min(12, len(res)))
        x = self.entry.winfo_rootx() - top.winfo_rootx(); y = self.entry.winfo_rooty() - top.winfo_rooty() + self.entry.winfo_height()
        self.lb.place(x=x, y=y, width=max(self.entry.winfo_width(), 280)); self.lb.lift()

    def _focus_list(self):
        if self.lb and self.lb.size():
            self.lb.focus_set(); self.lb.selection_clear(0, "end"); self.lb.selection_set(0); self.lb.activate(0)

    def close(self):
        if self.lb: self.lb.place_forget()

    def pick(self, from_list=False):
        name = None
        if from_list and self.lb and self.lb.curselection(): name = self.lb.get(self.lb.curselection()[0])
        if name is None and not self._ph:
            q = self.var.get().strip().lower()
            if q in self.app.by_name: name = q
            else:
                m = self.matches(q, 1); name = m[0] if m else None
        if name is None:
            self.app.status("No item matches that search - try fewer letters.", kind="warn"); return
        iid = self.app.by_name.get(name.lower())
        self.close(); self.var.set(""); self.winfo_toplevel().focus_set(); self._set_ph(); self.on_pick(iid)


class IconCache:
    """OSRS Wiki item icons (mapping 'icon' field), cached on disk; loaded in a background thread.
    Failures are silent (icons are decoration only)."""
    def __init__(self, root, data_dir=None):
        self.root = root
        self.dir = os.path.join(data_dir or core.app_dir(), "icons"); os.makedirs(self.dir, exist_ok=True)
        self.imgs, self.pending, self.failed = {}, {}, set()
        self.enabled = True

    def _file(self, iid): return os.path.join(self.dir, f"{int(iid)}.png")

    def get(self, item, cb=None):
        """Return a PhotoImage now if available; otherwise start a fetch and call cb(img) later."""
        if not item or not self.enabled: return None
        iid = item["id"]
        if iid in self.imgs: return self.imgs[iid]
        if iid in self.failed: return None
        f = self._file(iid)
        if os.path.exists(f) and os.path.getsize(f) > 0:
            try:
                self.imgs[iid] = tk.PhotoImage(master=self.root, file=f); return self.imgs[iid]
            except Exception:
                self.failed.add(iid); return None
        if cb: self.pending.setdefault(iid, []).append(cb)
        if len(self.pending.get(iid, [])) <= 1:
            threading.Thread(target=self._fetch, args=(item,), daemon=True).start()
        return None

    def _fetch(self, item):
        iid, url = item["id"], core.icon_url(item.get("icon"))
        try:
            if not url: raise ValueError("no icon")
            req = urllib.request.Request(url, headers={"User-Agent": core.USER_AGENT})
            with urllib.request.urlopen(req, timeout=8) as r: data = r.read(200_000)
            if not data.startswith(b"\x89PNG"): raise ValueError("not png")
            tmp = self._file(iid) + ".tmp"
            with open(tmp, "wb") as fh: fh.write(data)
            os.replace(tmp, self._file(iid))
            self.root.after(0, lambda: self._done(iid, True))
        except Exception:
            try: self.root.after(0, lambda: self._done(iid, False))
            except Exception: pass

    def _done(self, iid, ok):
        cbs = self.pending.pop(iid, [])
        img = None
        if ok:
            try: img = self.imgs[iid] = tk.PhotoImage(master=self.root, file=self._file(iid))
            except Exception: self.failed.add(iid)
        else:
            self.failed.add(iid)
        for cb in cbs:
            try: cb(img)
            except Exception: pass
