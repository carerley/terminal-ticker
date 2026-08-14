# Ticker CLI design

`ticker` gives terminal-first users a quick, indicative stock quote without an
account, API key, or runtime dependency.

## MVP

- Query one or more symbols: `ticker AAPL MSFT`
- Show price, daily change, market state, and quote age
- Provide stable agent output with `--json`
- Cache successful quotes for 30 seconds and clearly label cached/stale data
- Fall back to stale cached data when the network or provider is unavailable

Yahoo Finance's unofficial chart endpoint is the initial no-key provider. The
provider is isolated so it can be replaced. Quotes are informational and the
tool does not promise exchange-grade real-time data.

The CLI is the product API. Codex and Claude skills call `ticker --json` rather
than duplicating data-fetching logic.
