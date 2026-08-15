# Ticker CLI design

`ticker` gives terminal-first users a quick, indicative stock quote without an
account, API key, or runtime dependency.

## Product behavior

- Query one or more symbols: `ticker AAPL MSFT`
- Remember successful queries and show a recency-sorted portfolio with `ticker`
- Highlight one portfolio row in interactive terminals with keyboard navigation
- Show price, daily change, market state, and quote age
- Add separate close and extended-hours columns outside the regular session
- Render colored `1d`, `1w`, `1m`, `3m`, and `ytd` trend sparklines
- Provide stable agent output with `--json`
- Fetch market data on every invocation so the displayed quote is current

Yahoo Finance's unofficial chart endpoint is the initial no-key provider. The
provider is isolated so it can be replaced. Recent symbols—not prices—are
stored locally. Quotes are informational and the
tool does not promise exchange-grade real-time data.

The CLI is the product API. Codex and Claude skills call `ticker --json` rather
than duplicating data-fetching logic.

Yahoo endpoint research is maintained separately in
[`docs/YAHOO_FINANCE_ENDPOINTS.md`](docs/YAHOO_FINANCE_ENDPOINTS.md).

## Planned analysis depth

Each view answers a different-depth question while remaining nested:

```text
Glance ⊂ Snapshot ⊂ Study
```

```text
ticker                 portfolio comparison
ticker AAPL            Glance: should I care?
ticker AAPL --more     Snapshot: is this a good company or stock?
ticker AAPL --study    Study: let me analyze it
```

The Set 1 portfolio table is implemented. The single-symbol Glance view and
the deeper Snapshot and Study views remain planned.

### Glance

A fast seven-metric health check:

```text
PRICE        $305.77  post
TODAY         +0.22%
1M            -6.80%
YTD          +12.40%
MARKET CAP     $4.5T
P/E            35.2x
REV GROWTH      +6.1%
```

- Price is the latest available trade, including pre- and post-market.
- Today compares the latest price with the previous regular close.
- 1M and YTD use regular-session closing prices.
- P/E is trailing twelve months.
- Revenue growth is the latest reported quarter versus the same quarter a year
  earlier.

### Snapshot

A balanced investment view grouped by question:

- Market: price, today, 1M, YTD, 52-week range, and market cap
- Business: revenue, revenue growth, EPS, and EPS growth
- Quality: operating margin, free cash flow, and FCF margin
- Valuation: trailing P/E and forward P/E
- Catalyst: next earnings date

The 52-week position should be visual rather than another isolated number:

```text
52W RANGE    $169.21 ├────────────●──┤ $312.50   95%
```

### Study

A deeper view organized for interpretation, capped around 25–28 metrics:

- Performance: today, 1W, 1M, 3M, YTD, 1Y, 52-week range, volume, average
  volume, and relative volume
- Growth: revenue, quarterly YoY growth, three-year CAGR, EPS, and EPS growth
- Quality: gross, operating, net, and FCF margins; free cash flow; and ROIC
- Balance sheet: cash, debt, and net cash/debt
- Valuation: market cap, trailing and forward P/E, price/sales, and FCF yield
- Catalyst: next earnings date

### Data and interaction rules

- Use `N/M` when a ratio is not meaningful, such as P/E with negative earnings.
- Use `N/A` when data is unavailable.
- Label estimates, especially forward P/E and earnings dates.
- Include reporting bases and periods, such as `$124.3B TTM`, `+6.1% YoY`, or
  `Oct 29 estimated`.
- Distinguish live quote freshness from quarterly fundamental freshness.
- Load progressively: price and returns first, basic fundamentals second, and
  study metrics last.
- Keep the portfolio optimized for comparison; analysis depth applies to the
  single-symbol view.
