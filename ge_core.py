"""Core logic: OSRS Wiki prices API client, caching, GE tax/margin math, storage.
Read-only public data. No game client interaction."""
import json, os, re, sys, time, threading, urllib.request, urllib.parse

API = "https://prices.runescape.wiki/api/v1/osrs"
USER_AGENT = "osrs-ge-tracker/0.7 (+https://github.com/Phantomphreak001/osrs-ge-tracker)"
MIN_LATEST_INTERVAL = 60          # seconds between /latest fetches, max
MAPPING_TTL = 24 * 3600           # re-download mapping once a day

# GE tax (OSRS Wiki, Grand Exchange page, checked 2026-10-15): 2% since 29 May 2025,
# rounded down (so <50gp => 0), capped at 5,000,000 per item; some items exempt.
TAX_RATE = 0.02
TAX_CAP = 5_000_000
TAX_EXEMPT = {
    "Old school bond", "Energy potion(4)", "Energy potion(3)", "Energy potion(2)", "Energy potion(1)",
    "Bronze arrow", "Bronze dart", "Iron arrow", "Iron dart", "Mind rune", "Steel arrow", "Steel dart",
    "Bass", "Bread", "Cake", "Cooked chicken", "Cooked meat", "Herring", "Lobster", "Mackerel",
    "Meat pie", "Pike", "Salmon", "Shrimps", "Tuna",
    "Ardougne teleport", "Camelot teleport", "Civitas illa fortis teleport", "Falador teleport",
    "Games necklace(8)", "Kourend castle teleport", "Lumbridge teleport", "Ring of dueling(8)",
    "Teleport to house", "Varrock teleport",
    "Chisel", "Gardening trowel", "Glassblowing pipe", "Hammer", "Needle", "Pestle and mortar",
    "Rake", "Saw", "Secateurs", "Seed dibber", "Shears", "Spade", "Watering can",
}


def ge_tax(price, name=None):
    if price is None or price <= 0 or (name and name in TAX_EXEMPT):
        return 0
    return min(int(price * TAX_RATE), TAX_CAP)   # int() floors for positive


def margin(high, low, name=None):
    """Flip: buy at instant-sell price (low), sell at instant-buy price (high), pay tax on sale.
    Returns (margin_gp, roi_pct) or (None, None)."""
    if not high or not low:
        return None, None
    m = high - ge_tax(high, name) - low
    return m, (m / low * 100.0)


def app_dir():
    base = os.environ.get("APPDATA") if sys.platform == "win32" else None
    base = base or os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
    d = os.path.join(base, "ge-tracker")
    os.makedirs(d, exist_ok=True)
    return d


class Api:
    def __init__(self, data_dir=None, opener=None):
        self.dir = data_dir or app_dir()
        self._open = opener or self._http
        self._latest, self._latest_t = {}, 0
        self._vol, self._vol_t = {}, 0
        self._h1, self._h1_old = {}, {}      # raw /1h now; {timestamp: /1h data} historical
        self._ts = {}                        # (id, step) -> (fetched_at, data)
        self._lock = threading.Lock()

    def _http(self, url):
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.loads(r.read().decode())

    def get(self, path, **params):
        url = f"{API}/{path}" + ("?" + urllib.parse.urlencode(params) if params else "")
        return self._open(url)

    def mapping(self):
        f = os.path.join(self.dir, "mapping.json")
        if os.path.exists(f) and time.time() - os.path.getmtime(f) < MAPPING_TTL:
            with open(f, encoding="utf-8") as fh:
                return json.load(fh)
        try:
            data = self.get("mapping")
            with open(f, "w", encoding="utf-8") as fh:
                json.dump(data, fh)
            return data
        except Exception:
            if os.path.exists(f):           # stale cache beats nothing
                with open(f, encoding="utf-8") as fh:
                    return json.load(fh)
            raise

    def latest(self, force=False):
        with self._lock:
            if force and time.time() - self._latest_t < MIN_LATEST_INTERVAL:
                force = False                # never faster than 60s
            if force or not self._latest_t:
                self._latest = self.get("latest")["data"]
                self._latest_t = time.time()
            return self._latest, self._latest_t

    def volumes_1h(self):
        with self._lock:
            if time.time() - self._vol_t >= 300:
                d = self.get("1h")["data"]
                self._h1 = d
                self._vol = {k: (v.get("highPriceVolume") or 0) + (v.get("lowPriceVolume") or 0)
                             for k, v in d.items()}
                self._vol_t = time.time()
            return self._vol

    def hourly_now(self):
        """Raw /1h data (shares the 5-min cache with volumes_1h)."""
        self.volumes_1h()
        return self._h1

    def hourly_at(self, ts):
        """/1h?timestamp=ts (historical, immutable -> cached forever, in memory; keep last 4)."""
        ts = int(ts) // 3600 * 3600
        with self._lock:
            if ts not in self._h1_old:
                self._h1_old[ts] = self.get("1h", timestamp=ts)["data"]
                for k in sorted(self._h1_old)[:-4]:
                    del self._h1_old[k]
            return self._h1_old[ts]

    def timeseries(self, item_id, timestep="1h", max_age=None):
        """Cached per (id, step); refetch only after max_age (default: 5m step 60s, else 5 min)."""
        max_age = (60 if timestep == "5m" else 300) if max_age is None else max_age
        k = (int(item_id), timestep)
        hit = self._ts.get(k)
        if hit and time.time() - hit[0] < max_age:
            return hit[1]
        d = self.get("timeseries", timestep=timestep, id=item_id)["data"]
        self._ts[k] = (time.time(), d)
        return d


