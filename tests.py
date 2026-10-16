"""Headless tests. python tests.py   (add --live to also hit the real API once)"""
import sys, tempfile, unittest, ge_core as c

class T(unittest.TestCase):
    def test_tax(self):
        self.assertEqual(c.ge_tax(49), 0); self.assertEqual(c.ge_tax(50), 1)
        self.assertEqual(c.ge_tax(99), 1); self.assertEqual(c.ge_tax(100), 2)
        self.assertEqual(c.ge_tax(1_000_000), 20_000)
        self.assertEqual(c.ge_tax(250_000_000), 5_000_000); self.assertEqual(c.ge_tax(2_000_000_000), 5_000_000)
        self.assertEqual(c.ge_tax(10_000_000, "Old school bond"), 0)
    def test_margin(self):
        m, roi = c.margin(1000, 900); self.assertEqual(m, 80); self.assertAlmostEqual(roi, 80/9)
        self.assertEqual(c.margin(None, 5), (None, None))
    def test_store_alerts(self):
        d = tempfile.mkdtemp(); s = c.Store(d); s.add(4151); s.set_alert(4151, above=800, below=700)
        self.assertEqual(c.Store(d).data["watch"], [4151])
        lat = {"4151": {"high": 810, "low": 690}}
        self.assertEqual(len(c.check_alerts(s.data["alerts"], lat)), 2)
        self.assertEqual(c.check_alerts(s.data["alerts"], lat), [])  # fires once
    def test_ratelimit_and_ua(self):
        calls = []
        a = c.Api(tempfile.mkdtemp(), opener=lambda u: calls.append(u) or {"data": {}})
        a.latest(force=True); a.latest(force=True); a.latest(force=True)
        self.assertEqual(len(calls), 1); self.assertNotIn("python", c.USER_AGENT.lower().split()[0])

class Flips(unittest.TestCase):
    NOW = 1_000_000
    ITEMS = {1: {"name": "Good", "limit": 100}, 2: {"name": "Stale", "limit": 100},
             3: {"name": "LowVol", "limit": 100}, 4: {"name": "Neg", "limit": 100},
             5: {"name": "Absurd", "limit": 100}, 6: {"name": "Better", "limit": 10000},
             7: {"name": "NoLimit"}, 8: {"name": "Old school bond", "limit": 100}}
    def p(self, hi, lo, age=60, lage=None):
        return {"high": hi, "low": lo, "highTime": self.NOW - age, "lowTime": self.NOW - (age if lage is None else lage)}
    def test_filters_and_rank(self):
        lat = {"1": self.p(1100, 1000), "2": self.p(1100, 1000, lage=3600), "3": self.p(1100, 1000),
               "4": self.p(1000, 1000), "5": self.p(3000, 1000), "6": self.p(110, 100),
               "7": self.p(1100, 1000), "8": self.p(1100, 1000), "x": self.p(1, 1)}
        vol = {"1": 500, "2": 500, "3": 50, "4": 500, "5": 500, "6": 5000, "7": 50000, "8": 500}
        r = c.best_flips(self.ITEMS, lat, vol, now=self.NOW)
        names = [x["name"] for x in r]
        self.assertEqual(set(names), {"Good", "Better", "NoLimit", "Old school bond"})
        g = next(x for x in r if x["name"] == "Good")
        self.assertEqual(g["margin"], 78); self.assertEqual(g["profit_limit"], 7800); self.assertEqual(g["score"], 7800)
        b = next(x for x in r if x["name"] == "Better")   # 8gp * min(10000, 5000)
        self.assertEqual(b["score"], 40000); self.assertEqual(b["profit_limit"], 80000)
        bond = next(x for x in r if x["name"] == "Old school bond"); self.assertEqual(bond["margin"], 100)  # exempt
        nl = next(x for x in r if x["name"] == "NoLimit"); self.assertIsNone(nl["profit_limit"])
        self.assertEqual(nl["score"], 78 * 50000)
        self.assertEqual(names[0], "NoLimit"); self.assertEqual(names[1], "Better")
        self.assertEqual([x["score"] for x in r], sorted((x["score"] for x in r), reverse=True))
    def test_settings_and_topn(self):
        lat = {str(i): self.p(1100 + i, 1000) for i in range(1, 6)}
        items = {i: {"name": f"I{i}", "limit": 10} for i in range(1, 6)}
        vol = {str(i): 1000 for i in range(1, 6)}
        self.assertEqual(len(c.best_flips(items, lat, vol, now=self.NOW, settings={"top_n": 3})), 3)
        self.assertEqual(c.best_flips(items, lat, vol, now=self.NOW, settings={"min_vol_1h": 5000}), [])
        self.assertEqual(c.best_flips(items, lat, vol, now=self.NOW, settings={"max_age_min": 0.5}), [])
        st = c.ticker_settings({"ticker": {"top_n": 5}}); self.assertEqual(st["top_n"], 5); self.assertEqual(st["max_roi"], 50.0)
        d = tempfile.mkdtemp(); s = c.Store(d); self.assertTrue(s.data["show_ticker"])
        s.data["show_ticker"] = False; s.save(); self.assertFalse(c.Store(d).data["show_ticker"])

