"""Suggestions engine (v0.6): in-game OSRS GE flipping / investing ideas from historical OSRS Wiki price data.
Pure functions only (no I/O) so everything is unit-testable. Amounts are OSRS gp. Estimates, not guarantees."""
import math, time
from ge_core import margin, ge_tax, mid, h1_mid, h1_vol, pct_change, volatility, series_mids

STRATEGIES = ("Quick flips", "Overnight-hold", "Longer investment")
RISKS = ("Low", "Medium", "High")
BOND_NAMES = {"Old school bond", "Old school bond (untradeable)"}
MIN_POSITION = 0.01        # ignore positions smaller than 1% of the stack
SHORTLIST_N = 30           # max items that get a lazy per-item 24h timeseries fetch
WINDOW_H = 4               # GE buy-limit window (hours)

# Per-risk filters. max_price_frac: one unit may cost at most this share of the stack.
# max_vola: cap on 24h |change| % (bulk proxy) / daily sigma % when a timeseries is available.
# max_share: diversification cap per item (fraction of stack); None = no cap (High).
RISK = {
    "Low":    {"min_vol": 2000, "max_price_frac": 0.20, "max_vola": 3.0, "max_roi": 6.0,  "max_age_min": 20,
               "max_share": 0.20, "min_daily_vol": 20000},
    "Medium": {"min_vol": 500,  "max_price_frac": 0.35, "max_vola": 6.0, "max_roi": 15.0, "max_age_min": 45,
               "max_share": 0.30, "min_daily_vol": 5000},
    "High":   {"min_vol": 60,   "max_price_frac": 1.00, "max_vola": 1e9, "max_roi": 40.0, "max_age_min": 120,
               "max_share": None, "min_daily_vol": 500},
}
DEFAULTS = {"fill_frac": 0.15, "slots": 8, "max_items": 8, "members": "All", "exclude_bonds": True}


def parse_gp(s):
    """'10m' -> 10_000_000, '2.5k' -> 2500, '1,234' -> 1234, '1b' -> 1e9. Raises ValueError on junk / <= 0."""
    t = str(s).strip().lower().replace(",", "").replace("_", "").replace(" ", "").removesuffix("gp")
    mult = {"k": 1_000, "m": 1_000_000, "b": 1_000_000_000}.get(t[-1:] if t else "", 1)
    if mult != 1: t = t[:-1]
    try: v = float(t)
    except ValueError: raise ValueError(f"not a gp amount: {s!r}")
    if not math.isfinite(v) or v <= 0: raise ValueError(f"amount must be > 0: {s!r}")
    return int(round(v * mult))


def risk_settings(risk, **over):
    st = dict(RISK[risk if risk in RISK else "Medium"]); st.update({k: v for k, v in over.items() if v is not None})
    return st


def base_candidates(items, latest, h1, h1_old, cash, risk="Medium", now=None, members="All", exclude_bonds=True):
    """Common filter over the cached bulk data (/mapping, /latest, /1h now, /1h 24h ago). No API calls.
    Drops: missing prices, stale trades, thin volume, price > risk share of cash, bonds (opt), members filter,
    absurd spreads (ROI > risk cap = likely manipulated/stale), 24h move beyond the volatility cap."""
    st = risk_settings(risk); now = time.time() if now is None else now
    out = []
    for sid, p in latest.items():
        try: iid = int(sid)
        except ValueError: continue
        it = items.get(iid)
        hi, lo = p.get("high"), p.get("low")
        if not it or not hi or not lo or lo < 1: continue
        nm = it["name"]
        if exclude_bonds and nm in BOND_NAMES: continue
        if members == "F2P" and it.get("members"): continue
        if members == "Members" and not it.get("members"): continue
        age = now - min(p.get("highTime") or 0, p.get("lowTime") or 0)
        if age > st["max_age_min"] * 60: continue
        hp = h1.get(sid) or {}; v = h1_vol(hp)
        if v < st["min_vol"]: continue
        if lo > cash * st["max_price_frac"]: continue
        m, roi = margin(hi, lo, nm)
        if roi is not None and roi > st["max_roi"]: continue
        m_now = h1_mid(hp) or mid(hi, lo); m_old = h1_mid((h1_old or {}).get(sid))
        chg = pct_change(m_old, m_now)
        if chg is not None and abs(chg) > st["max_vola"] * 4: continue    # 24h jump >> risk tolerance
        out.append({"id": iid, "name": nm, "high": hi, "low": lo, "margin": m, "roi": roi, "limit": it.get("limit"),
                    "vol": v, "mid": m_now, "mid_24h": m_old, "chg24": chg, "age_min": age / 60,
                    "members": bool(it.get("members"))})
    return out