# ---------- best-flips ranking (used by the ticker) ----------
TICKER_DEFAULTS = {
    "max_age_min": 45,      # both last instant-buy AND instant-sell trade must be this recent
    "min_vol_1h": 100,      # combined high+low volume over the last 1h
    "min_margin": 1,        # gp after tax
    "max_roi": 50.0,        # % - drops stale/manipulated spreads
    "min_price": 1,         # low price floor (gp)
    "top_n": 18,
    "rank_by": "profit",    # "profit" (margin x min(limit, vol)) or "roi"
}


def ticker_settings(data):
    s = dict(TICKER_DEFAULTS); s.update((data or {}).get("ticker") or {})
    return s


def best_flips(items, latest, vol, now=None, settings=None):
    """Rank flip candidates across all items.
    items: {id: mapping dict}, latest: /latest data (str ids), vol: {str id: 1h combined volume}.
    score = margin_after_tax * min(buy_limit, vol_1h)   (limit unknown => vol_1h).
    Returns list of dicts sorted by score desc, at most top_n."""
    st = dict(TICKER_DEFAULTS); st.update(settings or {})
    now = time.time() if now is None else now
    max_age = st["max_age_min"] * 60
    out = []
    for sid, p in latest.items():
        try: iid = int(sid)
        except ValueError: continue
        it = items.get(iid)
        hi, lo = p.get("high"), p.get("low")
        if not it or not hi or not lo or lo < st["min_price"]:
            continue
        if now - (p.get("highTime") or 0) > max_age or now - (p.get("lowTime") or 0) > max_age:
            continue
        v = vol.get(sid) or 0
        if v < st["min_vol_1h"]:
            continue
        m, roi = margin(hi, lo, it["name"])
        if m is None or m < st["min_margin"] or roi > st["max_roi"]:
            continue
        lim = it.get("limit")
        qty = min(lim, v) if lim else v
        out.append({"id": iid, "name": it["name"], "high": hi, "low": lo, "margin": m, "roi": roi,
                    "limit": lim, "vol": v, "profit_limit": m * lim if lim else None, "score": m * qty})
    key = (lambda r: (r["roi"], r["score"])) if st.get("rank_by") == "roi" else (lambda r: r["score"])
    out.sort(key=key, reverse=True)
    return out[:int(st["top_n"])]


SETTINGS_VERSION = 2
UI_DEFAULTS = {"geometry": "1400x860", "tab": "home", "advanced": False, "tips_seen": False}
FINDER_DEFAULTS = {"cash": "10m", "risk": "Medium", "strategy": "Quick flips", "members": "All", "max_items": 8,
                   "slots": 8, "fill_pct": 15, "cap_pct": 30, "exclude_bonds": True}
MARKET_UI_DEFAULTS = {"min_vol": 500}
TIMING_UI_DEFAULTS = {"window": 2, "night_start": 18, "night_end": 6}


def migrate_settings(raw):
    """Bring any watchlist.json content (v0.1-v0.6 = version 1, or v0.7 = 2) up to the current schema.
    Never drops user data: watch ids are coerced to unique ints, alerts kept, unknown keys preserved,
    sub-dicts get missing defaults filled in."""
    d = dict(raw) if isinstance(raw, dict) else {}
    watch, seen = [], set()
    for x in d.get("watch") or []:
        try: i = int(x)
        except (TypeError, ValueError): continue
        if i not in seen: seen.add(i); watch.append(i)
    d["watch"] = watch
    alerts = {}
    for k, a in (d.get("alerts") or {}).items():
        if isinstance(a, dict):
            a = dict(a); a.setdefault("above", None); a.setdefault("below", None); a.setdefault("fired", [])
            alerts[str(k)] = a
    d["alerts"] = alerts
    d.setdefault("auto_refresh", True); d.setdefault("show_ticker", True)
    d["theme"] = d.get("theme") if d.get("theme") in ("light", "dark") else "dark"
    for key, dflt in (("ticker", TICKER_DEFAULTS), ("ui", UI_DEFAULTS), ("finder", FINDER_DEFAULTS),
                      ("market", MARKET_UI_DEFAULTS), ("timing", TIMING_UI_DEFAULTS)):
        sub = dict(dflt); sub.update(d.get(key) if isinstance(d.get(key), dict) else {})
        d[key] = sub
    d.setdefault("alert_log", [])
    d["settings_version"] = SETTINGS_VERSION
    return d


