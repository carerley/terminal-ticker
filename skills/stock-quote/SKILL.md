---
name: stock-quote
description: Check stock or ETF quotes, recent-symbol portfolios, and price trends with the local ticker CLI. Use when a user asks for a current price, daily change, previous close, market state, remembered symbols, portfolio view, or 1-day through year-to-date chart.
---

# Stock Quote

Run the installed CLI as the deterministic quote backend:

```bash
ticker SYMBOL --json
```

For multiple symbols, pass them in one call:

```bash
ticker AAPL MSFT --json
```

Run `ticker --json` to retrieve the user's recency-sorted symbol portfolio.
Use `--chart 1d|1w|1m|3m|ytd|all` when trend history is requested.

Parse the JSON and report the symbol, name, price and currency, daily change and
percentage, market state, timestamp, and `extended_hours` data when present.
Keep regular-session and extended-hours changes distinct. Treat the result as
indicative market information, not investment advice.

If `ticker` is unavailable, explain that the project CLI must be installed; do
not substitute an unrelated or authenticated market-data service.