class Analytics(unittest.TestCase):
    def test_change_mid(self):
        self.assertAlmostEqual(c.pct_change(100, 110), 10.0); self.assertAlmostEqual(c.pct_change(200, 150), -25.0)
        self.assertIsNone(c.pct_change(0, 5)); self.assertIsNone(c.pct_change(None, 5)); self.assertIsNone(c.pct_change(5, None))
        self.assertEqual(c.mid(110, 90), 100); self.assertEqual(c.mid(None, 90), 90); self.assertIsNone(c.mid(None, None))
    def test_volatility(self):
        self.assertEqual(c.volatility([100, 100, 100, 100]), 0.0)
        self.assertIsNone(c.volatility([100, 110])); self.assertIsNone(c.volatility([100, None, None]))
        v = c.volatility([100, 110, 99, 108.9])   # returns +10, -10, +10 -> mean 3.33, pop stdev 9.428
        self.assertAlmostEqual(v, 9.428090, places=4)
        self.assertAlmostEqual(c.volatility([100, None, 110, 99, 108.9]), v)  # gaps skipped
    def test_sma_stats_trend(self):
        self.assertEqual(c.sma([1, 2, 3, 4], 2), [None, 1.5, 2.5, 3.5])
        self.assertEqual(c.sma([1, None, 3], 2), [None, 1, 3])
        st = c.series_stats([None, 100, 50, 150, 120]); self.assertEqual((st["min"], st["max"]), (50, 150))
        self.assertAlmostEqual(st["avg"], 105); self.assertAlmostEqual(st["chg"], 20.0); self.assertIsNone(c.series_stats([None]))
        self.assertEqual(c.trend([10] * 6 + [20] * 6)[1], "↑"); self.assertEqual(c.trend([20] * 6 + [10] * 6)[1], "↓")
        self.assertEqual(c.trend([100, 101, 100, 101])[1], "→"); self.assertEqual(c.trend([None])[1], "?")
    def test_watch_summary(self):
        ts = [{"timestamp": i, "avgHighPrice": 1100 + i * 10, "avgLowPrice": 1000 + i * 10} for i in range(25)]
        s = c.watch_summary(ts)
        self.assertAlmostEqual(s["chg"], (1290 - 1050) / 1050 * 100); self.assertIsNotNone(s["vol"])
        self.assertIn(s["arrow"], "↑↓→")
    def test_movers_filter(self):
        items = {1: {"name": "Up"}, 2: {"name": "Down"}, 3: {"name": "Junk"}, 4: {"name": "Cheap"}, 5: {"name": "New"},
                 6: {"name": "Flat"}, 7: {"name": "OldThin"}}
        def h(hi, lo, v): return {"avgHighPrice": hi, "avgLowPrice": lo, "highPriceVolume": v // 2, "lowPriceVolume": v - v // 2}
        old = {"1": h(1010, 990, 5000), "2": h(2020, 1980, 5000), "3": h(1000, 1000, 5000), "4": h(10, 10, 1e6),
               "6": h(500, 500, 9000), "7": h(1000, 1000, 10)}
        now = {"1": h(1212, 1188, 5000), "2": h(1515, 1485, 5000), "3": h(5000, 5000, 20), "4": h(20, 20, 10**6),
               "5": h(9000, 9000, 9000), "6": h(500, 500, 9000), "7": h(3000, 3000, 9000)}
        g, l = c.market_movers(items, now, old, {"min_vol": 500, "min_price": 100, "min_value": 1_000_000})
        self.assertEqual([r["name"] for r in g], ["Up"]); self.assertAlmostEqual(g[0]["chg"], 20.0)
        self.assertEqual([r["name"] for r in l], ["Down"]); self.assertAlmostEqual(l[0]["chg"], -25.0)
        g2, _ = c.market_movers(items, now, old, {"min_vol": 0, "min_value": 0})
        self.assertEqual([r["name"] for r in g2][:2], ["Junk", "OldThin"])   # junk only appears when unfiltered
        tv, tval = c.most_traded(items, now, 3)
        self.assertEqual(tv[0]["name"], "Cheap"); self.assertEqual(tval[0]["name"], "New")
    def test_flip_log(self):
        self.assertEqual(c.flip_profit(10, 1000, 1100), (1100 - 22 - 1000) * 10)
        self.assertEqual(c.flip_profit(5, 40, 49), 45)                     # <50gp: no tax
        self.assertEqual(c.flip_profit(1, 300_000_000, 310_000_000), 5_000_000)   # tax capped
        self.assertEqual(c.flip_profit(1, 9_000_000, 9_100_000, "Old school bond"), 100_000)  # exempt
        self.assertEqual(c.flip_profit(2, 1000, 900), -236)                # loss incl tax
        d = tempfile.mkdtemp(); fl = c.FlipLog(d)
        fl.add(4151, "Abyssal whip", 2, 1_500_000, 1_600_000, ts=1_700_000_000)
        fl.add(4151, "Abyssal whip", 1, 1_600_000, 1_550_000, ts=1_700_000_000 + 86400)
        fl.add(2, "Cannonball", 1000, 200, 210, ts=1_700_000_000 + 86400)
        r = c.FlipLog(d)   # persisted
        self.assertEqual(len(r.flips), 3)
        self.assertEqual(r.flips[0]["profit"], (1_600_000 - 32_000 - 1_500_000) * 2); self.assertEqual(r.flips[0]["tax"], 64_000)
        exp = 136_000 + (1_550_000 - 31_000 - 1_600_000) + (210 - 4 - 200) * 1000
        self.assertEqual(r.total(), exp)
        self.assertEqual(r.by_item()["Abyssal whip"]["n"], 2); self.assertEqual(r.by_item()["Cannonball"]["profit"], 6000)
        self.assertEqual(sum(r.by_day().values()), exp); self.assertEqual(len(r.by_day()), 2)
        self.assertEqual(r.cumulative()[-1][1], exp)
        r.delete(2); self.assertEqual(len(c.FlipLog(d).flips), 2)
        with self.assertRaises(ValueError): r.add(2, "Cannonball", 0, 1, 2)
    def test_timeseries_cache(self):
        calls = []
        a = c.Api(tempfile.mkdtemp(), opener=lambda u: calls.append(u) or {"data": [1]})
        a.timeseries(4151); a.timeseries(4151); a.hourly_at(7200); a.hourly_at(7300)
        self.assertEqual(len(calls), 2); self.assertIn("timestamp=7200", calls[1])

class Timing(unittest.TestCase):
    @staticmethod
    def ts_et(y, mo, d, h):
        import datetime as dt
        return int(dt.datetime(y, mo, d, h, tzinfo=c.ET).timestamp())
    def pt(self, t, lo, hi, v=100):
        return {"timestamp": t, "avgLowPrice": lo, "avgHighPrice": hi, "lowPriceVolume": v // 2, "highPriceVolume": v - v // 2}
    def test_et_bucket_dst(self):
        # 2026-03-08 DST starts (EST->EDT); 2026-11-01 ends. Same UTC hour maps to different ET hours.
        self.assertEqual(c.to_et(1772884800).hour, 7)    # 2026-03-07 12:00 UTC = 7 AM EST (DST starts 3-08 2AM)
        self.assertEqual(c.to_et(1773057600).hour, 8)    # 2026-03-09 12:00 UTC = 8 AM EDT
        self.assertEqual(c.to_et(1793448000).hour, 8)    # 2026-10-31 12:00 UTC = 8 AM EDT
        self.assertEqual(c.to_et(1793538000).hour, 8)    # 2026-11-01 13:00 UTC = 8 AM EST
        pts = [self.pt(self.ts_et(2026, 3, d, 9), 100, 110) for d in range(5, 12)]
        prof = c.hour_profile(pts)
        self.assertEqual(prof[9]["n"], 7); self.assertEqual(sum(h["n"] for h in prof), 7)
    def test_5m_merged_to_hour(self):
        t0 = self.ts_et(2026, 6, 1, 14)
        prof = c.hour_profile([self.pt(t0 + 300 * i, 100, 110, 10) for i in range(12)])
        self.assertEqual(prof[14]["n"], 1); self.assertEqual(prof[14]["vol"], 120)
    def synth(self, days=14):
        pts = []
        for d in range(days):
            for h in range(24):
                t = self.ts_et(2026, 9, 1 + d, h)
                lo = 1000 + (50 if 8 <= h <= 20 else 0) + (d % 3)        # cheap at night
                vol = 20 if h == 3 else (500 if 1 <= h <= 5 else 200)    # 3 AM: cheapest-ish but dead
                if h == 3: lo -= 30
                pts.append(self.pt(t, lo, lo + 100 + (40 if h in (18, 19) else 0), vol))
        return pts
    def test_best_hours_volume_threshold(self):
        prof = c.hour_profile(self.synth())
        b = c.best_hours(prof, {"window": 1})
        self.assertNotEqual(b["buy_h"], 3)                 # 3 AM excluded: volume below threshold
        self.assertIn(b["buy_h"], (0, 1, 2, 4, 5, 6, 7, 21, 22, 23))
        self.assertIn(b["sell_h"], (18, 19)); self.assertIn(b["peak_h"], (1, 2, 4, 5))
        self.assertEqual(b["quiet_h"], 3)
        b0 = c.best_hours(prof, {"window": 1, "vol_frac": 0}); self.assertEqual(b0["buy_h"], 3)
        b2 = c.best_hours(prof, {"window": 2}); self.assertIn(b2["sell_h"], (18,))
    def test_timing_roi_and_conf(self):
        prof = c.hour_profile(self.synth()); b = c.best_hours(prof, {"window": 1})
        r = c.timing_roi(prof, b, limit=100)
        exp_m = round(b["sell"]) - c.ge_tax(round(b["sell"])) - round(b["buy"])
        self.assertEqual(r["margin"], exp_m); self.assertAlmostEqual(r["roi"], exp_m / round(b["buy"]) * 100)
        self.assertEqual(r["per_limit"], exp_m * 100)
        self.assertEqual(c.timing_confidence(prof, b)[0], "high")
        few = c.hour_profile(self.synth(days=2)); self.assertEqual(c.timing_confidence(few, c.best_hours(few, {"min_samples": 1}))[0], "low")
        import random; random.seed(1)
        noisy = [dict(p, avgLowPrice=1000 + random.randint(-300, 300)) for p in self.synth()]
        for p in noisy: p["avgHighPrice"] = p["avgLowPrice"] + 100
        np_ = c.hour_profile(noisy); self.assertNotEqual(c.timing_confidence(np_, c.best_hours(np_))[0], "high")
        self.assertEqual(c.timing_confidence(c.hour_profile([]), c.best_hours(c.hour_profile([])))[0], "none")
    def test_night_window(self):
        pts = self.synth(); now = self.ts_et(2026, 9, 10, 9)
        n = c.night_window(pts, now=now, start=18, end=6)
        self.assertEqual((n["start"].day, n["start"].hour, n["end"].day, n["end"].hour), (9, 18, 10, 6))
        self.assertEqual(n["n"], 12); self.assertIn(n["peak_h"], (1, 2, 4, 5))
        n2 = c.night_window(pts, now=self.ts_et(2026, 9, 10, 3)); self.assertEqual(n2["end"].day, 9)
    def test_rank_by_roi(self):
        it = {1: {"name": "Big", "limit": 1000}, 2: {"name": "Pct", "limit": 1}}
        lat = {"1": {"high": 1100, "low": 1000, "highTime": 999_990, "lowTime": 999_990},
               "2": {"high": 130, "low": 100, "highTime": 999_990, "lowTime": 999_990}}
        vol = {"1": 1000, "2": 1000}
        self.assertEqual(c.best_flips(it, lat, vol, now=1_000_000)[0]["name"], "Big")
        self.assertEqual(c.best_flips(it, lat, vol, now=1_000_000, settings={"rank_by": "roi"})[0]["name"], "Pct")

class Theme(unittest.TestCase):
    def test_palette_lookup(self):
        import theme as th
        self.assertEqual(set(th.PALETTES), {"light", "dark"})
        self.assertEqual(set(th.PALETTES["light"]), set(th.PALETTES["dark"]))   # same keys in both
        self.assertIs(th.palette("Dark"), th.PALETTES["dark"]); self.assertIs(th.palette("light"), th.PALETTES["light"])
        self.assertIs(th.palette(None), th.PALETTES["dark"]); self.assertIs(th.palette("neon"), th.PALETTES["dark"])
        d = th.PALETTES["dark"]
        self.assertEqual((d["bg"], d["accent"], d["pos"], d["neg"]), ("#1e1e1e", "#d4af37", "#4caf50", "#ff6f6c"))

    def test_contrast(self):
        import theme as th
        for n, p in th.PALETTES.items():
            for fg, bg in (("fg", "bg"), ("fg", "field"), ("fg", "heading"), ("pos", "field"), ("neg", "field"),
                           ("select_fg", "select"), ("chart_fg", "chart_bg"), ("ticker_text", "ticker_bg"),
                           ("ticker_pos", "ticker_bg"), ("muted", "bg"), ("info", "bg"), ("accent", "bg"), ("ticker_name", "ticker_bg")):
                self.assertGreaterEqual(th.contrast(p[fg], p[bg]), 4.5, f"{n}: {fg} on {bg}")

    def test_theme_persistence(self):
        import theme as th, json, os
        d = tempfile.mkdtemp(); s = c.Store(d)
        self.assertEqual(th.get_theme(s.data), "dark")                       # new install default
        self.assertEqual(th.set_theme(s, "LIGHT"), "light")
        self.assertEqual(th.get_theme(c.Store(d).data), "light")             # survives reload
        th.set_theme(s, "bogus"); self.assertEqual(c.Store(d).data["theme"], "dark")
        with open(os.path.join(d, "watchlist.json"), "w") as fh: json.dump({"watch": [2]}, fh)   # pre-0.5 file
        s2 = c.Store(d); self.assertEqual(th.get_theme(s2.data), "dark"); self.assertEqual(s2.data["watch"], [2])


import suggestions as sg


class UX(unittest.TestCase):
    """v0.7: gp formatting, settings migration, window geometry."""
    def test_short_gp(self):
        cases = {0: "0", 7: "7", 950: "950", 999: "999", 999.6: "1k", 1000: "1k", 1234: "1.2k", 12345: "12.3k",
                 45300: "45.3k", 795700: "796k", 999_999: "1m", 1_000_000: "1m", 3_412_345: "3.4m", 22_839_211: "22.8m",
                 415_588_000: "416m", 999_960_000: "1b", 1_250_000_000: "1.25b", -15: "-15", -47_309: "-47.3k",
                 -2_500_000: "-2.5m", None: "-"}
        for v, want in cases.items(): self.assertEqual(c.short_gp(v), want, v)

    def test_fmt_gp_signed(self):
        self.assertEqual(c.fmt_gp(1234567), "1,234,567"); self.assertEqual(c.fmt_gp(None), "-")
        self.assertEqual(c.fmt_gp(1234.6), "1,235"); self.assertEqual(c.fmt_gp(-15), "-15")
        self.assertEqual(c.signed_gp(2928, short=False), "+2,928"); self.assertEqual(c.signed_gp(601_000), "+601k")
        self.assertEqual(c.signed_gp(-57_706), "-57.7k"); self.assertEqual(c.signed_gp(0), "0")
        self.assertEqual(c.signed_gp(None), "-")
        self.assertEqual(c.ago(12), "12s ago"); self.assertEqual(c.ago(125), "2m ago"); self.assertEqual(c.ago(3725), "1h 2m ago")

    def test_migrate_settings_pure(self):
        old = {"watch": [4151, "560", 4151, "junk", None], "alerts": {4151: {"above": 900}, "2": "bad"}, "theme": "neon",
               "show_ticker": False, "ticker": {"min_vol_1h": 250, "rank_by": "roi"}, "custom": 1}
        m = c.migrate_settings(old)
        self.assertEqual(m["watch"], [4151, 560])
        self.assertEqual(m["alerts"], {"4151": {"above": 900, "below": None, "fired": []}})
        self.assertEqual(m["theme"], "dark"); self.assertFalse(m["show_ticker"]); self.assertEqual(m["custom"], 1)
        self.assertEqual(m["ticker"]["min_vol_1h"], 250); self.assertEqual(m["ticker"]["rank_by"], "roi")
        self.assertEqual(m["ticker"]["max_age_min"], c.TICKER_DEFAULTS["max_age_min"])
        self.assertEqual(m["ui"], c.UI_DEFAULTS); self.assertEqual(m["finder"], c.FINDER_DEFAULTS)
        self.assertEqual(m["settings_version"], c.SETTINGS_VERSION)
        self.assertEqual(c.migrate_settings(m), m)                    # idempotent
        self.assertEqual(c.migrate_settings(None)["watch"], [])       # missing / corrupt file
        self.assertEqual(old["watch"][1], "560")                      # input not mutated

    def test_store_migrates_file_with_backup(self):
        import json, os
        d = tempfile.mkdtemp(); f = os.path.join(d, "watchlist.json")
        v06 = {"watch": [4151, 11832], "alerts": {"4151": {"above": 800000, "below": None, "fired": ["above"]}},
               "auto_refresh": False, "show_ticker": True, "theme": "light", "ticker": {"top_n": 10}}
        with open(f, "w") as fh: json.dump(v06, fh)
        s = c.Store(d)
        self.assertEqual(s.data["watch"], [4151, 11832]); self.assertEqual(s.data["alerts"]["4151"]["fired"], ["above"])
        self.assertFalse(s.data["auto_refresh"]); self.assertEqual(s.data["theme"], "light")
        self.assertEqual(s.data["ticker"]["top_n"], 10); self.assertEqual(s.data["ui"]["tab"], "home")
        with open(f) as fh: self.assertEqual(json.load(fh)["settings_version"], 2)        # migrated on disk
        with open(f + ".v1.bak") as fh: self.assertEqual(json.load(fh), v06)              # untouched backup
        s.data["ui"]["tips_seen"] = True; s.save(); self.assertTrue(c.Store(d).data["ui"]["tips_seen"])
        with open(f, "w") as fh: fh.write("{not json")                                    # corrupt -> defaults
        self.assertEqual(c.Store(d).data["watch"], [])

    def test_flip_today_and_alert_log(self):
        import time as t
        d = tempfile.mkdtemp(); fl = c.FlipLog(d); now = t.time()
        fl.add(560, "Death rune", 100, 190, 200, ts=now); fl.add(560, "Death rune", 100, 190, 200, ts=now - 3 * 86400)
        self.assertEqual(fl.today(now), (600, 1))           # (200 - 4 tax - 190) x 100
        s = c.Store(d)
        for i in range(40): s.log_alert(4151, "above", 10 + i, 5)
        self.assertEqual(len(s.data["alert_log"]), 30); self.assertEqual(s.data["alert_log"][-1]["price"], 49)

    def test_fit_geometry_and_icon_url(self):
        self.assertEqual(c.fit_geometry("1400x860+50+40", 1920, 1080), "1400x860+50+40")
        self.assertEqual(c.fit_geometry("1400x860", 1366, 768), "1346x688")
        self.assertEqual(c.fit_geometry("2560x1440+3000+10", 1920, 1080), "1900x1000")     # monitor removed
        self.assertEqual(c.fit_geometry("garbage", 1920, 1080), "1400x860")
        self.assertEqual(c.icon_url("Abyssal whip.png"), "https://oldschool.runescape.wiki/images/Abyssal_whip.png")
        self.assertEqual(c.icon_url("Prayer potion(4).png"), "https://oldschool.runescape.wiki/images/Prayer_potion%284%29.png")
        self.assertIsNone(c.icon_url(None))

    def test_friendly_error(self):
        import urllib.error, socket
        self.assertIn("internet", c.friendly_error(urllib.error.URLError("x")))
        self.assertIn("internet", c.friendly_error(socket.timeout()))
        self.assertIn("slow down", c.friendly_error(urllib.error.HTTPError("u", 429, "x", {}, None)))


class Suggest(unittest.TestCase):
    NOW = 2_000_000
    def p(self, hi, lo, age=60):
        return {"high": hi, "low": lo, "highTime": self.NOW - age, "lowTime": self.NOW - age}
    def h(self, mid, vol):
        return {"avgHighPrice": mid, "avgLowPrice": mid, "highPriceVolume": vol // 2, "lowPriceVolume": vol - vol // 2}
    def world(self):
        items = {1: {"name": "Liquid", "limit": 10000, "members": False}, 2: {"name": "Pricey", "limit": 8, "members": True},
                 3: {"name": "Thin", "limit": 100, "members": True}, 4: {"name": "Old school bond", "limit": 100},
                 5: {"name": "Manip", "limit": 100, "members": True}, 6: {"name": "Stale", "limit": 100, "members": True},
                 7: {"name": "Mid", "limit": 2000, "members": True}}
        lat = {"1": self.p(210, 200), "2": self.p(5_300_000, 5_000_000), "3": self.p(1100, 1000),
               "4": self.p(12_000_000, 11_500_000), "5": self.p(3000, 1000), "6": self.p(1100, 1000, age=9000),
               "7": self.p(10_400, 10_000)}
        h1 = {"1": self.h(205, 50000), "2": self.h(5_050_000, 600), "3": self.h(1050, 100), "4": self.h(11_700_000, 3000),
              "5": self.h(2000, 5000), "6": self.h(1050, 5000), "7": self.h(10_200, 3000)}
        old = {k: self.h(v["avgHighPrice"], 1000) for k, v in h1.items()}
        return items, lat, h1, old

    def test_parse_gp(self):
        P = sg.parse_gp
        self.assertEqual(P("10m"), 10_000_000); self.assertEqual(P("2.5k"), 2500); self.assertEqual(P("1,234"), 1234)
        self.assertEqual(P(" 1B "), 1_000_000_000); self.assertEqual(P("750K"), 750_000); self.assertEqual(P("1.25m gp"), 1_250_000)
        self.assertEqual(P("5000"), 5000)
        for bad in ("", "abc", "-5m", "0", "m", "nan"):
            with self.assertRaises(ValueError): P(bad)

    def test_risk_filters(self):
        items, lat, h1, old = self.world()
        names = lambda risk, cash=10_000_000, **k: {c["name"] for c in sg.base_candidates(items, lat, h1, old, cash, risk, self.NOW, **k)}
        low, med, high = names("Low"), names("Medium"), names("High")
        self.assertEqual(low, {"Liquid", "Mid"})                 # volume >= 2000, ROI <= 6%
        self.assertEqual(med, {"Liquid", "Mid"})                 # Pricey > 35% of 10m, Thin vol 100 < 500
        self.assertEqual(high, {"Liquid", "Mid", "Pricey", "Thin"})
        for s in (low, med, high): self.assertFalse(s & {"Manip", "Stale", "Old school bond"})
        self.assertIn("Old school bond", names("High", 100_000_000, exclude_bonds=False))
        self.assertEqual(names("High", members="F2P"), {"Liquid"})
        self.assertNotIn("Liquid", names("High", members="Members"))
        self.assertGreater(sg.RISK["Low"]["min_vol"], sg.RISK["Medium"]["min_vol"] > sg.RISK["High"]["min_vol"])

    def test_quick_rank_and_tax(self):
        items, lat, h1, old = self.world()
        c = sg.base_candidates(items, lat, h1, old, 10_000_000, "High", self.NOW)
        r = sg.rank_quick(c, 10_000_000, "High", fill_frac=0.1)
        by = {x["name"]: x for x in r}
        L = by["Liquid"]; self.assertEqual((L["buy"], L["sell"]), (201, 209))
        self.assertEqual(L["unit_profit"], 209 - 4 - 201)                    # 2% tax floored
        self.assertEqual(L["qty_cap"], 10000)                                # min(limit, 0.1*50000*4, cash)
        P = by["Pricey"]; self.assertEqual(P["unit_profit"], 5_299_999 - 105_999 - 5_000_001)
        self.assertEqual(P["qty_cap"], 1)                                   # cash 10m / 5m -> 1 (limit 8)
        M = by["Mid"]; self.assertEqual(M["unit_profit"], 10_399 - 207 - 10_001); self.assertEqual(M["qty_cap"], 999)  # cash 10m // 10,001
        self.assertEqual([x["score"] for x in r], sorted((x["score"] for x in r), reverse=True))
        self.assertEqual([x["name"] for x in r[:2]], ["Pricey", "Mid"])  # 193,999x1 > 191x999
        neg = sg.rank_quick([dict(c[0], high=c[0]["low"] + 1)], 10_000_000)  # margin eaten by tax
        self.assertEqual(neg, [])

    def test_allocation(self):
        mk = lambda i, buy, up, cap: {"id": i, "name": f"I{i}", "buy": buy, "unit_profit": up, "qty_cap": cap, "lo_frac": .5}
        ranked = [mk(i, 1000, 50 - i, 10_000) for i in range(12)]
        rows, tot = sg.allocate(ranked, 1_000_000, "Medium", slots=8, max_items=10)
        self.assertEqual(len(rows), 4); self.assertEqual(rows[-1]["cost"], 100_000)  # 30% cap: 3x300k then remaining 100k
        self.assertTrue(all(r["cost"] <= 300_000 for r in rows))
        small = [mk(i, 1000, 50 - i, 1000) for i in range(12)]          # limit/volume cap 1000 units
        rows, tot = sg.allocate(small, 10_000_000, "Medium", slots=8, max_items=10)
        self.assertEqual(len(rows), 8)                                      # slots bound
        rows, tot = sg.allocate(small, 10_000_000, "Medium", slots=8, max_items=5)
        self.assertEqual(len(rows), 5)                                      # item-count cap
        self.assertTrue(all(r["qty"] <= 1000 for r in rows))             # limit/volume cap
        self.assertLessEqual(tot["used"], 10_000_000); self.assertEqual(tot["used"] + tot["left"], 10_000_000)
        self.assertEqual(tot["profit_hi"], sum(r["qty"] * r["unit_profit"] for r in rows))
        self.assertEqual(tot["profit_lo"], sum(int(r["profit"] * .5) for r in rows))
        rows, _ = sg.allocate(ranked, 1_000_000, "High", slots=8, max_items=8)   # no diversification cap
        self.assertEqual(rows[0]["qty"], 1000); self.assertEqual(len(rows), 1)
        rows, _ = sg.allocate(ranked, 1_000_000, "Low", slots=8, max_items=8)
        self.assertTrue(all(r["share"] <= 0.2 + 1e-9 for r in rows)); self.assertEqual(len(rows), 5)
        rows, _ = sg.allocate(ranked, 1_000_000, "Medium", max_share=0.5)
        self.assertEqual(rows[0]["qty"], 500)
        self.assertEqual(sg.allocate([], 5_000, "Low")[0], [])
        self.assertEqual(sg.allocate([mk(1, 1000, 10, 5)], 10_000_000, "High")[0], [])   # 5k crumb < 1% of stack
        rows, _ = sg.allocate([mk(1, 5000, 10, 100)], 4_999, "High"); self.assertEqual(rows, [])  # can't afford one

    def daily(self, mids, vol=50_000):
        return [{"avgHighPrice": m, "avgLowPrice": m, "highPriceVolume": vol, "lowPriceVolume": vol} for m in mids]

    def test_daily_stats(self):
        self.assertIsNone(sg.daily_stats(self.daily([100] * 5)))
        d = sg.daily_stats(self.daily([100 + i for i in range(30)]))
        self.assertAlmostEqual(d["avg7"], 126); self.assertAlmostEqual(d["chg30"], 29.0); self.assertIsNone(d["recovery"])
        dip = sg.daily_stats(self.daily([100, 100, 90, 101, 100, 100, 90, 95, 96, 97, 100, 100]))
        self.assertEqual(dip["recovery"], 0.5)

    def cand(self, i, lo, hi, vol, chg=0.0, lim=1000):
        return {"id": i, "name": f"C{i}", "low": lo, "high": hi, "limit": lim, "vol": vol, "mid": (lo + hi) / 2,
                "mid_24h": lo * (1 - chg / 100), "chg24": chg, "age_min": 2, "margin": 0, "roi": 0}

    def test_overnight_rank(self):
        cs = [self.cand(1, 900, 910, 5000), self.cand(2, 960, 970, 5000), self.cand(3, 700, 710, 5000), self.cand(4, 1000, 1010, 5000)]
        daily = {1: sg.daily_stats(self.daily([1000] * 20 + [990, 1005] * 5)),    # 10% under avg, calm
                 2: sg.daily_stats(self.daily([1000] * 30)),                     # ~4% under
                 3: sg.daily_stats(self.daily([1000, 700] * 15)),                # wild -> filtered by vola cap
                 4: sg.daily_stats(self.daily([1000] * 30))}                     # not under avg
        r = sg.rank_overnight(cs, 10_000_000, "Medium", daily=daily)
        self.assertEqual([x["name"] for x in r], ["C1", "C2"])
        a = r[0]; self.assertLess(a["sell"], a["avg"]); self.assertGreater(a["sell"], a["buy"])
        self.assertEqual(a["unit_profit"], a["sell"] - sg.ge_tax(a["sell"]) - a["buy"])
        self.assertIn("under 7d avg", a["why"])
        r2 = sg.rank_overnight([self.cand(5, 950, 960, 5000, chg=-5)], 10_000_000, "High")   # no history -> 24h proxy
        self.assertEqual(len(r2), 1); self.assertIn("24h", r2[0]["why"]); self.assertEqual(r2[0]["confidence"], "Low")
        self.assertEqual(sg.rank_overnight([self.cand(5, 950, 960, 5000, chg=-5)], 10_000_000, "Medium", daily=daily,
                                           require_history=True), [])     # history fetched but not for this item
        sh = sg.shortlist([self.cand(1, 10, 11, 9000, chg=-20), self.cand(2, 50_000, 50_100, 900, chg=-15),
                           self.cand(3, 100, 101, 9000, chg=+5)], "Overnight-hold")
        self.assertEqual([x["id"] for x in sh], [2, 1])                   # dips only, weighted by gp liquidity

    def test_long_rank(self):
        cs = [self.cand(1, 1290, 1300, 5000), self.cand(2, 990, 1000, 5000), self.cand(3, 1000, 1010, 5000), self.cand(9, 1000, 1010, 5000)]
        daily = {1: sg.daily_stats(self.daily([1000 + 10 * i for i in range(30)])),       # steady +29% uptrend
                 2: sg.daily_stats(self.daily([1000 - 5 * i for i in range(30)])),        # downtrend
                 3: sg.daily_stats(self.daily([1050, 1000, 1060] * 10))}                 # dips that recover
        r = sg.rank_long(cs, 10_000_000, "Medium", daily=daily)
        names = [x["name"] for x in r]; self.assertIn("C1", names); self.assertNotIn("C2", names); self.assertNotIn("C9", names)
        up = r[names.index("C1")]; self.assertIn("30d", up["why"]); self.assertGreater(up["sell"], up["buy"])
        self.assertEqual(sg.rank_long(cs, 10_000_000, "Medium", daily={}), [])            # needs history

    def test_suggest_pipeline(self):
        items, lat, h1, old = self.world()
        for strat in sg.STRATEGIES:
            rows, tot = sg.suggest(items, lat, h1, old, 10_000_000, strat, "Medium", now=self.NOW)
            self.assertLessEqual(tot["used"], 10_000_000)
        rows, tot = sg.suggest(items, lat, h1, old, 10_000_000, "Quick flips", "Medium", now=self.NOW, slots=1)
        self.assertEqual(len(rows), 1); self.assertTrue(rows[0]["cost"] <= 3_000_000)


class Live(unittest.TestCase):
    def test_live(self):
        def out(*a):
            try: print(*a)
            except UnicodeEncodeError: print(*(ascii(x) if isinstance(x, str) else x for x in a))

        a = c.Api(tempfile.mkdtemp()); m = a.mapping(); self.assertGreater(len(m), 3000)
        lat, _ = a.latest(); self.assertIn("4151", lat)
        self.assertGreater(len(a.timeseries(4151, "1h")), 10); self.assertTrue(a.volumes_1h())
        r = c.best_flips({i["id"]: i for i in m}, lat, a.volumes_1h()); self.assertTrue(r)
        out("\nTop flips:", [(x["name"], x["margin"], round(x["roi"], 1)) for x in r[:5]])
        now = a.hourly_now(); old = a.hourly_at(__import__("time").time() - 25 * 3600)
        items = {i["id"]: i for i in m}; g, l = c.market_movers(items, now, old)
        self.assertTrue(g or l); tv, tval = c.most_traded(items, now); self.assertTrue(tv and tval)
        s = c.watch_summary(a.timeseries(4151, "1h"), name="Abyssal whip"); self.assertIsNotNone(s["vol"])
        out("\nGainers:", [(x["name"], round(x["chg"], 1)) for x in g[:3]], "Losers:", [(x["name"], round(x["chg"], 1)) for x in l[:3]])
        out("Top gp traded 1h:", [x["name"] for x in tval[:3]], "Whip 24h:", ascii(s))
        ts = a.timeseries(4151, "1h"); ta = c.timing_analysis(ts, "Abyssal whip", 70)
        b = ta["best"]; self.assertEqual(len(ta["profile"]), 24); self.assertGreater(len(a.timeseries(4151, "5m")), 50)
        out("\nWhip timing:", len(ts), "pts; buy", c.fmt_hour(b["buy_h"], b["window"]), "sell", c.fmt_hour(b["sell_h"], b["window"]),
              "peak", c.fmt_hour(b["peak_h"]), ta["roi"], ta["conf"], ascii(ta["why"]))
        rows, tot = sg.suggest(items, lat, now, old, 10_000_000, "Quick flips", "Medium")
        self.assertTrue(rows); self.assertLessEqual(tot["used"], 10_000_000)
        out("\nSuggestions 10m Medium Quick flips: used", tot["used"], "profit", tot["profit_lo"], "-", tot["profit_hi"])
        for r in rows[:5]:
            out("  ", r["name"], "buy", r["buy"], "sell", r["sell"], "qty", r["qty"], "cost", r["cost"], "profit", r["profit"],
                "roi %.2f%%" % r["roi"], r["hold"], r["confidence"], "|", r["why"])
        out("\nWhip:", lat["4151"], "margin", c.margin(lat["4151"]["high"], lat["4151"]["low"]))

if __name__ == "__main__":
    live = "--live" in sys.argv; sys.argv = [a for a in sys.argv if a != "--live"]
    suite = unittest.TestLoader().loadTestsFromTestCase(T)
    suite.addTests(unittest.TestLoader().loadTestsFromTestCase(Flips))
    suite.addTests(unittest.TestLoader().loadTestsFromTestCase(Analytics))
    suite.addTests(unittest.TestLoader().loadTestsFromTestCase(Timing))
    suite.addTests(unittest.TestLoader().loadTestsFromTestCase(Theme))
    suite.addTests(unittest.TestLoader().loadTestsFromTestCase(Suggest))
    suite.addTests(unittest.TestLoader().loadTestsFromTestCase(UX))
    if live: suite.addTests(unittest.TestLoader().loadTestsFromTestCase(Live))
    sys.exit(not unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful())
