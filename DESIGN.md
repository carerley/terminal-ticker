# Ticker design

## Design core

Ticker is a terminal app for raising financial awareness without making
developers switch context.

It keeps the market visible from the environment where developers already
work. A check should take seconds, require no browser, account, API key, or
workflow change, and leave the user better oriented—not buried in financial
data.

### Product principles

- **Awareness before analysis.** Lead with the few signals that explain what
  changed and whether it deserves attention. Offer deeper detail progressively.
- **No context switch.** The complete everyday experience lives in the
  terminal and composes naturally with shell workflows and coding agents.
- **Glanceable by default.** Optimize hierarchy, density, color, and latency
  for repeated brief checks rather than long research sessions.
- **Quiet when nothing matters.** Make meaningful movement easy to spot without
  turning normal market noise into alerts or advice.
- **Trust through clarity.** Show market state, quote age, reporting periods,
  estimates, and unavailable data explicitly. Never imply trading-grade
  precision.
- **Useful immediately.** Installation and first use require no account,
  configuration, API key, or runtime dependency.

### Core loop

```text
Notice → Glance → Decide whether to go deeper → Return to work
```

The portfolio is the ambient awareness surface. A symbol view explains one
company at progressively greater depth. JSON output lets agents provide the
same information without duplicating Ticker's market-data logic.

### Watchlist interaction

- Keep one watchlist for now; preserve a path to named lists later.
- Add with `a` or `ticker add SYMBOL`; remove the selected row with `d` or
  `ticker remove SYMBOL`.
- Cycle `basic → extended → study` with `v`. Basic shows price and today's
  move; extended adds 1M, 6M, YTD, 1Y, and 5Y trends and returns; study
  adds available fundamentals.
- Use Left/Right to highlight a data column without changing row order. Press
  `s` to apply that column's sort or reverse its current direction.
- Start without row or column focus; show focus only after arrow-key navigation.
- Start in newest-added natural order with no applied sort. Remember the last
  explicitly applied sort column and direction between launches.

### Application header

Keep the application header hidden until it has a clear, non-redundant job.
Tabs begin at the top of the app; do not reserve space for speculative profile
or market-status content.

### Application shell

- Divide the app into header, tabs, body, community sidebar, input bar, and
  contextual footer.
- Provide `Watchlist` and `Community` tabs; remember the active tab.
- Show when each symbol was first added and allow sorting by that timestamp;
  querying an existing symbol must not reset it. Insert newly added symbols at
  the top of the natural, unsorted watchlist order. Keep Added as the final
  column in every view.
- Keep community asynchronous: show member navigation without presence state.
- Seed Community with famous investors and their largest disclosed manager
  holdings. Always show the manager and reporting period; never imply that a
  historical institutional disclosure is a personal or current portfolio.
- Press Enter on a community member to open their disclosure as a read-only
  Portfolio using the same live market table as the Watchlist. Include sortable
  `% OF PORTFOLIO` and `REPORTED` columns; use Esc to return to the directory.
- Lead the community portfolio title with bold, theme-colored `Portfolio`;
  render the manager, reporting period, and historical-disclosure label as
  muted supporting context.
- Collapse the sidebar below 100 columns before removing watchlist content.
- Open feedback with `/`; do not let input capture normal navigation until
  explicitly opened. All application commands remain direct footer shortcuts.
- Render footer keys in the theme accent and bold weight, with their action
  names muted beside them.
- Use semantic color roles for the background, active tab, panel title, row or
  column focus, applied sort, financial change, and selected member.
- Apply the same subtle background treatment to row and column focus. Replace
  the active curses color pair rather than OR-ing two pairs, which can produce
  an unintended blacked-out column.
- Keep the watchlist open and column focus subtle; do not use separator glyphs
  or reverse-video column bands. Render the table header separately with a
  stronger, theme-colored weight than body rows. Keep panel borders unlabeled when the active
  tab already makes their purpose obvious. Keep the footer limited to available
  commands, with current view and sort state communicated in the table itself.
  Show no sort marker until the user explicitly applies a sort.

### Boundaries

Ticker is not a trading terminal, brokerage, alert feed, or source of
investment advice. It should help users notice and understand market changes;
it should not manufacture urgency or attempt to replace deliberate research.

### Known styling issue

- Initializing the black-on-`COLOR_CYAN` curses pair formerly used for
  community-member selection polluted the initial highlight/background in
  some terminals, even though no member was selected. The dedicated pair was
  removed; community selection now reuses the subtle row-selection treatment.

## Product behavior

- Query one or more symbols: `ticker AAPL MSFT`
- Remember successful queries and show the saved watchlist with `ticker`
- Restore the last portfolio view and column sort
- Highlight one portfolio row in interactive terminals with keyboard navigation
- Show price, daily change, market state, and quote age
- Add separate close and extended-hours columns outside the regular session
- Render colored `1d`, `1w`, `1m`, `3m`, and `ytd` trend sparklines
- Provide stable agent output with `--json`
- Fetch market data on every invocation so the displayed quote is current

Yahoo Finance's unofficial chart endpoint is the initial no-key provider. The
provider is isolated so it can be replaced. Symbols and display
preferences—not prices—are stored locally. Quotes are informational and the
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

The portfolio implements basic, extended, and study density. The single-symbol
Glance, Snapshot, and Study analysis views below remain planned.

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
