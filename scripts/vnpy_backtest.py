#!/usr/bin/env python3
"""Headless VeighNa (vnpy) CTA backtest pilot.

Fetches daily bars for a symbol (default: AAPL) from the direct Yahoo
chart API (no yfinance), saves them as CSV, loads them into vnpy
``BarData`` objects and runs ``BacktestingEngine`` from
``vnpy_ctastrategy.backtesting`` with a built-in strategy.

Strategy used: ``DoubleMaStrategy``
(``vnpy_ctastrategy.strategies.double_ma_strategy``, default setting
fast_window=10 / slow_window=20).

Why not the Bollinger strategy: in current vnpy_ctastrategy the class is
``BollChannelStrategy`` (file ``boll_channel_strategy.py``) and it first
aggregates incoming bars into 15-minute bars via ``BarGenerator``,
which is designed for intraday data and is a poor fit for daily bars.
``DoubleMaStrategy`` consumes daily bars directly.

How the data reaches the engine: ``BacktestingEngine.load_data()``
clears ``history_data`` and reloads from vnpy's local database, so this
script instead assigns the parsed bars directly to
``engine.history_data`` and calls ``run_backtesting()``. That is the
documented attribute the replay loop iterates over.

Outputs (in ``results/``):
- ``<SYMBOL>_daily.csv``  -- the bars exactly as fetched
- ``statistics.json``     -- the FULL dict returned by
  ``engine.calculate_statistics()``, serialised verbatim
- ``vnpy_result.md``      -- strategy, window and headline statistics
"""

import argparse
import csv
import json
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

NY_TZ = ZoneInfo("America/New_York")

DEFAULT_SYMBOL = "AAPL"
DEFAULT_START = "2023-11-01"
DEFAULT_END = "2026-10-31"
DEFAULT_CAPITAL = 1_000_000


