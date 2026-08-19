from __future__ import annotations

import argparse
import curses
import json
import shutil
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass

from . import __version__
from .core import PERIODS, Quote, QuoteError, Trend, YahooProvider, get_market_data
from .fundamentals import Fundamentals, FundamentalsProvider
from .identity import initialize_device_identity, sync_device_watchlist
from .portfolio import Portfolio, PortfolioEntry
from .profiler import Profiler
from .render import Color, age_text, change_text, compact_number, money, pe_text, percent, sparkline, trend_line
from .tui import PortfolioRow, run_progressive_portfolio
from .update import check_for_update, prompt_for_update


CHART_PERIODS = [*PERIODS, "all"]


@dataclass
class Result:
    quote: Quote
    trends: dict[str, Trend]
    last_queried: int | None = None
    fundamentals: Fundamentals | None = None

    def to_dict(self) -> dict[str, object]:
        value: dict[str, object] = self.quote.to_dict()
        if len(self.trends) == 1:
            value["trend"] = next(iter(self.trends.values())).to_dict()
        elif self.trends:
            value["trends"] = {period: trend.to_dict() for period, trend in self.trends.items()}
        if self.last_queried is not None:
            value["last_queried"] = self.last_queried
        if self.fundamentals is not None:
            value["fundamentals"] = self.fundamentals.to_dict()
        return value


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        prog="ticker", description="Raise financial awareness without making developers switch context."
    )
    result.add_argument("symbol", nargs="?", help="ticker symbol to add")
    result.add_argument("--json", action="store_true", help=argparse.SUPPRESS)
    result.add_argument("--compact", action="store_true", help=argparse.SUPPRESS)
    result.add_argument(
        "--chart",
        nargs="?",
        const="1m",
        choices=CHART_PERIODS,
        metavar="PERIOD",
        help=argparse.SUPPRESS,
    )
    result.add_argument("--no-chart", action="store_true", help=argparse.SUPPRESS)
    result.add_argument("--ascii", action="store_true", help=argparse.SUPPRESS)
    result.add_argument(
        "--color",
        choices=["auto", "always", "never"],
        default="auto",
        help=argparse.SUPPRESS,
    )
    result.add_argument(
        "-p",
        "--print",
        dest="no_interactive",
        action="store_true",
        help="print output without opening interactive mode",
    )
    result.add_argument(
        "--no-interactive",
        dest="no_interactive",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    result.add_argument("--profile", action="store_true", help=argparse.SUPPRESS)
    result.add_argument("--profile-json", metavar="PATH", help=argparse.SUPPRESS)
    result.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return result


def main(argv: list[str] | None = None) -> int:
    started_at = time.perf_counter()
    args = parser().parse_args(argv)
    profiler = Profiler(args.profile or bool(args.profile_json), started_at)
    if (
        args.symbol is None
        and not args.no_interactive
        and not profiler.enabled
        and sys.stdin.isatty()
        and sys.stdout.isatty()
    ):
        update = check_for_update(__version__)
        if update is not None:
            if prompt_for_update(update):
                return 0
    try:
        return run(args, profiler)
    finally:
        sync_device_watchlist([entry.symbol for entry in Portfolio().entries()])
        if args.profile:
            profiler.write_report()
        if args.profile_json:
            profiler.write_json(args.profile_json)


def run(args: argparse.Namespace, profiler: Profiler) -> int:
    initialize_device_identity()
    if args.chart and args.no_chart:
        print("ticker: --chart and --no-chart cannot be used together", file=sys.stderr)
        return 2

    portfolio = Portfolio()
    portfolio_mode = args.symbol is None
    with profiler.span("load_portfolio"):
        entries = portfolio.entries() if portfolio_mode else []
    if portfolio_mode and not entries:
        empty_interactive = (
            args.symbol is None
            and not args.json
            and not args.no_interactive
            and not profiler.enabled
            and sys.stdin.isatty()
            and sys.stdout.isatty()
        )
        if empty_interactive:
            try:
                run_progressive_portfolio(
                    [], None if args.no_chart else (args.chart or "1m"), args.ascii,
                    YahooProvider(profiler=profiler),
                    FundamentalsProvider(profiler=profiler),
                    portfolio, portfolio.preferences(),
                )
                return 0
            except curses.error:
                pass
        print("[]" if args.json else "No recent symbols. Try: ticker AAPL")
        return 0

    symbols = [entry.symbol for entry in entries] if portfolio_mode else normalize([args.symbol])
    if symbols is None:
        return 2
    period = None if args.no_chart else (args.chart or ("1m" if portfolio_mode else None))
    provider = YahooProvider(profiler=profiler)
    fundamentals_provider = FundamentalsProvider(profiler=profiler)
    entry_map = {entry.symbol: entry for entry in entries}
    interactive = (
        portfolio_mode
        and args.symbol is None
        and not args.json
        and not args.no_interactive
        and not profiler.enabled
        and period != "all"
        and sys.stdin.isatty()
        and sys.stdout.isatty()
    )
    if interactive:
        try:
            run_progressive_portfolio(
                entries, period, args.ascii, provider, fundamentals_provider,
                portfolio, portfolio.preferences(),
            )
            return 0
        except curses.error:
            pass

    results: list[Result] = []
    successful_explicit: list[str] = []
    failed = False
    def fetch_one(symbol: str) -> Result:
        with profiler.span("fetch_symbol", symbol=symbol, period=period):
            return fetch(symbol, period, provider)

    fetched: dict[str, Result | QuoteError] = {}
    if len(symbols) > 1:
        with ThreadPoolExecutor(max_workers=min(4, len(symbols))) as executor:
            futures = {executor.submit(fetch_one, symbol): symbol for symbol in symbols}
            for future in as_completed(futures):
                symbol = futures[future]
                try:
                    fetched[symbol] = future.result()
                except QuoteError as error:
                    fetched[symbol] = error
    else:
        try:
            fetched[symbols[0]] = fetch_one(symbols[0])
        except QuoteError as error:
            fetched[symbols[0]] = error

    for symbol in symbols:
        try:
            value = fetched[symbol]
            if isinstance(value, QuoteError):
                raise value
            result = value
            if symbol in entry_map:
                result.last_queried = entry_map[symbol].last_queried
            results.append(result)
            if not portfolio_mode:
                successful_explicit.append(result.quote.symbol)
        except QuoteError as error:
            print(f"ticker: {symbol}: {error}", file=sys.stderr)
            failed = True

    if portfolio_mode and results:
        with ThreadPoolExecutor(max_workers=min(4, len(results) * 2)) as executor:
            additions = {}
            for result in results:
                if "1m" not in result.trends:
                    additions[executor.submit(provider.get_trend, result.quote.symbol, "1m")] = (
                        result,
                        "1m",
                    )
                if "ytd" not in result.trends:
                    additions[executor.submit(provider.get_trend, result.quote.symbol, "ytd")] = (
                        result,
                        "ytd",
                    )
                additions[
                    executor.submit(
                        fundamentals_provider.get,
                        result.quote.symbol,
                        result.quote.latest_price,
                    )
                ] = (result, "fundamentals")
            for future in as_completed(additions):
                result, kind = additions[future]
                try:
                    if kind in {"1m", "ytd"}:
                        result.trends[kind] = future.result()
                    else:
                        result.fundamentals = future.result()
                except (QuoteError, OSError, ValueError):
                    pass

    if successful_explicit:
        portfolio.touch(successful_explicit)

    color = Color(args.color)
    with profiler.span("render"):
        if args.json:
            values = [result.to_dict() for result in results]
            output = values[0] if len(symbols) == 1 and values else values
            print(json.dumps(output, indent=2))
        elif period == "all":
            for index, result in enumerate(results):
                if index:
                    print()
                print_detail(result, color, args.ascii, args.compact)
        elif portfolio_mode or len(symbols) > 1:
            print_table(results, period, color, args.ascii)
        elif results:
            print_detail(results[0], color, args.ascii, args.compact)
    return 1 if failed else 0


def normalize(values: list[str]) -> list[str] | None:
    symbols: list[str] = []
    for raw in values:
        symbol = raw.strip().upper()
        if not symbol or len(symbol) > 32 or any(c.isspace() for c in symbol):
            print(f"ticker: invalid symbol: {raw!r}", file=sys.stderr)
            return None
        symbols.append(symbol)
    return list(dict.fromkeys(symbols))


def fetch(
    symbol: str,
    period: str | None,
    provider: YahooProvider,
) -> Result:
    periods = list(PERIODS) if period == "all" else [period]
    quote: Quote | None = None
    trends: dict[str, Trend] = {}
    for selected in periods:
        fetched_quote, trend = get_market_data(symbol, provider, selected)
        quote = fetched_quote if quote is None else quote
        if trend:
            trends[selected] = trend
    if quote is None:
        quote, _ = get_market_data(symbol, provider, None)
    return Result(quote, trends)


def print_table(results: list[Result], period: str | None, color: Color, ascii_only: bool) -> None:
    if not results:
        return
    terminal_width = shutil.get_terminal_size((120, 24)).columns
    chart_period = period if period and period != "all" else "1m"
    has_extended = any(result.quote.extended_hours for result in results)
    chart_width = max(8, min(14, terminal_width - (87 if has_extended else 60)))
    show_cap = terminal_width >= (119 if has_extended else 92)
    show_pe = terminal_width >= (129 if has_extended else 102)
    show_growth = terminal_width >= (140 if has_extended else 114)
    if has_extended:
        sessions = {
            result.quote.extended_hours.session
            for result in results
            if result.quote.extended_hours
        }
        extended_header = "PRE-MARKET" if sessions == {"pre"} else "AFTER HOURS"
        change_header = "PRE %" if sessions == {"pre"} else "AFTER %"
        header = (
            f"{'SYMBOL':<8} {'CLOSE':>12}  {extended_header:>12}  "
            f"{change_header:>9}  {'TODAY':>9}  "
        )
    else:
        header = f"{'SYMBOL':<8} {'PRICE':>12}  {'TODAY':>9}  "
    header += f"{'1M TREND':<{chart_width}}  {'1M':>8}  {'YTD':>8}"
    if show_cap:
        header += f"  {'MKT CAP':>10}"
    if show_pe:
        header += f"  {'P/E':>8}"
    if show_growth:
        header += f"  {'REV YOY':>9}"
    print(header)
    for result in results:
        quote = result.quote
        one_month = result.trends.get("1m") or result.trends.get(chart_period)
        ytd = result.trends.get("ytd")
        chart_text = sparkline(one_month.points, chart_width, ascii_only) if one_month else "N/A"
        chart_change = one_month.change_percent if one_month else None
        today = quote.latest_change_percent
        line = f"{quote.symbol:<8} "
        if has_extended:
            extended = quote.extended_hours
            extended_price = money(extended.price, quote.currency) if extended else "—"
            extended_change = extended.change_percent if extended else None
            line += (
                f"{money(quote.price, quote.currency):>12}  {extended_price:>12}  "
                f"{color.change(f'{percent(extended_change):>9}', extended_change)}  "
            )
        else:
            line += f"{money(quote.latest_price, quote.currency):>12}  "
        line += (
            f"{color.change(f'{percent(today):>9}', today)}  "
            f"{color.change(f'{chart_text:<{chart_width}}', chart_change)}  "
            f"{color.change(f'{percent(chart_change):>8}', chart_change)}  "
            f"{color.change(f'{percent(ytd.change_percent if ytd else None):>8}', ytd.change_percent if ytd else None)}"
        )
        fundamentals = result.fundamentals
        if show_cap:
            line += f"  {compact_number(fundamentals.market_cap if fundamentals else None, quote.currency):>10}"
        if show_pe:
            earnings_available = fundamentals is not None and (
                fundamentals.pe_ratio is not None or fundamentals.trailing_eps is not None
            )
            line += f"  {pe_text(fundamentals.pe_ratio if fundamentals else None, earnings_available):>8}"
        if show_growth:
            growth = fundamentals.revenue_growth if fundamentals else None
            line += f"  {color.change(f'{percent(growth):>9}', growth)}"
        print(line)
def print_detail(result: Result, color: Color, ascii_only: bool, compact: bool) -> None:
    quote = result.quote
    change = color.change(change_text(quote), quote.change_percent)
    if compact:
        if quote.extended_hours:
            extended = quote.extended_hours
            extended_change = color.change(percent(extended.change_percent), extended.change_percent)
            label = "PM" if extended.session == "pre" else "AH"
            print(
                f"{quote.symbol:<8} {money(quote.price, quote.currency):>12} → "
                f"{money(extended.price, quote.currency)} {label}  {extended_change}"
            )
        else:
            print(f"{quote.symbol:<8} {money(quote.price, quote.currency):>12}  {change}")
        return
    print(f"{quote.symbol}  {quote.name}")
    if quote.extended_hours:
        extended = quote.extended_hours
        extended_change = color.change(
            f"{extended.change:+.2f} ({extended.change_percent:+.2f}%)",
            extended.change_percent,
        )
        label = "Pre-market" if extended.session == "pre" else "After hours"
        print(f"Regular close  {money(quote.price, quote.currency)}  {change}")
        print(f"{label:<13} {money(extended.price, quote.currency)}  {extended_change}")
    else:
        print(f"{money(quote.price, quote.currency)}  {change}")
    print(f"{quote.latest_session} · updated {age_text(quote.latest_timestamp)} ago")
    if result.trends:
        print()
        width = max(12, min(32, shutil.get_terminal_size((80, 24)).columns - 28))
        for trend in result.trends.values():
            print(trend_line(trend, color, width, ascii_only))


if __name__ == "__main__":
    raise SystemExit(main())