def fit_geometry(geom, sw, sh):
    """Saved 'WxH+X+Y' clamped to the current screen (monitor may have changed); falls back to the default size."""
    m = re.match(r"^(\d+)x(\d+)(?:([+-]-?\d+)([+-]-?\d+))?$", str(geom or ""))
    if not m: m = re.match(r"^(\d+)x(\d+)$", UI_DEFAULTS["geometry"])
    w, h = min(int(m.group(1)), sw - 20), min(int(m.group(2)), sh - 80)
    w, h = max(w, min(1000, sw)), max(h, min(640, sh - 80))
    out = f"{w}x{h}"
    if m.lastindex and m.lastindex >= 4 and m.group(3):
        x, y = int(m.group(3)), int(m.group(4))
        if 0 <= x <= sw - 200 and 0 <= y <= sh - 200: out += f"+{x}+{y}"
    return out


class Store:
    """Watchlist + alerts in a JSON file in the app data folder."""
    def __init__(self, data_dir=None):
        self.path = os.path.join(data_dir or app_dir(), "watchlist.json")
        raw = None
        if os.path.exists(self.path):
            try:
                with open(self.path, encoding="utf-8") as fh:
                    raw = json.load(fh)
            except Exception:
                raw = None
        if isinstance(raw, dict) and raw.get("settings_version", 1) < SETTINGS_VERSION:
            try:                              # one-time backup of the pre-migration file
                bak = self.path + f".v{raw.get('settings_version', 1)}.bak"
                if not os.path.exists(bak):
                    with open(bak, "w", encoding="utf-8") as fh: json.dump(raw, fh, indent=1)
            except Exception:
                pass
        self.data = migrate_settings(raw)
        if isinstance(raw, dict) and raw != self.data:
            try: self.save()
            except Exception: pass

    def save(self):
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(self.data, fh, indent=1)
        os.replace(tmp, self.path)

    def add(self, item_id):
        if item_id not in self.data["watch"]:
            self.data["watch"].append(item_id); self.save()

    def remove(self, item_id):
        if item_id in self.data["watch"]:
            self.data["watch"].remove(item_id)
        self.data["alerts"].pop(str(item_id), None); self.save()

    def log_alert(self, item_id, kind, price, target, ts=None):
        log = self.data.setdefault("alert_log", [])
        log.append({"ts": ts or time.time(), "id": int(item_id), "kind": kind, "price": price, "target": target})
        del log[:-30]

    def set_alert(self, item_id, above=None, below=None):
        self.data["alerts"][str(item_id)] = {"above": above, "below": below, "fired": []}
        self.save()


def check_alerts(alerts, latest):
    """Return list of (item_id, kind, price, target). Marks fired so each triggers once."""
    hits = []
    for sid, a in alerts.items():
        p = latest.get(sid)
        if not p:
            continue
        fired = a.setdefault("fired", [])
        if a.get("above") and p.get("high") and p["high"] >= a["above"] and "above" not in fired:
            hits.append((int(sid), "above", p["high"], a["above"])); fired.append("above")
        if a.get("below") and p.get("low") and p["low"] <= a["below"] and "below" not in fired:
            hits.append((int(sid), "below", p["low"], a["below"])); fired.append("below")
    return hits


def fmt_gp(v):
    """Exact gp with thousands separators (used for prices you type into the GE)."""
    if v is None: return "-"
    return f"{v:,.0f}" if isinstance(v, float) else f"{v:,}"


def short_gp(v):
    """Compact gp the way OSRS players write it: 950, 1.2k, 45.3k, 796k, 3.4m, 1.25b (sign kept)."""
    if v is None: return "-"
    a, sign = abs(v), "-" if v < 0 else ""
    if round(a) < 1000: return f"{sign}{a:,.0f}"
    units = ((1e3, "k", 1), (1e6, "m", 1), (1e9, "b", 2))
    for i, (d, suf, dec) in enumerate(units):
        x = a / d
        txt = f"{x:.{dec}f}" if x < 100 else f"{x:.0f}"
        if float(txt) >= 1000 and i + 1 < len(units): continue      # 999,999 -> 1m, not 1000k
        if "." in txt: txt = txt.rstrip("0").rstrip(".")
        return f"{sign}{txt}{suf}"


