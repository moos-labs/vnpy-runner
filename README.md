# vnpy-runner

Run a [VeighNa (vnpy)](https://github.com/vnpy/vnpy) CTA backtest in
GitHub Actions and read the result from the workflow artifact.
Market-agnostic pattern: symbol, window and capital are script
arguments (the pilot run uses the defaults below).

## Pattern

- Public repo, one workflow: `.github/workflows/run.yml`
- Trigger is `workflow_dispatch` **only**
- Python 3.11 via `actions/setup-python`, dependencies from
  `requirements.txt` (`vnpy`, `vnpy_ctastrategy`, `vnpy_sqlite`,
  `requests`)
- The step named **Run commands** runs exactly `${{ vars.COMMANDS }}`
- The final step uploads `results/` as the `results` artifact with
  `actions/upload-artifact@v4` and `if: always()`
- Commands live in the repo Actions variable `COMMANDS`:

  ```
  mkdir -p results
  set -o pipefail; python scripts/vnpy_backtest.py 2>&1 | tee results/run.log
  ```

  (`set -o pipefail` matters: without it, piping through `tee` would
  mask a failing script as a successful run.)

## Pilot run

- Script: `scripts/vnpy_backtest.py` (no arguments = the pilot below)
- Symbol: `AAPL` daily bars, fetched by the script itself from the
  direct Yahoo chart API
  (`https://query1.finance.yahoo.com/v8/finance/chart/AAPL?period1=...&period2=...&interval=1d`
  via `requests`) and saved as `results/AAPL_daily.csv`. No yfinance
  is used anywhere in this repo.
- Engine: `BacktestingEngine` from `vnpy_ctastrategy.backtesting`
- Strategy: **`DoubleMaStrategy`** — a built-in strategy in
  `vnpy_ctastrategy.strategies` (default fast_window=10,
  slow_window=20)
- Window: 2023-11-01 to 2026-10-31 (~3 years)
- Capital: 1,000,000; size=1, pricetick=0.01, rate=0, slippage=0
- Outputs: `results/statistics.json` (the full dict returned by
  `calculate_result()` + `calculate_statistics()`),
  `results/vnpy_result.md` (strategy, window, headline
  `total_return` / `sharpe_ratio` / `max_drawdown`) and
  `results/run.log`.

## Honest notes on VeighNa

- VeighNa is a Chinese quant trading platform and is **GUI-first**:
  its normal entry point is a Qt desktop app, and the official
  backtester app (`vnpy_ctabacktester`) is a GUI widget. None of that
  is needed here — the CTA backtesting engine itself
  (`vnpy_ctastrategy.backtesting.BacktestingEngine`) runs fully
  headless, which is all this repo installs and uses.
  `vnpy_ctabacktester` is therefore deliberately **not** in
  `requirements.txt`.
- Installing `vnpy` still pulls its GUI dependencies (PySide6,
  pyqtgraph, …) because they are hard requirements of the `vnpy`
  package, even though a headless run never opens a window.
- Database driver: vnpy 4.x split its database drivers into separate
  packages and `get_database()` defaults to SQLite via `vnpy_sqlite`.
  A strategy's `on_init()` calls `load_bar(...)`, which goes through
  `get_database()` even in a backtest — without `vnpy_sqlite` the
  first run failed with `ModuleNotFoundError: No module named
  'vnpy_sqlite'`. It is in `requirements.txt` for exactly that call;
  it returns no pre-start bars, which is fine.
- Data hand-off: `BacktestingEngine.load_data()` clears
  `history_data` and reloads bars from vnpy's own local database, so
  the script assigns the parsed `BarData` list directly to
  `engine.history_data` and then calls `run_backtesting()` — the
  replay loop iterates exactly that attribute.
- Strategy choice: the Bollinger strategy in current
  `vnpy_ctastrategy` is named `BollChannelStrategy`
  (`boll_channel_strategy.py`) and aggregates incoming bars into
  15-minute bars first — built for intraday data, a poor fit for
  daily bars. `DoubleMaStrategy` consumes daily bars directly, so it
  is the pilot strategy.
- Sizing caveat: the built-in `DoubleMaStrategy` trades a fixed
  volume of 1, so with `size=1` the PnL scale is that of a single
  share against 1,000,000 capital. The statistics are quoted
  verbatim from the engine for that setting — they demonstrate the
  engine end-to-end, they are not a sized portfolio backtest.
- Yahoo's chart API intermittently returns HTTP 429 from GitHub
  runner IPs; re-dispatching the unchanged workflow usually succeeds.