def fetch_yahoo_daily(symbol: str, start: date, end: date, csv_path: Path) -> list[dict]:
    """Fetch daily bars from Yahoo's chart API and save them as CSV."""
    period1 = int(datetime.combine(start, time.min, tzinfo=NY_TZ).timestamp())
    # period2 is exclusive, so ask for one day past the requested end date.
    period2 = int(
        datetime.combine(end + timedelta(days=1), time.min, tzinfo=NY_TZ).timestamp()
    )
    url = (
        f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
        f"?period1={period1}&period2={period2}&interval=1d"
    )
    print(f"Fetching {url}")
    resp = requests.get(
        url,
        headers={"User-Agent": "Mozilla/5.0 (vnpy-runner research script)"},
        timeout=30,
    )
    resp.raise_for_status()
    payload = resp.json()

    chart = payload.get("chart") or {}
    if chart.get("error"):
        raise RuntimeError(f"Yahoo chart API error: {chart['error']}")
    result = chart["result"][0]
    timestamps = result.get("timestamp") or []
    quote = result["indicators"]["quote"][0]

    rows: list[dict] = []
    for i, ts in enumerate(timestamps):
        close = quote["close"][i]
        if close is None:
            continue
        dt = datetime.fromtimestamp(ts, tz=NY_TZ)
        rows.append(
            {
                "date": dt.date().isoformat(),
                "datetime": dt,
                "open": quote["open"][i],
                "high": quote["high"][i],
                "low": quote["low"][i],
                "close": close,
                "volume": quote["volume"][i] or 0,
            }
        )

    if not rows:
        raise RuntimeError("Yahoo chart API returned no usable bars")

    csv_path.parent.mkdir(parents=True, exist_ok=True)
    with csv_path.open("w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["date", "open", "high", "low", "close", "volume"])
        for r in rows:
            writer.writerow([r["date"], r["open"], r["high"], r["low"], r["close"], r["volume"]])
    print(f"Saved {len(rows)} bars to {csv_path} ({rows[0]['date']} .. {rows[-1]['date']})")
    return rows


def to_bars(rows: list[dict], symbol: str):
    """Convert fetched rows into vnpy BarData objects (Exchange.NASDAQ)."""
    from vnpy.trader.constant import Exchange, Interval
    from vnpy.trader.object import BarData

    bars = []
    for r in rows:
        bars.append(
            BarData(
                gateway_name="YAHOO",
                symbol=symbol,
                exchange=Exchange.NASDAQ,
                datetime=r["datetime"],
                interval=Interval.DAILY,
                volume=float(r["volume"]),
                turnover=float(r["close"]) * float(r["volume"]),
                open_price=float(r["open"]),
                high_price=float(r["high"]),
                low_price=float(r["low"]),
                close_price=float(r["close"]),
            )
        )
    return bars


def clean_value(value):
    """Make a statistics value JSON-serialisable without changing it."""
    if hasattr(value, "item"):  # numpy scalar -> native Python scalar
        try:
            value = value.item()
        except Exception:
            pass
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return value


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("symbol", nargs="?", default=DEFAULT_SYMBOL)
    parser.add_argument("--start", default=DEFAULT_START, help="YYYY-MM-DD")
    parser.add_argument("--end", default=DEFAULT_END, help="YYYY-MM-DD")
    parser.add_argument("--capital", type=int, default=DEFAULT_CAPITAL)
    args = parser.parse_args()

    symbol = args.symbol.upper()
    start = date.fromisoformat(args.start)
    end = date.fromisoformat(args.end)

    results_dir = Path("results")
    results_dir.mkdir(parents=True, exist_ok=True)

    rows = fetch_yahoo_daily(symbol, start, end, results_dir / f"{symbol}_daily.csv")
    bars = to_bars(rows, symbol)

    # Imported here so the data-fetch failure modes stay easy to read
    # in the log before the (heavy) vnpy import chain runs.
    from vnpy.trader.constant import Interval
    from vnpy_ctastrategy.backtesting import BacktestingEngine
    from vnpy_ctastrategy.strategies.double_ma_strategy import DoubleMaStrategy

    engine = BacktestingEngine()
    engine.set_parameters(
        vt_symbol=f"{symbol}.NASDAQ",
        interval=Interval.DAILY,
        start=datetime.combine(start, time.min, tzinfo=NY_TZ),
        end=datetime.combine(end, time.min, tzinfo=NY_TZ),
        rate=0.0,
        slippage=0.0,
        size=1,
        pricetick=0.01,
        capital=args.capital,
    )
    engine.add_strategy(DoubleMaStrategy, {})
    # load_data() would clear this and reload from vnpy's local DB;
    # assigning history_data directly is how the replay gets our CSV.
    engine.history_data = bars
    engine.run_backtesting()
    engine.calculate_result()
    statistics = engine.calculate_statistics()

    if not statistics:
        raise RuntimeError("calculate_statistics() returned an empty dict")

    cleaned = {key: clean_value(value) for key, value in statistics.items()}
    stats_path = results_dir / "statistics.json"
    stats_path.write_text(json.dumps(cleaned, indent=2) + "\n")
    print(f"Wrote {stats_path}: {cleaned}")

    md = f"""# VeighNa (vnpy) backtest result

- Symbol: {symbol} (Exchange.NASDAQ, daily bars, Yahoo chart API)
- Strategy: DoubleMaStrategy (built-in, vnpy_ctastrategy.strategies.double_ma_strategy; default setting fast_window=10, slow_window=20)
- Window: {start.isoformat()} to {end.isoformat()}
- Capital: {args.capital}
- Bars loaded: {len(bars)}

## Headline statistics (exactly as returned by calculate_statistics())

- total_return: {cleaned.get('total_return')}
- sharpe_ratio: {cleaned.get('sharpe_ratio')}
- max_drawdown: {cleaned.get('max_drawdown')}

Also from the same dict: max_ddpercent: {cleaned.get('max_ddpercent')},
end_balance: {cleaned.get('end_balance')},
total_trade_count: {cleaned.get('total_trade_count')}.

The full statistics dict is in `statistics.json`. Note the built-in
DoubleMaStrategy trades a fixed volume of 1, so with size=1 the PnL
scale is that of a single share; the statistics are the engine's own
output for that setting, not a sized portfolio allocation.
"""
    result_path = results_dir / "vnpy_result.md"
    result_path.write_text(md)
    print(f"Wrote {result_path}")


if __name__ == "__main__":
    main()