def _qty_cap(row, cash, fill_frac, hours=WINDOW_H):
    """Units you can realistically buy: buy limit (per 4h), share of market volume over `hours`, cash."""
    caps = [int(cash // max(row["buy"], 1)), max(1, int(row["vol"] * fill_frac * hours))]
    if row.get("limit"): caps.append(int(row["limit"]))
    return max(0, min(caps))


def _conf(score):
    return "High" if score >= 2 else "Med" if score >= 1 else "Low"


def rank_quick(cands, cash, risk="Medium", fill_frac=0.15):
    """Quick flips: buy at low+1, sell at high-1 (stay first in queue), positive after-tax margin.
    Expected profit per 4h limit window = margin * qty, qty = min(limit, fill_frac * 1h vol * 4, cash/buy)."""
    st = risk_settings(risk); out = []
    for c in cands:
        buy, sell = c["low"] + 1, c["high"] - 1
        if sell <= buy: buy, sell = c["low"], c["high"]
        m = sell - ge_tax(sell, c["name"]) - buy
        if m <= 0: continue
        r = dict(c, strategy="Quick flips", buy=buy, sell=sell, unit_profit=m, roi=m / buy * 100)
        q = _qty_cap(r, cash, fill_frac)
        if q <= 0: continue
        r["qty_cap"] = q; r["score"] = m * q
        r["fill_h"] = q / max(c["vol"] * fill_frac, 1e-9)
        pts = (c["vol"] >= st["min_vol"] * 4) + (c["age_min"] <= 10) + (r["roi"] <= 3)
        r["confidence"] = _conf(pts - (r["fill_h"] > 4))
        r["why"] = f"{r['roi']:.1f}% margin after tax, {c['vol']:,}/h traded"
        notes = []
        if c["age_min"] > 20: notes.append(f"last trade {c['age_min']:.0f}m ago")
        if r["roi"] > 8: notes.append("wide spread - may be stale")
        if c.get("chg24") is not None and abs(c["chg24"]) > 5: notes.append(f"moved {c['chg24']:+.1f}% 24h")
        r["notes"] = "; ".join(notes) or "margin can close fast; re-check before buying"
        r["hold"] = f"~{max(r['fill_h'], 0.25):.1f}h fill"
        r["lo_frac"] = 0.5          # pessimistic: half the margin / half fills
        out.append(r)
    out.sort(key=lambda r: -r["score"]); return out


def daily_stats(ts):
    """From a 24h-step /timeseries list: avg mid of last 7 and 30 days, 7d/30d % change, daily sigma %,
    dip-recovery rate (share of >=3% down days followed within 3 days by a close above the pre-dip level),
    avg daily volume (7d)."""
    pts = [x for x in (ts or []) if mid(x.get("avgHighPrice"), x.get("avgLowPrice"))]
    mids = series_mids(pts)
    if len(mids) < 8: return None
    vols = [(x.get("highPriceVolume") or 0) + (x.get("lowPriceVolume") or 0) for x in pts]
    l7, l30 = mids[-7:], mids[-30:]
    rec = dips = 0
    for i in range(1, len(l30) - 1):
        if l30[i] <= l30[i - 1] * 0.97:
            dips += 1; rec += any(v >= l30[i - 1] for v in l30[i + 1:i + 4])
    return {"avg7": sum(l7) / len(l7), "avg30": sum(l30) / len(l30), "chg7": pct_change(mids[-8], mids[-1]),
            "chg30": pct_change(l30[0], l30[-1]), "sigma": volatility(l30), "recovery": (rec / dips) if dips else None,
            "dvol": sum(vols[-7:]) / min(7, len(vols)), "last": mids[-1]}


def shortlist(cands, strategy, n=SHORTLIST_N):
    """Pick which items deserve a lazy per-item timeseries fetch (bulk-data pre-score)."""
    if strategy == "Overnight-hold":
        key = lambda c: (c["chg24"] or 0) * math.log10(max(c["vol"] * c["mid"], 10))   # dip size x gp liquidity
        pool = [c for c in cands if (c["chg24"] or 0) < -1]
        return sorted(pool, key=key)[:n]
    key = lambda c: -((c["chg24"] or 0) * math.log10(max(c["vol"] * c["mid"], 10)))
    return sorted(cands, key=key)[:n]


def rank_overnight(cands, cash, risk="Medium", fill_frac=0.15, daily=None, require_history=False):
    """Overnight / short hold: price below its recent (7d) average with decent volume -> mean reversion.
    Target = buy + discount * reversion_share; reversion share is cut by volatility (sigma-aware haircut).
    Without a timeseries the 24h-ago /1h mid stands in for the average."""
    st = risk_settings(risk); daily = daily or {}; out = []
    for c in cands:
        d = daily.get(c["id"])
        if require_history and not d: continue
        avg = d["avg7"] if d else c.get("mid_24h")
        sig = d["sigma"] if d and d["sigma"] is not None else abs(c.get("chg24") or 0)
        if not avg: continue
        if d and d["dvol"] < st["min_daily_vol"]: continue
        if sig > st["max_vola"]: continue
        buy = c["low"]; disc = (avg - buy) / avg * 100
        if disc < 1.0: continue
        share = max(0.25, 0.7 - sig / 20)                 # volatile items revert less reliably
        sell = int(buy + (avg - buy) * share)
        m = sell - ge_tax(sell, c["name"]) - buy
        if m <= 0: continue
        r = dict(c, strategy="Overnight-hold", buy=buy, sell=sell, unit_profit=m, roi=m / buy * 100, avg=avg, sigma=sig)
        q = _qty_cap(r, cash, fill_frac)
        if q <= 0: continue
        rec = d.get("recovery") if d else None
        r["qty_cap"] = q; r["score"] = m * q * (1 + (rec or 0.5)) / (1 + sig / 5)
        r["fill_h"] = q / max(c["vol"] * fill_frac, 1e-9)
        pts = (d is not None) + (sig <= 3) + ((rec or 0) >= 0.6)
        r["confidence"] = _conf(pts)
        r["why"] = (f"{disc:.1f}% under {'7d' if d else '24h-ago'} avg {avg:,.0f}"
                    + (f"; recovers {rec:.0%} of dips" if rec is not None else ""))
        r["notes"] = f"sigma {sig:.1f}%/day" + ("; no 7d history (24h proxy)" if not d else "") + \
                     ("; falling knife?" if (c.get("chg24") or 0) < -8 else "")
        r["hold"] = f"fill ~{max(r['fill_h'], .25):.1f}h, hold 8-24h"
        r["lo_frac"] = -0.25 if sig > 4 else 0.0           # could lose a bit if it keeps sliding
        out.append(r)
    out.sort(key=lambda r: -r["score"]); return out


def rank_long(cands, cash, risk="Medium", fill_frac=0.15, daily=None):
    """Longer investment (1-4 weeks): needs a 24h timeseries. Uptrend over 7-30d with liquidity, or an item that
    consistently recovers after dips. Target = current + half the 30d trend (capped), volatility discounted."""
    st = risk_settings(risk); daily = daily or {}; out = []
    for c in cands:
        d = daily.get(c["id"])
        if not d or d["chg30"] is None or d["dvol"] < st["min_daily_vol"]: continue
        sig = d["sigma"] or 0
        if sig > st["max_vola"]: continue
        rec = d["recovery"]
        trending = d["chg30"] > 2 and (d["chg7"] or 0) > -2 and d["last"] >= d["avg30"]
        recovering = rec is not None and rec >= 0.6 and c["low"] < d["avg30"]
        if not (trending or recovering): continue
        buy = c["low"]
        up = min(max(d["chg30"], 0) * 0.5, st["max_roi"]) if trending else (d["avg30"] - buy) / buy * 100
        sell = int(buy * (1 + up / 100))
        m = sell - ge_tax(sell, c["name"]) - buy
        if m <= 0: continue
        r = dict(c, strategy="Longer investment", buy=buy, sell=sell, unit_profit=m, roi=m / buy * 100, sigma=sig)
        q = _qty_cap(r, cash, fill_frac, hours=24)        # several limit windows over a day of buying
        if c.get("limit"): q = min(q, int(c["limit"]) * 6)
        if q <= 0: continue
        r["qty_cap"] = q; r["score"] = m * q / (1 + sig / 4)
        r["fill_h"] = q / max(c["vol"] * fill_frac, 1e-9)
        r["confidence"] = _conf(trending + recovering + (sig <= 2) - (d["chg30"] > 40))
        r["why"] = (f"30d {d['chg30']:+.1f}%, 7d {d['chg7'] or 0:+.1f}%" if trending else f"recovers {rec:.0%} of dips")
        r["notes"] = f"sigma {sig:.1f}%/day; trends reverse - set a stop" + ("; parabolic" if d["chg30"] > 40 else "")
        r["hold"] = f"buy over ~{max(r['fill_h'], 1):.0f}h, hold 1-4 wk"
        r["lo_frac"] = -0.5
        out.append(r)
    out.sort(key=lambda r: -r["score"]); return out


RANKERS = {"Quick flips": rank_quick, "Overnight-hold": rank_overnight, "Longer investment": rank_long}


def allocate(ranked, cash, risk="Medium", slots=8, max_items=8, max_share=None):
    """Greedy portfolio: walk ranked list, one GE slot per item, at most min(slots, max_items) items.
    qty = min(qty_cap, cash share cap (Low/Med), remaining cash // buy). Then totals."""
    st = risk_settings(risk)
    share = st["max_share"] if max_share is None else (max_share if max_share > 0 else None)
    n = max(0, min(int(slots), int(max_items)))
    left, rows = int(cash), []
    for r in ranked:
        if len(rows) >= n or left <= 0: break
        cap = r["qty_cap"]
        if share: cap = min(cap, int(cash * share // r["buy"]))
        q = min(cap, left // r["buy"])
        if q <= 0 or q * r["buy"] < cash * MIN_POSITION: continue      # skip crumbs (< 1% of stack)
        cost = q * r["buy"]; prof = q * r["unit_profit"]
        rows.append(dict(r, qty=q, cost=cost, profit=prof, profit_lo=int(prof * r.get("lo_frac", 0.5)),
                         share=cost / cash))
        left -= cost
    tot = {"cash": int(cash), "used": int(cash) - left, "left": left, "n": len(rows),
           "profit_hi": sum(r["profit"] for r in rows), "profit_lo": sum(r["profit_lo"] for r in rows)}
    return rows, tot


def suggest(items, latest, h1, h1_old, cash, strategy="Quick flips", risk="Medium", daily=None, now=None,
            members="All", exclude_bonds=True, fill_frac=0.15, slots=8, max_items=8, max_share=None):
    """Full pipeline on already-fetched data. daily: {id: daily_stats(...)} for the shortlist (optional)."""
    c = base_candidates(items, latest, h1, h1_old, cash, risk, now, members, exclude_bonds)
    kw = {} if strategy == "Quick flips" else {"daily": daily}
    if strategy == "Overnight-hold": kw["require_history"] = bool(daily) and risk != "High"   # 24h proxy only on High
    ranked = RANKERS[strategy](c, cash, risk, fill_frac, **kw)
    return allocate(ranked, cash, risk, slots, max_items, max_share)