def signed_gp(v, short=True):
    if v is None: return "-"
    return ("+" if v > 0 else "") + (short_gp(v) if short else fmt_gp(v))


def friendly_error(e):
    """Turn network/API exceptions into a plain sentence for the status bar."""
    import socket, urllib.error
    if isinstance(e, urllib.error.HTTPError):
        if e.code == 429: return "The price server asked us to slow down - will retry shortly."
        return f"The OSRS Wiki price server returned an error ({e.code}). Will retry automatically."
    if isinstance(e, (urllib.error.URLError, socket.timeout, TimeoutError, ConnectionError, OSError)):
        return "Can't reach the OSRS Wiki price server - check your internet connection. Will retry automatically."
    return f"Something went wrong: {e}"


def icon_url(icon_name):
    if not icon_name: return None
    return "https://oldschool.runescape.wiki/images/" + urllib.parse.quote(icon_name.replace(" ", "_"))


def ago(seconds):
    s = max(0, int(seconds))
    if s < 60: return f"{s}s ago"
    if s < 3600: return f"{s // 60}m ago"
    return f"{s // 3600}h {s % 3600 // 60}m ago"


# ---------- analytics math ----------
def mid(high, low):
    if high and low: return (high + low) / 2
    return high or low or None


def pct_change(old, new):
    if not old or new is None: return None
    return (new - old) / old * 100.0


def h1_mid(p):
    return mid((p or {}).get("avgHighPrice"), (p or {}).get("avgLowPrice"))


def h1_vol(p):
    return ((p or {}).get("highPriceVolume") or 0) + ((p or {}).get("lowPriceVolume") or 0)


MOVER_DEFAULTS = {"min_vol": 500, "min_price": 100, "min_value": 1_000_000, "top_n": 25}


def market_movers(items, now_h1, old_h1, settings=None):
    """Compare /1h average mid now vs the /1h bucket ~24h earlier.
    Filters: both buckets have prices, 1h volume >= min_vol in BOTH, mid >= min_price,
    gp traded in current hour >= min_value. Returns (gainers, losers)."""
    st = dict(MOVER_DEFAULTS); st.update(settings or {})
    rows = []
    for sid, p in now_h1.items():
        try: iid = int(sid)
        except ValueError: continue
        it, q = items.get(iid), old_h1.get(sid)
        if not it or not q: continue
        m1, m0 = h1_mid(p), h1_mid(q)
        v1, v0 = h1_vol(p), h1_vol(q)
        if not m1 or not m0 or m1 < st["min_price"]: continue
        if min(v1, v0) < st["min_vol"] or m1 * v1 < st["min_value"]: continue
        rows.append({"id": iid, "name": it["name"], "old": m0, "now": m1,
                     "chg": pct_change(m0, m1), "vol": v1, "value": m1 * v1})
    n = int(st["top_n"])
    g = sorted([r for r in rows if r["chg"] > 0], key=lambda r: -r["chg"])[:n]
    l = sorted([r for r in rows if r["chg"] < 0], key=lambda r: r["chg"])[:n]
    return g, l


def most_traded(items, now_h1, top_n=25):
    """(by_volume, by_gp_value) over the last 1h."""
    rows = []
    for sid, p in now_h1.items():
        try: iid = int(sid)
        except ValueError: continue
        it, m, v = items.get(iid), h1_mid(p), h1_vol(p)
        if not it or not m or not v: continue
        rows.append({"id": iid, "name": it["name"], "mid": m, "vol": v, "value": m * v})
    return (sorted(rows, key=lambda r: -r["vol"])[:top_n], sorted(rows, key=lambda r: -r["value"])[:top_n])


def series_mids(ts):
    return [mid(x.get("avgHighPrice"), x.get("avgLowPrice")) for x in ts]


def volatility(prices):
    """Std-dev of step-to-step % returns (population). None if < 3 prices."""
    p = [x for x in prices if x]
    r = [(b - a) / a * 100.0 for a, b in zip(p, p[1:])]
    if len(r) < 2: return None
    m = sum(r) / len(r)
    return (sum((x - m) ** 2 for x in r) / len(r)) ** 0.5


def margin_series(ts, name=None):
    return [margin(x.get("avgHighPrice"), x.get("avgLowPrice"), name)[0] for x in ts]


