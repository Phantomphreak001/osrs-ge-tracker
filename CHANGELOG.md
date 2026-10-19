# Changelog

## 0.8.0
- Open flips: log a buy now ("Log a buy (sell later)" on My Flips, the item page, or right-click → "Log a buy…"),
  then press "Sold…" when it sells. Supports partial sales (the rest stays open), edit and delete.
- Open flips list shows total cost, time held, the live sell price and estimated profit after GE tax.
- Open flips are saved in `open_flips.json`; `flips.json` keeps its existing format so older versions can still read it.
- A corrupt `flips.json` is now copied aside (`.corrupt-<time>.bak`) instead of being overwritten.

## 0.7.0
- UX pass: new Home tab, item view, flip finder with advanced options, tips and offline state.
- Settings file migration (`settings_version: 2`, automatic backup of the old file).
- Item icons fetched in the background and cached.

## 0.6
- Suggestions tab: quick flips, overnight holds and longer-term investment ideas.

## 0.5
- Light and dark themes.

## 0.4
- Timing / ROI view: price by hour of day (US Eastern, DST-aware) and holding-period returns.

## 0.3
- Analytics tab: market movers, watchlist summary, deep-dive chart with SMAs, flip log.

## 0.2
- Best-flips ticker.

## 0.1
- Item search, watchlist with margin after GE tax, price chart and price alerts.
