---
name: stock-quote
description: Check the latest available stock or ETF price with the local ticker CLI. Use when a user asks for a quote, current stock price, daily price change, previous close, or market state for one or more ticker symbols.
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

Parse the JSON and report the symbol, name, price and currency, daily change and
percentage, market state, and timestamp. Clearly say when `cached` or `stale` is
true. Treat the result as indicative market information, not investment advice.

If `ticker` is unavailable, explain that the project CLI must be installed; do
not substitute an unrelated or authenticated market-data service.