def trend(values, k=6):
    """Compare mean of last k vs first k non-null values. Returns (delta, arrow)."""
    v = [x for x in values if x is not None]
    if len(v) < 2: return None, "?"
    k = max(1, min(k, len(v) // 2))
    a, b = sum(v[:k]) / k, sum(v[-k:]) / k
    d = b - a
    tol = max(abs(a) * 0.05, 1)
    return d, ("↑" if d > tol else "↓" if d < -tol else "→")


def watch_summary(ts_1h_24, now_ts=None, name=None):
    """From last ~24 1h buckets: change %, volatility, margin trend."""
    d = ts_1h_24[-25:]
    mids = [x for x in series_mids(d) if x]
    chg = pct_change(mids[0], mids[-1]) if len(mids) >= 2 else None
    md, arrow = trend(margin_series(d, name))
    return {"chg": chg, "vol": volatility(mids), "margin_trend": md, "arrow": arrow}


def sma(values, n):
    out, win = [], []
    for v in values:
        win.append(v)
        if len(win) > n: win.pop(0)
        good = [x for x in win if x is not None]
        out.append(sum(good) / len(good) if len(win) == n and good else None)
    return out


def series_stats(prices):
    p = [x for x in prices if x]
    if not p: return None
    return {"min": min(p), "max": max(p), "avg": sum(p) / len(p), "chg": pct_change(p[0], p[-1])}


def flip_profit(qty, buy, sell, name=None):
    """Realized profit after GE tax (tax charged per item on the sale)."""
    return (sell - ge_tax(sell, name) - buy) * qty


def held(seconds):
    """How long an open flip has been held: 45m, 3h 12m, 2d 4h."""
    s = max(0, int(seconds or 0))
    if s < 3600: return f"{s // 60}m"
    if s < 86400: return f"{s // 3600}h {s % 3600 // 60}m"
    return f"{s // 86400}d {s % 86400 // 3600}h"


def _parse_json_file(path):
    """(ok, data). ok=False means the file exists but could not be parsed."""
    try:
        with open(path, encoding="utf-8") as fh: return True, json.load(fh)
    except FileNotFoundError:
        return True, None
    except Exception:
        return False, None


def _keep_copy(path, suffix):
    """Copy a data file aside once (never overwrites an earlier copy). Failures are ignored."""
    try:
        bak = path + suffix
        if os.path.exists(path) and not os.path.exists(bak):
            import shutil; shutil.copy2(path, bak)
        return bak
    except Exception:
        return None


OPEN_FLIPS_VERSION = 1


class FlipLog:
    """Realized flips in flips.json (a plain JSON list - same format as v0.1-v0.7, so older versions
    can still read it) plus open flips (bought, not sold yet) in open_flips.json next to it (v0.8)."""
    def __init__(self, data_dir=None):
        d = data_dir or app_dir()
        self.path = os.path.join(d, "flips.json")
        self.open_path = os.path.join(d, "open_flips.json")
        self.flips, self.open = [], []
        migrate = False
        ok, raw = _parse_json_file(self.path)
        if not ok:
            # Corrupt file: keep it aside so nothing is lost when we save again.
            _keep_copy(self.path, time.strftime(".corrupt-%Y%m%d-%H%M%S.bak"))
        elif isinstance(raw, list):
            self.flips = [f for f in raw if isinstance(f, dict)]
        elif isinstance(raw, dict):           # tolerate a {"flips": [...], "open": [...]} style file
            self.flips = [f for f in raw.get("flips") or [] if isinstance(f, dict)]
            self.open = [self._norm_open(o) for o in raw.get("open") or [] if isinstance(o, dict)]
            _keep_copy(self.path, ".pre-v0.8.bak")
            migrate = True
        ok, raw = _parse_json_file(self.open_path)
        if not ok:
            _keep_copy(self.open_path, time.strftime(".corrupt-%Y%m%d-%H%M%S.bak"))
        elif raw is not None:
            lst = raw.get("open") if isinstance(raw, dict) else raw
            self.open += [self._norm_open(o) for o in lst or [] if isinstance(o, dict)]
        if migrate:      # write open flips first, then flips.json back as the plain list older versions read
            try: self.save_open(); self.save()
            except Exception: pass

    @staticmethod
    def _norm_open(o):
        o = dict(o)
        o.setdefault("oid", os.urandom(6).hex()); o.setdefault("ts", time.time()); o.setdefault("note", "")
        o["qty"], o["buy"] = int(o.get("qty") or 0), int(o.get("buy") or 0)
        return o

    @staticmethod
    def _write(path, data):
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh: json.dump(data, fh, indent=1)
        os.replace(tmp, path)

    def save(self):
        self._write(self.path, self.flips)

    def save_open(self):
        self._write(self.open_path, {"version": OPEN_FLIPS_VERSION, "open": self.open})

    def add(self, item_id, name, qty, buy, sell, ts=None, **extra):
        qty, buy, sell = int(qty), int(buy), int(sell)
        if qty <= 0 or buy < 0 or sell < 0: raise ValueError("qty must be > 0, prices >= 0")
        f = {"ts": ts or time.time(), "id": item_id, "name": name, "qty": qty, "buy": buy, "sell": sell,
             "tax": ge_tax(sell, name) * qty, "profit": flip_profit(qty, buy, sell, name)}
        f.update({k: v for k, v in extra.items() if v not in (None, "")})
        self.flips.append(f); self.save(); return f

    def delete(self, idx):
        del self.flips[idx]; self.save()

    # ---------- open flips (v0.8): log the buy now, the sale later ----------
    def get_open(self, oid):
        for o in self.open:
            if o["oid"] == oid: return o
        raise KeyError(oid)

    def add_open(self, item_id, name, qty, buy, note="", ts=None):
        qty, buy = int(qty), int(buy)
        if qty <= 0 or buy < 0: raise ValueError("qty must be > 0, price >= 0")
        o = {"oid": os.urandom(6).hex(), "ts": ts or time.time(), "id": item_id, "name": name,
             "qty": qty, "buy": buy, "note": (note or "").strip()}
        self.open.append(o); self.save_open(); return o

    def edit_open(self, oid, **kw):
        o = self.get_open(oid); new = dict(o)
        for k in ("id", "name", "note", "ts"):
            if k in kw and kw[k] is not None: new[k] = kw[k].strip() if k == "note" else kw[k]
        if kw.get("qty") is not None: new["qty"] = int(kw["qty"])
        if kw.get("buy") is not None: new["buy"] = int(kw["buy"])
        if new["qty"] <= 0 or new["buy"] < 0: raise ValueError("qty must be > 0, price >= 0")
        o.update(new); self.save_open(); return o

    def delete_open(self, oid):
        self.open.remove(self.get_open(oid)); self.save_open()

    def sell_open(self, oid, sell, qty=None, ts=None):
        """Sell all (qty=None) or part of an open flip. The sold part becomes a normal completed flip
        (same fields as add(), plus bought_ts/note); any remainder stays open. Returns the completed flip."""
        o = self.get_open(oid)
        qty = o["qty"] if qty in (None, "") else int(qty)
        sell = int(sell)
        if qty <= 0 or qty > o["qty"]: raise ValueError(f"quantity sold must be between 1 and {o['qty']}")
        if sell < 0: raise ValueError("price must be >= 0")
        f = self.add(o["id"], o["name"], qty, o["buy"], sell, ts=ts, bought_ts=o["ts"], note=o.get("note"))
        # completed flip is saved first: a crash between the two writes can only duplicate, never lose
        if qty == o["qty"]: self.open.remove(o)
        else: o["qty"] -= qty
        self.save_open(); return f

    def open_rows(self, latest=None, now=None):
        """Open flips with live-price estimates: cost, held, live (instant-buy 'high'), est profit after tax."""
        now = now or time.time(); latest = latest or {}; out = []
        for o in self.open:
            live = (latest.get(str(o["id"])) or {}).get("high")
            out.append(dict(o, cost=o["qty"] * o["buy"], held_s=now - o["ts"], held=held(now - o["ts"]), live=live,
                            est=flip_profit(o["qty"], o["buy"], live, o["name"]) if live else None))
        return out

    def open_cost(self): return sum(o["qty"] * o["buy"] for o in self.open)

    def total(self): return sum(f["profit"] for f in self.flips)

    def today(self, now=None):
        """(profit, count) of flips logged today (local time)."""
        d = time.strftime("%Y-%m-%d", time.localtime(now or time.time()))
        fs = [f for f in self.flips if time.strftime("%Y-%m-%d", time.localtime(f["ts"])) == d]
        return sum(f["profit"] for f in fs), len(fs)

    def by_day(self):
        out = {}
        for f in self.flips:
            d = time.strftime("%Y-%m-%d", time.localtime(f["ts"]))
            out[d] = out.get(d, 0) + f["profit"]
        return dict(sorted(out.items()))

    def by_item(self):
        out = {}
        for f in self.flips:
            r = out.setdefault(f["name"], {"profit": 0, "qty": 0, "n": 0})
            r["profit"] += f["profit"]; r["qty"] += f["qty"]; r["n"] += 1
        return out

    def cumulative(self):
        acc, out = 0, []
        for f in sorted(self.flips, key=lambda f: f["ts"]):
            acc += f["profit"]; out.append((f["ts"], acc))
        return out


def fmt_pct(v):
    return "-" if v is None else f"{v:+.2f}%"


# ---------- time-of-day timing / ROI (v0.4) ----------
try:
    from zoneinfo import ZoneInfo
    ET = ZoneInfo("America/New_York")
except Exception:                     # Windows without tzdata: pip install --user tzdata
    ET = None

TIMING_DEFAULTS = {"min_samples": 3, "vol_frac": 0.5, "window": 2,
                   "night_start": 18, "night_end": 6}


def to_et(ts):
    import datetime as _dt
    if ET is None:
        raise RuntimeError("No tz data: run  py -3 -m pip install --user tzdata")
    return _dt.datetime.fromtimestamp(int(ts), ET)


def _mean_sd(xs):
    if not xs: return None, None
    m = sum(xs) / len(xs)
    sd = (sum((x - m) ** 2 for x in xs) / (len(xs) - 1)) ** .5 if len(xs) > 1 else 0.0
    return m, sd


def hour_profile(ts, name=None):
    """Bucket timeseries points by ET hour of day (DST-correct). Returns list of 24 dicts:
    hour, n, vol, low, high, mid, spread, low_sd, high_sd (None where no data).
    Volume averaged over all points in the hour (no-trade buckets count as 0); prices over points with trades.
    5m data is first summed/averaged to hourly buckets so n counts hours, not 5m slices."""
    hourly = {}
    for p in ts or []:
        t = p.get("timestamp")
        if t is None: continue
        b = hourly.setdefault(int(t) // 3600 * 3600, {"v": 0, "lo": [], "hi": []})
        b["v"] += (p.get("highPriceVolume") or 0) + (p.get("lowPriceVolume") or 0)
        if p.get("avgLowPrice"): b["lo"].append((p["avgLowPrice"], p.get("lowPriceVolume") or 1))
        if p.get("avgHighPrice"): b["hi"].append((p["avgHighPrice"], p.get("highPriceVolume") or 1))
    wavg = lambda xs: sum(a * w for a, w in xs) / sum(w for _, w in xs) if xs else None
    acc = [{"v": [], "lo": [], "hi": [], "sp": []} for _ in range(24)]
    for t, b in hourly.items():
        a = acc[to_et(t).hour]; a["v"].append(b["v"])
        lo, hi = wavg(b["lo"]), wavg(b["hi"])
        if lo: a["lo"].append(lo)
        if hi: a["hi"].append(hi)
        if lo and hi: a["sp"].append(hi - lo)
    out = []
    for h, a in enumerate(acc):
        lo, lsd = _mean_sd(a["lo"]); hi, hsd = _mean_sd(a["hi"])
        out.append({"hour": h, "n": len(a["v"]), "vol": _mean_sd(a["v"])[0], "low": lo, "high": hi,
                    "low_sd": lsd, "high_sd": hsd, "mid": (lo + hi) / 2 if lo and hi else None,
                    "spread": _mean_sd(a["sp"])[0], "n_low": len(a["lo"]), "n_high": len(a["hi"])})
    return out


def dow_hour_volume(ts):
    """7x24 matrix [weekday Mon=0][ET hour] of average volume (None = no samples)."""
    acc = [[[] for _ in range(24)] for _ in range(7)]
    for p in ts or []:
        if p.get("timestamp") is None: continue
        d = to_et(p["timestamp"])
        acc[d.weekday()][d.hour].append((p.get("highPriceVolume") or 0) + (p.get("lowPriceVolume") or 0))
    return [[(sum(c) / len(c)) if c else None for c in row] for row in acc]


def _window(prof, start, w, key):
    hs = [prof[(start + i) % 24] for i in range(w)]
    vals = [h[key] for h in hs if h[key]]
    return sum(vals) / len(vals) if len(vals) == w else None


def best_hours(prof, settings=None):
    """Pick best buy window (lowest avg low) and best sell window (highest avg high) of `window` hours,
    only among hours with >= min_samples and avg volume >= vol_frac * median hourly volume.
    Returns dict buy_h, sell_h, buy, sell, peak_h, quiet_h, eligible (list of hours)."""
    st = dict(TIMING_DEFAULTS); st.update(settings or {})
    vols = sorted(h["vol"] for h in prof if h["vol"] is not None and h["n"])
    med = vols[len(vols) // 2] if vols else 0
    ok = {h["hour"] for h in prof if h["n"] >= st["min_samples"] and (h["vol"] or 0) >= st["vol_frac"] * med}
    w = max(1, int(st["window"]))
    buy = sell = None
    for s in range(24):
        if not all((s + i) % 24 in ok for i in range(w)): continue
        lo, hi = _window(prof, s, w, "low"), _window(prof, s, w, "high")
        if lo and (buy is None or lo < buy[1]): buy = (s, lo)
        if hi and (sell is None or hi > sell[1]): sell = (s, hi)
    withv = [h for h in prof if h["n"] and h["vol"] is not None]
    peak = max(withv, key=lambda h: h["vol"])["hour"] if withv else None
    quiet = min(withv, key=lambda h: h["vol"])["hour"] if withv else None
    return {"buy_h": buy and buy[0], "buy": buy and buy[1], "sell_h": sell and sell[0], "sell": sell and sell[1],
            "window": w, "peak_h": peak, "quiet_h": quiet, "eligible": sorted(ok), "median_vol": med}


def timing_confidence(prof, bh):
    """('high'|'medium'|'low', reason). Based on samples in chosen hours and how the low->high
    hour-of-day gap compares to the day-to-day std dev of prices in those hours."""
    if bh.get("buy_h") is None or bh.get("sell_h") is None:
        return "none", "not enough hours with data/volume"
    hs = [prof[(bh["buy_h"] + i) % 24] for i in range(bh["window"])] + \
         [prof[(bh["sell_h"] + i) % 24] for i in range(bh["window"])]
    n = min(h["n"] for h in hs)
    sd = max((h["low_sd"] or 0) for h in hs[:bh["window"]]) + max((h["high_sd"] or 0) for h in hs[bh["window"]:])
    lows = [h["low"] for h in prof if h["low"]]; highs = [h["high"] for h in prof if h["high"]]
    # time-of-day effect = how much the hourly averages move vs the noise
    swing = (max(lows) - min(lows) + max(highs) - min(highs)) / 2 if lows and highs else 0
    z = swing / sd if sd else (float("inf") if swing else 0)
    if n >= 10 and z >= 1.0: return "high", f"{n}+ days/hour, hour effect {z:.1f}x day-to-day σ"
    if n >= 5 and z >= 0.5: return "medium", f"{n}+ days/hour, hour effect {z:.1f}x day-to-day σ"
    why = f"only {n} samples/hour" if n < 5 else f"hour effect {z:.1f}x σ (mostly noise)"
    return "low", "weak pattern: " + why


def timing_roi(prof, bh, name=None, limit=None):
    """Historical best-timing flip: buy at avg low in buy window, sell at avg high in sell window, after tax."""
    if not bh.get("buy") or not bh.get("sell"): return None
    buy, sell = round(bh["buy"]), round(bh["sell"])
    m, roi = margin(sell, buy, name)
    return {"buy": buy, "sell": sell, "margin": m, "roi": roi,
            "per_limit": m * limit if (limit and m is not None) else None}


def night_window(ts, now=None, start=18, end=6):
    """Most recent completed 'night' window [start ET previous day .. end ET] (or ongoing if now inside).
    Returns dict start, end (ET datetimes), peak_h, peak_vol, peak_low, peak_high, total_vol, avg_low, avg_high, n."""
    import datetime as _dt
    now = time.time() if now is None else now
    n_et = to_et(now)
    end_d = n_et.replace(hour=end, minute=0, second=0, microsecond=0)
    if end_d > n_et: end_d -= _dt.timedelta(days=1)
    st_d = end_d.replace(hour=start)
    while st_d >= end_d: st_d -= _dt.timedelta(days=1)
    t0, t1 = st_d.timestamp(), end_d.timestamp()
    pts = [p for p in ts or [] if t0 <= p.get("timestamp", 0) < t1]
    prof = {}
    for p in pts:
        b = prof.setdefault(int(p["timestamp"]) // 3600 * 3600, {"v": 0, "lo": [], "hi": []})
        b["v"] += (p.get("highPriceVolume") or 0) + (p.get("lowPriceVolume") or 0)
        if p.get("avgLowPrice"): b["lo"].append(p["avgLowPrice"])
        if p.get("avgHighPrice"): b["hi"].append(p["avgHighPrice"])
    if not prof: return {"start": st_d, "end": end_d, "n": 0}
    pk = max(prof, key=lambda k: prof[k]["v"]); b = prof[pk]
    av = lambda xs: sum(xs) / len(xs) if xs else None
    los = [x for v in prof.values() for x in v["lo"]]; his = [x for v in prof.values() for x in v["hi"]]
    return {"start": st_d, "end": end_d, "n": len(prof), "peak_h": to_et(pk).hour, "peak_vol": b["v"],
            "peak_low": av(b["lo"]), "peak_high": av(b["hi"]), "total_vol": sum(v["v"] for v in prof.values()),
            "avg_low": av(los), "avg_high": av(his)}


def fmt_hour(h, w=1):
    if h is None: return "-"
    f = lambda x: f"{x % 12 or 12}{'AM' if x % 24 < 12 else 'PM'}"
    return f(h) if w <= 1 else f"{f(h)}-{f((h + w) % 24)}"


def timing_analysis(ts1h, name=None, limit=None, settings=None):
    prof = hour_profile(ts1h, name); bh = best_hours(prof, settings)
    conf, why = timing_confidence(prof, bh)
    return {"profile": prof, "best": bh, "conf": conf, "why": why, "roi": timing_roi(prof, bh, name, limit)}
