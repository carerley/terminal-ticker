from __future__ import annotations

import argparse
import json
import sys
import time

from . import __version__
from .core import Cache, Quote, QuoteError, YahooProvider, get_quote


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        prog="ticker", description="Quick no-key stock quotes for the terminal"
    )
    result.add_argument("symbols", nargs="+", help="ticker symbols, such as AAPL or MSFT")
    result.add_argument("--json", action="store_true", help="emit stable JSON")
    result.add_argument("--compact", action="store_true", help="print one compact line per symbol")
    result.add_argument("--no-cache", action="store_true", help="bypass the local quote cache")
    result.add_argument("--cache-ttl", type=int, default=30, metavar="SECONDS")
    result.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    if args.cache_ttl < 0:
        print("ticker: --cache-ttl must be non-negative", file=sys.stderr)
        return 2

    quotes: list[Quote] = []
    failed = False
    provider, cache = YahooProvider(), Cache()
    for raw_symbol in args.symbols:
        symbol = raw_symbol.strip().upper()
        if not symbol or len(symbol) > 32 or any(c.isspace() for c in symbol):
            print(f"ticker: invalid symbol: {raw_symbol!r}", file=sys.stderr)
            failed = True
            continue
        try:
            quotes.append(
                get_quote(symbol, provider, cache, args.cache_ttl, not args.no_cache)
            )
        except QuoteError as error:
            print(f"ticker: {symbol}: {error}", file=sys.stderr)
            failed = True

    if args.json:
        values = [quote.to_dict() for quote in quotes]
        output = values[0] if len(args.symbols) == 1 and values else values
        print(json.dumps(output, indent=2))
    elif args.compact or len(args.symbols) > 1:
        for quote in quotes:
            print(compact_line(quote))
    else:
        for quote in quotes:
            print(detail(quote))
    return 1 if failed else 0


def compact_line(quote: Quote) -> str:
    price = money(quote.price, quote.currency)
    suffix = " · stale cache" if quote.stale else " · cached" if quote.cached else ""
    return f"{quote.symbol:<8} {price:>12}  {change_text(quote)}{suffix}"


def detail(quote: Quote) -> str:
    age = max(0, int(time.time()) - quote.timestamp)
    age_text = f"updated {age}s ago" if age < 60 else f"updated {age // 60}m ago"
    cache_text = " · stale cache" if quote.stale else " · cached" if quote.cached else ""
    return (
        f"{quote.symbol}  {quote.name}\n"
        f"{money(quote.price, quote.currency)}  {change_text(quote)}\n"
        f"{quote.market_state.lower()} · {age_text}{cache_text}"
    )


def money(value: float, currency: str) -> str:
    prefix = "$" if currency == "USD" else ""
    suffix = "" if currency == "USD" or not currency else f" {currency}"
    return f"{prefix}{value:,.2f}{suffix}"


def change_text(quote: Quote) -> str:
    if quote.change is None or quote.change_percent is None:
        return "change unavailable"
    return f"{quote.change:+.2f} ({quote.change_percent:+.2f}%)"


if __name__ == "__main__":
    raise SystemExit(main())
